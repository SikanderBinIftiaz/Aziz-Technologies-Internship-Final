"""Embedding backends.

* LocalEmbedder   - sentence-transformers (recommended; best retrieval quality)
* HashingEmbedder - dependency-free feature hashing; used as an offline fallback and by the test suite
"""
import hashlib
import logging
import math
import re
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "by", "from",
    "is", "are", "was", "were", "be", "been", "being", "it", "its", "this", "that", "these", "those",
    "as", "do", "does", "did", "how", "what", "which", "who", "whom", "when", "where", "why", "can",
    "could", "should", "would", "will", "shall", "may", "might", "i", "you", "we", "they", "he", "she",
    "my", "your", "our", "their", "me", "us", "them", "if", "than", "then", "so", "such", "not", "no",
    "about", "into", "over", "per", "any", "all", "have", "has", "had", "there", "here",
}


def tokenize(text: str) -> list[str]:
    """Lowercase, drop stopwords, apply very light stemming. Shared with the demo-mode excerpt picker."""
    tokens = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOPWORDS:
            continue
        if len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 5 and tok.endswith("ing"):
            tok = tok[:-3]
        elif len(tok) > 4 and tok.endswith("ed"):
            tok = tok[:-2]
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        tokens.append(tok)
    return tokens


class HashingEmbedder:
    name = "hash"
    dim = 1024
    default_threshold = 0.15

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        counts: dict[str, float] = {}
        for tok in tokens:
            counts[tok] = counts.get(tok, 0.0) + 1.0
        for left, right in zip(tokens, tokens[1:]):
            key = f"{left}_{right}"
            counts[key] = counts.get(key, 0.0) + 0.5
        for key, count in counts.items():
            digest = hashlib.md5(key.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[idx] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class LocalEmbedder:
    name = "local"
    default_threshold = 0.30

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]


@lru_cache
def get_embedder():
    settings = get_settings()
    backend = settings.embedding_backend.lower()
    if backend == "hash":
        log.info("Embedding backend: hashing (offline)")
        return HashingEmbedder()
    try:
        embedder = LocalEmbedder(settings.embedding_model)
        log.info("Embedding backend: %s", settings.embedding_model)
        return embedder
    except Exception as exc:
        if backend == "local":
            raise
        log.warning("sentence-transformers unavailable (%s). Falling back to hashing embeddings.", exc)
        return HashingEmbedder()
