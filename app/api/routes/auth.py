from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.db.database import get_db
from app.db.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()

# Used so a login for an unknown email takes as long as one for a real account.
_DUMMY_HASH = hash_password("not-a-real-password")


@router.post("/register", response_model=TokenResponse, status_code=201)
@limiter.limit(settings.rate_limit_auth)
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email)):
        raise AppError("EMAIL_TAKEN", "An account with this email already exists.", 409)
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.rate_limit_auth)
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email))
    valid = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid:
        raise AppError("INVALID_CREDENTIALS", "Incorrect email or password.", 401)
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user
