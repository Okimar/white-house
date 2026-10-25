"""LLMClient: the one way code (core and plugins) calls a model.

It wraps a Provider and charges a shared `RunBudget` for every call, so a plugin that fans out to
many parallel agents cannot exceed the limits of the run it belongs to. `fork()` makes a client
for another provider/model that shares the same budget and trace.

    llm = LLMClient(provider, model="gpt-x", budget=budget, trace=run.trace)
    other = llm.fork(provider=cheap_provider, model="small")
    a, b = await asyncio.gather(llm.generate(msgs_a), other.generate(msgs_b))
    plan = await llm.generate_json(msgs, schema={...})
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from tavern.core.budget import RunBudget
from tavern.providers.base import (
    ChatMessage,
    GenerationRequest,
    GenerationResult,
    Provider,
    ProviderError,
    StreamEvent,
    Usage,
)

TraceFn = Callable[..., None]


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str = "",
        budget: RunBudget | None = None,
        trace: TraceFn | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.budget = budget or RunBudget(max_steps=0)
        self._trace = trace
        self.defaults = defaults or {}

    def fork(
        self, *, provider: Provider | None = None, model: str | None = None, **defaults: Any
    ) -> LLMClient:
        """Another client sharing this one's budget and trace."""
        return LLMClient(
            provider or self.provider,
            model=self.model if model is None else model,
            budget=self.budget,
            trace=self._trace,
            defaults={**self.defaults, **defaults},
        )

    def _request(self, messages: list[ChatMessage], opts: dict[str, Any]) -> GenerationRequest:
        merged = {**self.defaults, **opts}
        return GenerationRequest(
            messages=messages,
            model=merged.pop("model", self.model),
            max_tokens=merged.pop("max_tokens", 0),
            temperature=merged.pop("temperature", None),
            top_p=merged.pop("top_p", None),
            stop=merged.pop("stop", []),
            response_format=merged.pop("response_format", None),
            extra=merged,
        )

    def _charge(self, usage: Usage, messages: list[ChatMessage], text: str) -> Usage:
        if usage.total == 0:  # backend reported nothing: estimate so limits still work
            usage = Usage(
                sum(len(m.content) for m in messages) // 4, len(text) // 4
            )
        self.budget.charge(usage.prompt_tokens, usage.completion_tokens)
        return usage

    def trace(self, kind: str, **data: Any) -> None:
        if self._trace:
            self._trace(kind, **data)

    async def stream(self, messages: list[ChatMessage], **opts: Any) -> AsyncIterator[StreamEvent]:
        self.budget.begin_step()
        request = self._request(messages, opts)
        parts: list[str] = []
        async for event in self.provider.stream(request):
            if event.kind == "text":
                parts.append(event.text)
                yield event
            else:
                usage = self._charge(event.usage or Usage(), messages, "".join(parts))
                yield StreamEvent("done", usage=usage, finish_reason=event.finish_reason)

    async def generate(self, messages: list[ChatMessage], **opts: Any) -> GenerationResult:
        self.budget.begin_step()
        result = await self.provider.generate(self._request(messages, opts))
        result.usage = self._charge(result.usage, messages, result.text)
        return result

    async def generate_json(
        self, messages: list[ChatMessage], *, schema: dict[str, Any] | None = None, **opts: Any
    ) -> Any:
        """Ask for JSON and return the parsed value.

        Uses the provider's JSON mode (`json_schema` when a schema is given). If the reply still
        is not valid JSON (some local models ignore the mode), the first JSON value inside the text
        is extracted. Raises ProviderError when nothing parseable comes back.
        """
        fmt = {"type": "json_schema", "name": "response", "schema": schema} if schema else {
            "type": "json_object"
        }
        result = await self.generate(messages, response_format=fmt, **opts)
        return extract_json(result.text)


def extract_json(text: str) -> Any:
    """Parse JSON from model output, tolerating code fences and surrounding prose."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for start, char in enumerate(stripped):
        if char in "{[":
            try:
                value, _ = decoder.raw_decode(stripped[start:])
            except json.JSONDecodeError:
                continue
            return value
    raise ProviderError("The model did not return valid JSON", details={"text": stripped[:200]})
