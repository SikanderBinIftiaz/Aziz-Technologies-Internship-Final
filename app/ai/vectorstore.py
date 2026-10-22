"""ChromaDB wrapper. Every query is filtered by owner_id so users can never see each other's data."""
import logging
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.ai.embeddings import get_embedder
from app.config import get_settings

log = logging.getLogger(__name__)


def _where(owner_id: int, document_ids: list[int] | None = None) -> dict:
    filters: list[dict] = [{"owner_id": owner_id}]
    if document_ids:
        filters.append({"document_id": {"$in": list(document_ids)}})
    return filters[0] if len(filters) == 1 else {"$and": filters}


class VectorStore:
    def __init__(self, path: str, embedder_name: str):
        self._client = chromadb.PersistentClient(
            path=path, settings=ChromaSettings(anonymized_telemetry=False)
        )
        # The collection name includes the embedder so vectors of different dimensions never mix.
        self._collection = self._client.get_or_create_collection(
            name=f"documind_chunks_{embedder_name}", metadata={"hnsw:space": "cosine"}
        )

    def count(self) -> int:
        return self._collection.count()

    def add_chunks(
        self, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]
    ) -> None:
        self._collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def query(
        self, embedding: list[float], k: int, owner_id: int, document_ids: list[int] | None = None
    ) -> list[dict]:
        if self._collection.count() == 0:
            return []
        result = self._collection.query(
            query_embeddings=[embedding],
            n_results=k,
            where=_where(owner_id, document_ids),
            include=["documents", "metadatas", "distances"],
        )
        hits = []
        for chunk_id, text, meta, dist in zip(
            result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            hits.append({"id": chunk_id, "text": text, "metadata": meta, "score": max(0.0, 1.0 - float(dist))})
        return hits

    def get_document_chunks(self, owner_id: int, document_id: int) -> list[dict]:
        result = self._collection.get(where=_where(owner_id, [document_id]), include=["documents", "metadatas"])
        chunks = [
            {"id": cid, "text": text, "metadata": meta}
            for cid, text, meta in zip(result["ids"], result["documents"], result["metadatas"])
        ]
        chunks.sort(key=lambda c: c["metadata"].get("chunk_index", 0))
        return chunks

    def delete_document(self, document_id: int) -> None:
        self._collection.delete(where={"document_id": document_id})


@lru_cache
def get_vectorstore() -> VectorStore:
    settings = get_settings()
    return VectorStore(settings.chroma_path, get_embedder().name)
