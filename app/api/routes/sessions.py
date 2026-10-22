import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.errors import AppError
from app.db.database import get_db
from app.db.models import ChatSession, Message, User
from app.schemas.chat import FeedbackRequest, MessageOut, SessionOut

router = APIRouter(prefix="/api", tags=["sessions"])


def _owned_session(db: Session, user: User, session_id: int) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if session is None or session.owner_id != user.id:
        raise AppError("SESSION_NOT_FOUND", "Chat session not found.", 404)
    return session


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(ChatSession).where(ChatSession.owner_id == user.id).order_by(ChatSession.id.desc()).limit(100)
    return list(db.scalars(query))


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
def session_messages(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = _owned_session(db, user, session_id)
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            citations=json.loads(m.citations_json or "[]"),
            tools_used=json.loads(m.tools_json or "[]"),
            confidence=m.confidence,
            found=None if m.found is None else bool(m.found),
            feedback=m.feedback,
            created_at=m.created_at,
        )
        for m in session.messages
    ]


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    session = _owned_session(db, user, session_id)
    db.delete(session)
    db.commit()


@router.post("/messages/{message_id}/feedback", status_code=204)
def message_feedback(
    message_id: int,
    payload: FeedbackRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    message = db.get(Message, message_id)
    if message is None or message.role != "assistant" or message.session.owner_id != user.id:
        raise AppError("MESSAGE_NOT_FOUND", "Message not found.", 404)
    message.feedback = payload.value
    db.commit()
