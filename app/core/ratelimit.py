"""Rate limiting keyed by the signed-in user (falls back to client IP)."""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.core.security import try_decode_subject


def rate_key(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        user_id = try_decode_subject(header[7:].strip())
        if user_id is not None:
            return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=rate_key)
