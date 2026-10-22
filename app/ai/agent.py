"""The DocuMind agent: a bounded tool-calling loop that answers from the user's documents.

run() is a generator of events so the same code serves the JSON endpoint and the streaming endpoint:
  {"type": "status", "text": ...}   progress message for the UI
  {"type": "token",  "text": ...}   a piece of the answer
  {"type": "reset"}                 discard tokens streamed so far (the model turned out to call a tool)
  {"type": "final",  ...}           validated answer, citations, confidence, usage
"""
import logging
import re
import time
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import LLMClient, get_llm
from app.ai.prompts import (
    AGENT_SYSTEM_PROMPT,
    NO_DOCUMENTS_MESSAGE,
    NOT_FOUND_MESSAGE,
    NOT_FOUND_PREFIX,
)
from app.ai.rag import (
    Retriever,
    SourceRegistry,
    best_excerpt,
    citation_dict,
    confidence_level,
    get_retriever,
    validate_citations,
)
from app.ai.tools import TOOL_SCHEMAS, ToolContext, UsageCounter, execute_tool, format_number, safe_eval
from app.config import Settings, get_settings
from app.db.models import Document

log = logging.getLogger(__name__)

_TOOL_STATUS = {
    "search_documents": "Searching your documents…",
    "calculate": "Calculating…",
    "document_stats": "Reading document details…",
    "summarize_document": "Summarizing the document…",
}
_NON_SEARCH_TOOLS = {"calculate", "document_stats", "summarize_document"}
_ARITHMETIC = re.compile(r"[\d\s.+\-*/()%^]+")
_LEAD_IN = re.compile(r"^(?:what(?:'s|\s+is)|calculate|compute|how\s+much\s+is|work\s+out|solve|evaluate)\s+", re.I)
_PERCENT_OF = re.compile(r"^(\d+(?:\.\d+)?)\s*%\s*of\s+(\d+(?:\.\d+)?)$", re.I)


def _arithmetic_question(question: str) -> str | None:
    """Return an evaluable expression if the whole question is arithmetic, e.g. '12*(3+4)' or 'What is 15% of 2,400?'."""
    candidate = question.strip().rstrip("?=.!").strip()
    candidate = _LEAD_IN.sub("", candidate)
    candidate = re.sub(r"(?<=\d),(?=\d{3}\b)", "", candidate)  # 2,400 -> 2400
    percent = _PERCENT_OF.match(candidate)
    if percent:
        return f"{percent.group(1)}/100*{percent.group(2)}"
    if _ARITHMETIC.fullmatch(candidate) and re.search(r"\d", candidate) and re.search(r"[+\-*/^%]", candidate):
        return candidate
    return None


