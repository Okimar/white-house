"""Provider interface. Every backend (OpenAI-compatible, Anthropic, Gemini, local, plugin) implements it.

A provider turns a `GenerationRequest` into a stream of `StreamEvent`s. It must:
- honour cancellation (it runs inside an anyio cancel scope; do not swallow CancelledError),
- report `Usage` when the backend provides it,
- map `response_format` (JSON mode) to the backend's native mode, or ignore it when there is none
  (LLMClient.generate_json also asks in words and checks the answer, core/structured.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from tavern.errors import AppError


class ProviderError(AppError):
    """The backend failed or rejected the request. The message is safe to show to the user.

    `reason` lets callers decide what to do next (another key, a retry, a fallback):
    "limit" (quota or rate limit), "auth" (bad or missing key), "refused" (the model or its
    safety filter declined), "network" (unreachable, timeout), "server" (5xx), "request" (the
    backend rejected the request itself, e.g. an unknown parameter), "" (unknown).
    """

    status = 502
    code = "provider_error"

    def __init__(
        self, message: str, *, reason: str = "", upstream_status: int = 0, details: dict | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.reason = reason
        self.upstream_status = upstream_status


def reason_for_status(status: int) -> str:
    if status == 429 or status == 402:
        return "limit"
    if status in (401, 403):
        return "auth"
    if status >= 500:
        return "server"
    if status >= 400:
        return "request"
    return ""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str
    # Pictures the model should see with this message: (media type, base64 data).
    images: tuple[tuple[str, str], ...] = ()


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
    """kind: "text" (delta in `text`), "reasoning" (the model's visible thinking, in `text`)
    or "done" (final `usage`, `finish_reason`)."""

    kind: str
    text: str = ""
    usage: Usage | None = None
    finish_reason: str = ""


@dataclass
class ModelInfo:
    """What the backend tells about one model. `params` is None when it does not say which
    settings the model accepts; otherwise the names from `tavern.providers.params`."""

    id: str
    name: str = ""
    context: int = 0
    max_output: int = 0
    params: list[str] | None = None
    price_in: float | None = None  # per million prompt tokens, in dollars
    price_out: float | None = None  # per million completion tokens
    limits: dict[str, Any] = field(default_factory=dict)  # e.g. {"temperature": [0, 2]}
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name or self.id, "context": self.context,
            "max_output": self.max_output, "params": self.params, "price_in": self.price_in,
            "price_out": self.price_out, "limits": self.limits, "notes": self.notes,
        }


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

    async def describe_models(self) -> list[ModelInfo]:
        """Models with whatever the backend reports about them. Default: ids only."""
        return [ModelInfo(m) for m in await self.list_models()]

    async def generate_image(self, prompt: str, *, model: str = "", size: str = "1024x1024") -> bytes:
        """One picture for `prompt`, as image file bytes. Kinds without image support refuse."""
        raise ProviderError(f"Connections of kind {self.kind!r} cannot generate pictures")

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        """Meaning vectors (embeddings) for texts. Kinds without them refuse."""
        raise ProviderError(f"Connections of kind {self.kind!r} cannot make embeddings")

    async def aclose(self) -> None:
        """Release network resources."""
