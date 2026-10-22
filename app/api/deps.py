"""Shared FastAPI dependencies."""
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db.database import get_db
from app.db.models import User

_bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise AppError("NOT_AUTHENTICATED", "Please sign in to continue.", 401)
    user = db.get(User, decode_access_token(credentials.credentials))
    if user is None:
        raise AppError("INVALID_TOKEN", "Your session is invalid. Please sign in again.", 401)
    return user
