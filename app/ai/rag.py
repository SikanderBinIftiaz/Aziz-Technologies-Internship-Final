"""Retrieval, source tracking, citation validation and confidence scoring."""
import logging
import re
from dataclasses import dataclass
from functools import lru_cache

from app.ai.embeddings import get_embedder, tokenize
from app.ai.vectorstore import VectorStore, get_vectorstore
from app.config import get_settings

log = logging.getLogger(__name__)


@dataclass
class Hit:
    id: str
    text: str
    document_id: int
    filename: str
    page: int
    chunk_index: int
    score: float


@dataclass
class Source:
    number: int
    document_id: int
    filename: str
    page: int
    chunk_index: int
    text: str
    score: float


class Retriever:
    def __init__(self, embedder, store: VectorStore, top_k: int, threshold: float | None = None):
        self.embedder = embedder
        self.store = store
        self.top_k = top_k
        self.threshold = threshold if threshold is not None else embedder.default_threshold

    def search(
        self, owner_id: int, query: str, document_ids: list[int] | None = None, k: int | None = None
    ) -> list[Hit]:
        query = (query or "").strip()
        if not query:
            return []
        embedding = self.embedder.embed([query])[0]
        raw = self.store.query(embedding, k or self.top_k, owner_id, document_ids)
        hits = []
        for item in raw:
            if item["score"] < self.threshold:
                continue
            meta = item["metadata"]
            hits.append(
                Hit(
                    id=item["id"],
                    text=item["text"],
                    document_id=int(meta["document_id"]),
                    filename=str(meta["filename"]),
                    page=int(meta["page"]),
                    chunk_index=int(meta["chunk_index"]),
                    score=item["score"],
                )
            )
        return hits


@lru_cache
def get_retriever() -> Retriever:
    settings = get_settings()
    return Retriever(get_embedder(), get_vectorstore(), settings.top_k, settings.similarity_threshold)


class SourceRegistry:
    """Assigns stable passage numbers during one agent run, even across several searches."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[int, int], Source] = {}
        self._by_number: dict[int, Source] = {}

    def register(self, hit: Hit) -> Source:
        key = (hit.document_id, hit.chunk_index)
        existing = self._by_key.get(key)
        if existing:
            existing.score = max(existing.score, hit.score)
            return existing
        source = Source(
            number=len(self._by_key) + 1,
            document_id=hit.document_id,
            filename=hit.filename,
            page=hit.page,
            chunk_index=hit.chunk_index,
            text=hit.text,
            score=hit.score,
        )
        self._by_key[key] = source
        self._by_number[source.number] = source
        return source

    def get(self, number: int) -> Source | None:
        return self._by_number.get(number)

    def __len__(self) -> int:
        return len(self._by_key)


def format_for_model(sources: list[Source]) -> str:
    """Render passages as delimited, clearly-labelled untrusted data."""
    blocks = []
    for s in sources:
        safe_text = s.text.replace("</document", "< /document")
        blocks.append(f'<document index="{s.number}" file="{s.filename}" page="{s.page}">\n{safe_text}\n</document>')
    return "\n\n".join(blocks)


_CITE_GROUP = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")


def validate_citations(text: str, registry: SourceRegistry) -> tuple[str, list[Source]]:
    """Keep only citations that point at real retrieved passages, then renumber them 1..n by first use."""
    order: dict[int, int] = {}
    cited: list[Source] = []
    dropped = False

    def replace(match: re.Match) -> str:
        nonlocal dropped
        out = []
        for raw in re.split(r"\s*,\s*", match.group(1)):
            number = int(raw)
            source = registry.get(number)
            if source is None:
                dropped = True
                log.warning("Dropped invalid citation [%s]", number)
                continue
            if number not in order:
                order[number] = len(order) + 1
                cited.append(source)
            out.append(f"[{order[number]}]")
        return "".join(out)

    clean = _CITE_GROUP.sub(replace, text)
    if dropped:
        clean = re.sub(r"[ \t]+([.,;:!?])", r"\1", clean)
    return clean.strip(), cited


def confidence_level(found: bool, cited: list[Source], threshold: float) -> str:
    if not found:
        return "none"
    if not cited:
        return "medium"
    best = max(s.score for s in cited)
    if best >= threshold * 2:
        return "high"
    if best >= threshold * 1.3:
        return "medium"
    return "low"


def citation_dict(source: Source) -> dict:
    # Chunks are at most ~800 characters, so this normally returns the whole passage the answer was built from.
    snippet = source.text if len(source.text) <= 1200 else source.text[:1200].rstrip() + "…"
    return {
        "number": source.number,
        "document_id": source.document_id,
        "filename": source.filename,
        "page": source.page,
        "snippet": snippet,
        "score": round(source.score, 3),
    }


def best_excerpt(text: str, query: str, max_chars: int = 320) -> str:
    """Pick the sentence that best matches the query (used by demo mode, which has no generator)."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]
    if not sentences:
        return text[:max_chars]
    query_terms = set(tokenize(query))
    scores = [len(query_terms & set(tokenize(s))) for s in sentences]
    best = max(range(len(sentences)), key=lambda i: (scores[i], -i))
    excerpt = sentences[best]
    if best + 1 < len(sentences) and len(excerpt) < max_chars // 2:
        excerpt = f"{excerpt} {sentences[best + 1]}"
    return excerpt[:max_chars]
