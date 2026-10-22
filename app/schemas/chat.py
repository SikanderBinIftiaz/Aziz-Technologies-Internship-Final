"""Request and response models for chat, sessions and usage."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ChatRequest(BaseModel):
    question: Question
    session_id: int | None = None
    document_ids: list[int] | None = Field(default=None, max_length=50)


class CitationOut(BaseModel):
    number: int
    document_id: int
    filename: str
    page: int
    snippet: str
    score: float


class UsageOut(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int


class ChatResponse(BaseModel):
    session_id: int
    message_id: int
    answer: str
    citations: list[CitationOut]
    confidence: Literal["high", "medium", "low", "none"]
    found: bool
    mode: Literal["claude", "demo"]
    tools_used: list[str]
    usage: UsageOut


class SessionOut(BaseModel):
    id: int
    title: str
    created_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    citations: list[CitationOut]
    tools_used: list[str]
    confidence: str | None
    found: bool | None
    feedback: int | None
    created_at: datetime


class FeedbackRequest(BaseModel):
    value: Literal[-1, 1]


class DailyUsage(BaseModel):
    date: str
    requests: int
    tokens: int
    cost_usd: float


class RecentRequest(BaseModel):
    created_at: datetime
    endpoint: str
    tokens: int
    cost_usd: float
    latency_ms: int


class UsageSummary(BaseModel):
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float
    avg_latency_ms: int
    p95_latency_ms: int
    documents: int
    chunks: int
    daily: list[DailyUsage]
    recent: list[RecentRequest]
