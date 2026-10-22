"""Response models for documents."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    size_bytes: int
    pages: int
    chunks: int
    words: int
    status: str
    error: str | None = None
    created_at: datetime
