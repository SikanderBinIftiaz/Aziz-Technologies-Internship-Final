"""Evaluation harness for DocuMind AI.

Indexes the sample documents into an isolated database, runs every question in data/eval/eval_set.json
through the real agent pipeline, and reports:

  * retrieval hit rate and MRR      - was the right passage in the top-k?
  * citation accuracy               - did the answer cite the right file and passage?
  * answer keyword rate             - does the answer contain the expected facts?
  * grounding                       - are the answer's words supported by its cited passages?
                                      (LLM judge with --judge, otherwise a lexical check)
  * refusal rate                    - are unanswerable questions refused instead of answered?
  * latency, tokens and cost

Usage:
    python evaluation/run_eval.py                 # isolated run, uses whatever .env configures
    python evaluation/run_eval.py --judge         # also ask the LLM to fact-check each answer
    python evaluation/run_eval.py --use-app-config  # use the app's real database/vector store
"""
import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGETS = {
    "grounded_rate": 0.90,
    "retrieval_hit_rate": 0.85,
    "refusal_rate": 0.90,
    "median_latency_ms": 6000,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate DocuMind AI")
    parser.add_argument("--eval-set", default=str(ROOT / "data" / "eval" / "eval_set.json"))
    parser.add_argument("--samples-dir", default=str(ROOT / "data" / "samples"))
    parser.add_argument("--output-dir", default=str(ROOT / "evaluation" / "reports"))
    parser.add_argument("--judge", action="store_true", help="use the LLM as a fact-checking judge (needs an API key)")
    parser.add_argument("--use-app-config", action="store_true", help="do not isolate storage in a temp directory")
    return parser.parse_args()


def isolate_storage() -> str:
    tmp = tempfile.mkdtemp(prefix="documind-eval-")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/eval.db"
    os.environ["CHROMA_PATH"] = f"{tmp}/chroma"
    os.environ["UPLOAD_DIR"] = f"{tmp}/uploads"
    return tmp


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[int(pct * (len(ordered) - 1))]


def lexical_grounding(answer: str, sources: list[dict], tokenize) -> float:
    """Fraction of the answer's content words that appear in the cited passages."""
    answer_terms = set(tokenize(re.sub(r"\[\d+\]", " ", answer)))
    if not answer_terms:
        return 0.0
    source_terms = set(tokenize(" ".join(s["snippet"] for s in sources)))
    return len(answer_terms & source_terms) / len(answer_terms)


def llm_judge(llm, answer: str, sources: list[dict]) -> bool | None:
    from app.ai.prompts import JUDGE_PROMPT

    context = "\n\n".join(f"[{s['number']}] {s['snippet']}" for s in sources) or "(no context)"
    result = llm.complete(JUDGE_PROMPT, [{"role": "user", "content": f"CONTEXT:\n{context}\n\nANSWER:\n{answer}"}], max_tokens=150)
    match = re.search(r"\{.*\}", result.text, re.DOTALL)
    if not match:
        return None
    try:
        return bool(json.loads(match.group(0)).get("supported"))
    except (json.JSONDecodeError, AttributeError):
        return None


def main() -> None:
    args = parse_args()
    tmp = None if args.use_app_config else isolate_storage()

    from app.ai.agent import get_agent
    from app.ai.embeddings import get_embedder, tokenize
    from app.ai.llm import get_llm
    from app.ai.rag import get_retriever
    from app.db.database import SessionLocal, init_db
    from app.db.models import User
    from app.ingestion.pipeline import ingest_document

    init_db()
    llm, embedder, retriever, agent = get_llm(), get_embedder(), get_retriever(), get_agent()
    mode = "claude" if llm.available else "demo"
    judge_enabled = args.judge and llm.available
    if args.judge and not llm.available:
        print("! --judge ignored: no ANTHROPIC_API_KEY configured")

    questions = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))["questions"]
    print(f"DocuMind evaluation | mode={mode} | embeddings={embedder.name} | threshold={retriever.threshold} | questions={len(questions)}")

    rows: list[dict] = []
    with SessionLocal() as db:
        user = User(email=f"eval-{int(time.time())}@documind.local", password_hash="-")
        db.add(user)
        db.commit()
        for path in sorted(Path(args.samples_dir).glob("*")):
            if path.is_file():
                doc = ingest_document(db, user.id, path.name, path.read_bytes())
                print(f"  indexed {doc.filename}: {doc.chunks} chunks")

        for q in questions:
            hits = retriever.search(user.id, q["question"])
            final = None
            started = time.perf_counter()
            for event in agent.run(db, user.id, q["question"], [], None):
                if event["type"] == "final":
                    final = event
            wall_ms = int((time.perf_counter() - started) * 1000)

            row = {
                "id": q["id"],
                "type": q["type"],
                "question": q["question"],
                "found": final["found"],
                "answer": final["answer"],
                "confidence": final["confidence"],
                "citations": [f"{c['filename']} p.{c['page']}" for c in final["citations"]],
                "latency_ms": wall_ms,
                "input_tokens": final["usage"]["input_tokens"],
                "output_tokens": final["usage"]["output_tokens"],
                "cost_usd": final["usage"]["cost_usd"],
            }
            keywords = [k.lower() for k in q.get("keywords", [])]
            if q["type"] == "answerable":
                expected = q["expected_file"]
                rank = next(
                    (i for i, h in enumerate(hits, 1) if h.filename == expected and all(k in h.text.lower() for k in keywords)),
                    None,
                )
                cited_texts = " ".join(c["snippet"].lower() for c in final["citations"] if c["filename"] == expected)
                row.update(
                    retrieval_rank=rank,
                    retrieval_hit=rank is not None,
                    citation_correct=final["found"] and bool(cited_texts) and all(k in cited_texts for k in keywords),
                    answer_has_keywords=all(k in final["answer"].lower() for k in keywords),
                )
                if final["found"]:
                    row["grounding"] = round(lexical_grounding(final["answer"], final["citations"], tokenize), 3)
                    row["grounded"] = row["grounding"] >= 0.6
                    if judge_enabled:
                        verdict = llm_judge(llm, final["answer"], final["citations"])
                        if verdict is not None:
                            row["judge_supported"] = verdict
                            row["grounded"] = verdict
                else:
                    row["grounded"] = False
            else:
                row["refused"] = not final["found"]
            rows.append(row)
            mark = "ok " if (row.get("retrieval_hit") if q["type"] == "answerable" else row["refused"]) else "MISS"
            print(f"  [{mark}] #{q['id']:>2} {q['question'][:70]}")

    answerable = [r for r in rows if r["type"] == "answerable"]
    unanswerable = [r for r in rows if r["type"] == "unanswerable"]
    latencies = [r["latency_ms"] for r in rows]
    rate = lambda items, key: round(sum(1 for r in items if r.get(key)) / len(items), 3) if items else 0.0

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "llm_model": llm.model if llm.available else None,
        "embedding_backend": embedder.name,
        "similarity_threshold": retriever.threshold,
        "judge": "llm" if judge_enabled else "lexical",
        "questions": len(rows),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "retrieval_hit_rate": rate(answerable, "retrieval_hit"),
        "retrieval_mrr": round(statistics.mean((1 / r["retrieval_rank"]) if r["retrieval_rank"] else 0 for r in answerable), 3) if answerable else 0.0,
        "citation_accuracy": rate(answerable, "citation_correct"),
        "answer_keyword_rate": rate(answerable, "answer_has_keywords"),
        "grounded_rate": rate(answerable, "grounded"),
        "refusal_rate": rate(unanswerable, "refused"),
        "mean_latency_ms": int(statistics.mean(latencies)) if latencies else 0,
        "median_latency_ms": int(statistics.median(latencies)) if latencies else 0,
        "p95_latency_ms": int(percentile(latencies, 0.95)),
        "total_input_tokens": sum(r["input_tokens"] for r in rows),
        "total_output_tokens": sum(r["output_tokens"] for r in rows),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / f"eval_{mode}_{stamp}.json").write_text(json.dumps({"summary": summary, "results": rows}, indent=2), encoding="utf-8")
    report = render_markdown(summary, rows)
    (out_dir / f"eval_{mode}_{stamp}.md").write_text(report, encoding="utf-8")
    (out_dir / f"latest_{mode}.md").write_text(report, encoding="utf-8")

    print("\n" + report)
    print(f"Reports written to {out_dir}")
    if tmp:
        print(f"(isolated storage: {tmp})")


