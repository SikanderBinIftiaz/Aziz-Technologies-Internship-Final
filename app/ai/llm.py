"""Thin wrapper around the Anthropic Messages API: streaming, tool calls, token accounting, error mapping."""
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache

import anthropic

from app.config import Settings, get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: list[dict] = field(default_factory=list)  # blocks to send back as the assistant turn
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


def _map_error(exc: Exception) -> AppError:
    log.error("LLM call failed: %s: %s", type(exc).__name__, exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return AppError("LLM_AUTH_FAILED", "The server's AI provider credentials are invalid.", 502)
    if isinstance(exc, anthropic.RateLimitError):
        return AppError("LLM_RATE_LIMITED", "The AI provider is rate limiting requests. Please retry shortly.", 503)
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return AppError("LLM_UNAVAILABLE", "Could not reach the AI provider. Please try again.", 503)
    if isinstance(exc, anthropic.APIStatusError):
        return AppError("LLM_ERROR", "The AI provider returned an error. Please try again.", 502)
    return AppError("LLM_ERROR", "The AI request failed. Please try again.", 502)


def _to_result(message) -> LLMResult:
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    content: list[dict] = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
            content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            tool_input = dict(block.input) if block.input else {}
            calls.append(ToolCall(id=block.id, name=block.name, input=tool_input))
            content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": tool_input})
    return LLMResult(
        text="".join(text_parts),
        tool_calls=calls,
        content=content,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        stop_reason=message.stop_reason or "",
    )


class LLMClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = None
        if self.settings.anthropic_api_key:
            self._client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=3,
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str:
        return self.settings.llm_model

    def _kwargs(self, system, messages, tools, tool_choice, max_tokens) -> dict:
        kwargs: dict = {
            "model": self.settings.llm_model,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        return kwargs

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        if not self._client:
            raise AppError("LLM_NOT_CONFIGURED", "No AI provider is configured on the server.", 503)
        try:
            message = self._client.messages.create(**self._kwargs(system, messages, tools, tool_choice, max_tokens))
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        return _to_result(message)

    def stream_message(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[tuple[str, object]]:
        """Yield ("text", delta) for every text chunk, then ("done", LLMResult) once."""
        if not self._client:
            raise AppError("LLM_NOT_CONFIGURED", "No AI provider is configured on the server.", 503)
        try:
            with self._client.messages.stream(
                **self._kwargs(system, messages, tools, tool_choice, max_tokens)
            ) as stream:
                for delta in stream.text_stream:
                    yield ("text", delta)
                final = stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise _map_error(exc) from exc
        yield ("done", _to_result(final))


@lru_cache
def get_llm() -> LLMClient:
    return LLMClient()
