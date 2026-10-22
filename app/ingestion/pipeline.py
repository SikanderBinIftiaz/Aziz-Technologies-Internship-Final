"""Document ingestion: validate, store, parse, chunk, embed and index an uploaded file."""
import hashlib
import logging
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.embeddings import get_embedder
from app.ai.vectorstore import get_vectorstore
from app.config import get_settings
from app.core.errors import AppError
from app.db.models import Document
from app.ingestion.chunker import chunk_pages
from app.ingestion.parsers import SUPPORTED_EXTENSIONS, parse_file

log = logging.getLogger(__name__)
EMBED_BATCH = 64


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "").name
    name = re.sub(r"[^\w .()\-]", "_", name).strip(" .")
    return (name[:150] or "document")


def _fail(db: Session, doc: Document, message: str, status_code: int, code: str) -> AppError:
    doc.status = "failed"
    doc.error = message
    db.commit()
    try:
        get_vectorstore().delete_document(doc.id)
    except Exception:  # cleanup is best-effort
        log.warning("Could not clean vectors for failed document %s", doc.id)
    log.warning("Ingestion failed for document %s (%s): %s", doc.id, doc.filename, message)
    return AppError(code, message, status_code)


def ingest_document(db: Session, user_id: int, filename: str, data: bytes) -> Document:
    settings = get_settings()
    safe_name = sanitize_filename(filename)
    ext = Path(safe_name).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise AppError(
            "UNSUPPORTED_TYPE", f"Unsupported file type '{ext or 'unknown'}'. Allowed: PDF, DOCX, TXT, MD.", 415
        )
    if not data:
        raise AppError("EMPTY_FILE", "The file is empty.", 422)
    if len(data) > settings.max_upload_bytes:
        raise AppError("FILE_TOO_LARGE", f"The file exceeds the {settings.max_upload_mb} MB limit.", 413)

    digest = hashlib.sha256(data).hexdigest()
    existing = db.scalar(select(Document).where(Document.owner_id == user_id, Document.content_hash == digest))
    if existing:
        raise AppError("DUPLICATE_DOCUMENT", f"This file was already uploaded as '{existing.filename}'.", 409)

    doc = Document(owner_id=user_id, filename=safe_name, content_hash=digest, size_bytes=len(data), status="processing")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    target_dir = Path(settings.upload_dir) / str(user_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{doc.id}{ext}"
    path.write_bytes(data)

    try:
        pages = parse_file(path)
        chunks = chunk_pages(pages, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            raise AppError("NO_TEXT", "No readable text was found in this file.", 422)

        embedder = get_embedder()
        texts = [c.text for c in chunks]
        embeddings: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            embeddings.extend(embedder.embed(texts[start : start + EMBED_BATCH]))

        get_vectorstore().add_chunks(
            ids=[f"{doc.id}:{c.index}" for c in chunks],
            embeddings=embeddings,
            documents=texts,
            metadatas=[
                {
                    "owner_id": user_id,
                    "document_id": doc.id,
                    "filename": safe_name,
                    "page": c.page,
                    "chunk_index": c.index,
                }
                for c in chunks
            ],
        )
        doc.pages = len(pages)
        doc.chunks = len(chunks)
        doc.words = sum(len(p.text.split()) for p in pages)
        doc.status = "ready"
        doc.error = None
        db.commit()
        log.info("Indexed document %s (%s): %d pages, %d chunks", doc.id, safe_name, doc.pages, doc.chunks)
        return doc
    except AppError as exc:
        raise _fail(db, doc, exc.message, exc.status_code, exc.code) from exc
    except Exception as exc:
        log.exception("Unexpected ingestion error for document %s", doc.id)
        raise _fail(db, doc, "Indexing failed unexpectedly. Please try again.", 500, "INGESTION_FAILED") from exc


def delete_document(db: Session, doc: Document) -> None:
    get_vectorstore().delete_document(doc.id)
    settings = get_settings()
    for candidate in (Path(settings.upload_dir) / str(doc.owner_id)).glob(f"{doc.id}.*"):
        candidate.unlink(missing_ok=True)
    db.delete(doc)
    db.commit()