class DocuMindAgent:
    def __init__(self, llm: LLMClient, retriever: Retriever, settings: Settings | None = None):
        self.llm = llm
        self.retriever = retriever
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ public
    def run(
        self,
        db: Session,
        user_id: int,
        question: str,
        history: list[dict],
        document_ids: list[int] | None = None,
    ) -> Iterator[dict]:
        started = time.perf_counter()
        registry = SourceRegistry()
        usage = UsageCounter()
        tools_used: list[str] = []

        ready = self._ready_documents(db, user_id, document_ids)
        if not ready:
            yield {"type": "token", "text": NO_DOCUMENTS_MESSAGE}
            yield self._finish(NO_DOCUMENTS_MESSAGE, registry, usage, tools_used, started, mode=self._mode())
            return

        ctx = ToolContext(
            db=db,
            user_id=user_id,
            retriever=self.retriever,
            llm=self.llm,
            registry=registry,
            usage=usage,
            settings=self.settings,
            scope_ids=document_ids or None,
        )

        if not self.llm.available:
            yield from self._run_demo(question, ctx, tools_used, started)
            return

        system = AGENT_SYSTEM_PROMPT.format(documents=self._document_list(ready))
        messages: list[dict] = [*history, {"role": "user", "content": question}]
        max_steps = self.settings.agent_max_steps
        final_text = ""

        for step in range(max_steps + 1):
            answer_only = step == max_steps  # the last step has no tools, so the model must answer
            emitted = False
            result = None
            stream = self.llm.stream_message(
                system,
                messages,
                tools=None if answer_only else TOOL_SCHEMAS,
                tool_choice={"type": "any"} if step == 0 else None,
            )
            for kind, payload in stream:
                if kind == "text":
                    emitted = True
                    yield {"type": "token", "text": payload}
                else:
                    result = payload
            usage.add(result.input_tokens, result.output_tokens)

            if result.tool_calls and not answer_only:
                if emitted:
                    yield {"type": "reset"}
                messages.append({"role": "assistant", "content": result.content})
                tool_results = []
                for call in result.tool_calls:
                    yield {"type": "status", "text": _TOOL_STATUS.get(call.name, "Working…")}
                    output = execute_tool(call.name, call.input, ctx)
                    tools_used.append(call.name)
                    log.info("tool=%s ok=%s", call.name, not output.startswith("ERROR"))
                    tool_results.append({"type": "tool_result", "tool_use_id": call.id, "content": output})
                messages.append({"role": "user", "content": tool_results})
                continue

            final_text = result.text
            if result.stop_reason == "max_tokens":
                log.warning("Answer was cut off by max_tokens")
            break

        if not final_text.strip():
            final_text = NOT_FOUND_MESSAGE
            yield {"type": "reset"}
            yield {"type": "token", "text": final_text}
        yield self._finish(final_text, registry, usage, tools_used, started, mode="claude")

    # ------------------------------------------------------------------ helpers
    def _mode(self) -> str:
        return "claude" if self.llm.available else "demo"

    def _ready_documents(self, db: Session, user_id: int, document_ids: list[int] | None) -> list[Document]:
        query = select(Document).where(Document.owner_id == user_id, Document.status == "ready")
        if document_ids:
            query = query.where(Document.id.in_(document_ids))
        return list(db.scalars(query.order_by(Document.id).limit(30)))

    @staticmethod
    def _document_list(docs: list[Document]) -> str:
        return "\n".join(f"- id {d.id}: {d.filename} ({d.pages} pages/sections)" for d in docs)

    def _run_demo(self, question: str, ctx: ToolContext, tools_used: list[str], started: float) -> Iterator[dict]:
        """Offline mode: no generator model, so answer with the best matching excerpts (and exact arithmetic)."""
        yield {"type": "status", "text": "Searching your documents…"}
        expression = _arithmetic_question(question)
        if expression:
            try:
                text = f"{expression} = {format_number(safe_eval(expression))}"
            except (ValueError, ZeroDivisionError, OverflowError):
                text = "I could not evaluate that expression."
            tools_used.append("calculate")
            yield {"type": "token", "text": text}
            yield self._finish(text, ctx.registry, ctx.usage, tools_used, started, mode="demo")
            return

        tools_used.append("search_documents")
        hits = ctx.retriever.search(ctx.user_id, question, ctx.scope_ids)
        if not hits:
            yield {"type": "token", "text": NOT_FOUND_MESSAGE}
            yield self._finish(NOT_FOUND_MESSAGE, ctx.registry, ctx.usage, tools_used, started, mode="demo")
            return

        lines = ["Demo mode (no AI model configured). These are the most relevant passages from your documents:", ""]
        for hit in hits[:3]:
            source = ctx.registry.register(hit)
            lines.append(f"- {best_excerpt(hit.text, question)} [{source.number}]")
        text = "\n".join(lines)
        yield {"type": "token", "text": text}
        yield self._finish(text, ctx.registry, ctx.usage, tools_used, started, mode="demo")

    def _finish(
        self,
        raw_text: str,
        registry: SourceRegistry,
        usage: UsageCounter,
        tools_used: list[str],
        started: float,
        mode: str,
    ) -> dict:
        text, cited = validate_citations(raw_text.strip(), registry)
        not_found = text.lower().startswith(NOT_FOUND_PREFIX) or text == NO_DOCUMENTS_MESSAGE
        used_non_search = any(t in _NON_SEARCH_TOOLS for t in tools_used)
        found = (not not_found) and (bool(cited) or used_non_search)
        if not found:
            cited = []
        threshold = self.retriever.threshold
        return {
            "type": "final",
            "answer": text,
            "citations": [citation_dict(s) for s in cited],
            "confidence": confidence_level(found, cited, threshold),
            "found": found,
            "mode": mode,
            "tools_used": list(dict.fromkeys(tools_used)),
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cost_usd": round(self.settings.estimate_cost(usage.input_tokens, usage.output_tokens), 6),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        }


@lru_cache
def get_agent() -> DocuMindAgent:
    return DocuMindAgent(get_llm(), get_retriever(), get_settings())
