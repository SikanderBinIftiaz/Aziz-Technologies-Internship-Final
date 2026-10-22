"""Password hashing and JWT helpers."""
import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings
from app.core.errors import AppError


def _prehash(password: str) -> bytes:
    # bcrypt only reads the first 72 bytes; hashing first makes every character count
    # and avoids length errors on long passphrases.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def try_decode_subject(token: str) -> int | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"])
    except (JWTError, KeyError, ValueError, TypeError):
        return None


def decode_access_token(token: str) -> int:
    user_id = try_decode_subject(token)
    if user_id is None:
        raise AppError("INVALID_TOKEN", "Your session is invalid or has expired. Please sign in again.", 401)
    return user_id