def render_markdown(s: dict, rows: list[dict]) -> str:
    def status(ok: bool) -> str:
        return "PASS" if ok else "BELOW TARGET"

    lines = [
        "# DocuMind AI Evaluation Report",
        "",
        f"Generated: {s['generated_at']}  ",
        f"Mode: **{s['mode']}**" + (f" (model `{s['llm_model']}`)" if s["llm_model"] else " (retrieval only, no generator model)") + "  ",
        f"Embeddings: `{s['embedding_backend']}`, similarity threshold {s['similarity_threshold']}  ",
        f"Grounding check: {s['judge']}  ",
        f"Questions: {s['questions']} ({s['answerable']} answerable, {s['unanswerable']} unanswerable)",
        "",
        "## Results",
        "",
        "| Metric | Result | Target | Status |",
        "|---|---|---|---|",
        f"| Retrieval hit rate (right passage in top-k) | {s['retrieval_hit_rate']:.1%} | >= {TARGETS['retrieval_hit_rate']:.0%} | {status(s['retrieval_hit_rate'] >= TARGETS['retrieval_hit_rate'])} |",
        f"| Retrieval MRR | {s['retrieval_mrr']:.3f} | n/a | n/a |",
        f"| Citation accuracy | {s['citation_accuracy']:.1%} | n/a | n/a |",
        f"| Answer contains expected facts | {s['answer_keyword_rate']:.1%} | n/a | n/a |",
        f"| Grounded answers | {s['grounded_rate']:.1%} | >= {TARGETS['grounded_rate']:.0%} | {status(s['grounded_rate'] >= TARGETS['grounded_rate'])} |",
        f"| Unanswerable questions refused | {s['refusal_rate']:.1%} | >= {TARGETS['refusal_rate']:.0%} | {status(s['refusal_rate'] >= TARGETS['refusal_rate'])} |",
        f"| Median latency | {s['median_latency_ms']} ms | < {TARGETS['median_latency_ms']} ms | {status(s['median_latency_ms'] < TARGETS['median_latency_ms'])} |",
        f"| p95 latency | {s['p95_latency_ms']} ms | n/a | n/a |",
        f"| Tokens (in / out) | {s['total_input_tokens']} / {s['total_output_tokens']} | n/a | n/a |",
        f"| Estimated cost | ${s['total_cost_usd']} | n/a | n/a |",
        "",
    ]
    misses = [
        r for r in rows
        if (r["type"] == "answerable" and not (r.get("retrieval_hit") and r.get("citation_correct")))
        or (r["type"] == "unanswerable" and not r.get("refused"))
    ]
    lines.append("## Questions that need attention")
    lines.append("")
    if not misses:
        lines.append("None. Every question behaved as expected.")
    else:
        lines += ["| # | Type | Question | What happened |", "|---|---|---|---|"]
        for r in misses:
            if r["type"] == "unanswerable":
                note = "answered instead of refusing"
            elif not r.get("retrieval_hit"):
                note = "correct passage not retrieved"
            else:
                note = "retrieved, but answer did not cite the correct passage"
            lines.append(f"| {r['id']} | {r['type']} | {r['question']} | {note} |")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
