from fastapi import APIRouter
from sqlalchemy import text

from app.ai.embeddings import get_embedder
from app.ai.llm import get_llm
from app.ai.vectorstore import get_vectorstore
from app.db.database import SessionLocal

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health():
    checks = {"database": "ok", "vector_store": "ok"}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "error"
    vectors = 0
    try:
        vectors = get_vectorstore().count()
    except Exception:
        checks["vector_store"] = "error"
    llm = get_llm()
    return {
        "status": "ok" if all(v == "ok" for v in checks.values()) else "degraded",
        "checks": checks,
        "mode": "claude" if llm.available else "demo",
        "model": llm.model if llm.available else None,
        "embedding_backend": get_embedder().name,
        "indexed_chunks": vectors,
    }
