import json
import logging
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.agent import get_agent
from app.api.deps import current_user
from app.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import limiter
from app.db.database import SessionLocal, get_db
from app.db.models import ChatSession, Document, Message, UsageLog, User
from app.schemas.chat import ChatRequest, ChatResponse

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])
settings = get_settings()

_CITE = re.compile(r"\s*\[\d{1,3}(?:\s*,\s*\d{1,3})*\]")


def _prepare(db: Session, user_id: int, payload: ChatRequest) -> tuple[ChatSession | None, list[dict]]:
    """Validate ownership of the session and documents, and load recent history for the model."""
    session = None
    if payload.session_id is not None:
        session = db.get(ChatSession, payload.session_id)
        if session is None or session.owner_id != user_id:
            raise AppError("SESSION_NOT_FOUND", "Chat session not found.", 404)

    if payload.document_ids:
        wanted = set(payload.document_ids)
        owned = set(
            db.scalars(select(Document.id).where(Document.owner_id == user_id, Document.id.in_(wanted)))
        )
        if owned != wanted:
            raise AppError("DOCUMENT_NOT_FOUND", "One or more selected documents were not found.", 404)

    return session, _load_history(db, session)


def _load_history(db: Session, session: ChatSession | None) -> list[dict]:
    if session is None:
        return []
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.session_id == session.id)
            .order_by(Message.id.desc())
            .limit(settings.history_messages)
        )
    )[::-1]
    history: list[dict] = []
    for row in rows:
        # Old [n] markers refer to passages from earlier turns, so they are removed to avoid confusing the model.
        content = _CITE.sub("", row.content) if row.role == "assistant" else row.content
        if history and history[-1]["role"] == row.role:
            continue
        if not history and row.role != "user":
            continue
        history.append({"role": row.role, "content": content})
    if history and history[-1]["role"] == "user":
        history.pop()
    return history


def _persist(
    db: Session, user_id: int, session: ChatSession | None, payload: ChatRequest, final: dict, endpoint: str
) -> ChatResponse:
    if session is None:
        question = payload.question
        title = question if len(question) <= 60 else question[:59].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
        session = ChatSession(owner_id=user_id, title=title)
        db.add(session)
        db.flush()
    db.add(Message(session_id=session.id, role="user", content=payload.question))
    assistant = Message(
        session_id=session.id,
        role="assistant",
        content=final["answer"],
        citations_json=json.dumps(final["citations"], ensure_ascii=False),
        tools_json=json.dumps(final["tools_used"]),
        confidence=final["confidence"],
        found=int(final["found"]),
    )
    db.add(assistant)
    usage = final["usage"]
    db.add(
        UsageLog(
            owner_id=user_id,
            endpoint=endpoint,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cost_usd=usage["cost_usd"],
            latency_ms=usage["latency_ms"],
        )
    )
    db.commit()
    log.info(
        "chat answered found=%s confidence=%s tools=%s tokens_in=%d tokens_out=%d latency_ms=%d",
        final["found"], final["confidence"], final["tools_used"],
        usage["input_tokens"], usage["output_tokens"], usage["latency_ms"],
    )
    return ChatResponse(
        session_id=session.id,
        message_id=assistant.id,
        answer=final["answer"],
        citations=final["citations"],
        confidence=final["confidence"],
        found=final["found"],
        mode=final["mode"],
        tools_used=final["tools_used"],
        usage=usage,
    )


@router.post("", response_model=ChatResponse)
@limiter.limit(settings.rate_limit_chat)
def chat(
    request: Request,
    payload: ChatRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    session, history = _prepare(db, user.id, payload)
    final = None
    for event in get_agent().run(db, user.id, payload.question, history, payload.document_ids):
        if event["type"] == "final":
            final = event
    if final is None:
        raise AppError("NO_ANSWER", "The assistant did not produce an answer.", 502)
    return _persist(db, user.id, session, payload, final, "chat")


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/stream")
@limiter.limit(settings.rate_limit_chat)
def chat_stream(request: Request, payload: ChatRequest, user: User = Depends(current_user)):
    user_id = user.id

    def events():
        # The stream outlives the request-scoped dependency, so it owns its own database session.
        db = SessionLocal()
        try:
            session, history = _prepare(db, user_id, payload)
            final = None
            for event in get_agent().run(db, user_id, payload.question, history, payload.document_ids):
                if event["type"] == "final":
                    final = event
                else:
                    yield _sse(event)
            if final is None:
                raise AppError("NO_ANSWER", "The assistant did not produce an answer.", 502)
            response = _persist(db, user_id, session, payload, final, "chat/stream")
            yield _sse({"type": "final", **response.model_dump(mode="json")})
        except AppError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
        except Exception:
            log.exception("Streaming chat failed")
            yield _sse({"type": "error", "code": "INTERNAL_ERROR", "message": "Something went wrong. Please try again."})
        finally:
            db.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
