"""Provider interface. Every backend (OpenAI-compatible, Anthropic, Gemini, local, plugin) implements it.

A provider turns a `GenerationRequest` into a stream of `StreamEvent`s. It must:
- honour cancellation (it runs inside an anyio cancel scope; do not swallow CancelledError),
- report `Usage` when the backend provides it,
- support `response_format` (JSON mode) natively or raise `ProviderError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from tavern.errors import AppError


class ProviderError(AppError):
    """The backend failed or rejected the request. The message is safe to show to the user."""

    status = 502
    code = "provider_error"


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class GenerationRequest:
    messages: list[ChatMessage]
    model: str = ""
    max_tokens: int = 0  # 0 = provider default
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] = field(default_factory=list)
    # Structured output. {"type": "json_object"} or
    # {"type": "json_schema", "name": "...", "schema": {...}}
    response_format: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # provider-specific passthrough


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StreamEvent:
    """kind: "text" (delta in `text`) or "done" (final `usage`, `finish_reason`)."""

    kind: str
    text: str = ""
    usage: Usage | None = None
    finish_reason: str = ""


@dataclass
class GenerationResult:
    text: str
    usage: Usage
    finish_reason: str = ""


class Provider(ABC):
    kind: str = ""

    @abstractmethod
    def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        """Yield text deltas, then one final "done" event."""

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Non-streaming call. Default: collect the stream."""
        parts: list[str] = []
        usage, reason = Usage(), ""
        async for event in self.stream(request):
            if event.kind == "text":
                parts.append(event.text)
            elif event.kind == "done":
                usage = event.usage or usage
                reason = event.finish_reason
        return GenerationResult("".join(parts), usage, reason)

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Model ids offered by the backend."""

    async def aclose(self) -> None:
        """Release network resources."""
