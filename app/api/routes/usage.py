from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.db.database import get_db
from app.db.models import Document, UsageLog, User
from app.schemas.chat import DailyUsage, RecentRequest, UsageSummary

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("", response_model=UsageSummary)
def usage_summary(user: User = Depends(current_user), db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=30)
    rows = list(
        db.scalars(
            select(UsageLog)
            .where(UsageLog.owner_id == user.id, UsageLog.created_at >= since)
            .order_by(UsageLog.id.desc())
        )
    )

    latencies = sorted(r.latency_ms for r in rows)
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0
    input_tokens = sum(r.input_tokens for r in rows)
    output_tokens = sum(r.output_tokens for r in rows)

    days = [(now - timedelta(days=offset)).date() for offset in range(6, -1, -1)]
    daily = {d.isoformat(): {"requests": 0, "tokens": 0, "cost": 0.0} for d in days}
    for r in rows:
        bucket = daily.get(r.created_at.date().isoformat())
        if bucket:
            bucket["requests"] += 1
            bucket["tokens"] += r.input_tokens + r.output_tokens
            bucket["cost"] += r.cost_usd

    doc_count, chunk_count = db.execute(
        select(func.count(Document.id), func.coalesce(func.sum(Document.chunks), 0)).where(
            Document.owner_id == user.id, Document.status == "ready"
        )
    ).one()

    return UsageSummary(
        requests=len(rows),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=round(sum(r.cost_usd for r in rows), 6),
        avg_latency_ms=int(sum(latencies) / len(latencies)) if latencies else 0,
        p95_latency_ms=p95,
        documents=doc_count,
        chunks=int(chunk_count),
        daily=[
            DailyUsage(date=k, requests=v["requests"], tokens=v["tokens"], cost_usd=round(v["cost"], 6))
            for k, v in daily.items()
        ],
        recent=[
            RecentRequest(
                created_at=r.created_at,
                endpoint=r.endpoint,
                tokens=r.input_tokens + r.output_tokens,
                cost_usd=r.cost_usd,
                latency_ms=r.latency_ms,
            )
            for r in rows[:10]
        ],
    )
