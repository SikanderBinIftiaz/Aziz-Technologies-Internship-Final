from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import limiter
from app.db.database import get_db
from app.db.models import Document, User
from app.ingestion.pipeline import delete_document, ingest_document
from app.schemas.documents import DocumentOut

router = APIRouter(prefix="/api/documents", tags=["documents"])
settings = get_settings()


@router.post("", response_model=DocumentOut, status_code=201)
@limiter.limit(settings.rate_limit_upload)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = file.file.read(settings.max_upload_bytes + 1)  # never read more than the limit allows
    return ingest_document(db, user.id, file.filename or "document", data)


@router.get("", response_model=list[DocumentOut])
def list_documents(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return list(db.scalars(select(Document).where(Document.owner_id == user.id).order_by(Document.id.desc())))


@router.delete("/{document_id}", status_code=204)
def remove_document(document_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if doc is None or doc.owner_id != user.id:
        raise AppError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
    delete_document(db, doc)
