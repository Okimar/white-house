"""LLMClient: the one way code (core and plugins) calls a model.

It wraps a Provider and charges a shared `RunBudget` for every call, so a plugin that fans out to
many parallel agents cannot exceed the limits of the run it belongs to. `fork()` makes a client
for another provider/model that shares the same budget and trace.

A `meter` (set by the app, see services/spending) is told about every finished or interrupted call:
it records the spending and returns the cost in dollars, which the budget's `max_cost` counts.
A `gate` (set for plugins, see plugins/manager.py) is awaited before every request and raises to
refuse it: no permission to use models, the plugin's own limit, or the user said no.

    llm = LLMClient(provider, model="gpt-x", budget=budget, trace=run.trace)
    other = llm.fork(provider=cheap_provider, model="small")
    a, b = await asyncio.gather(llm.generate(msgs_a), other.generate(msgs_b))
    plan = await llm.generate_json(msgs, schema={...})
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from tavern.core import structured
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


class StructuredOutputError(ProviderError):
    """The model kept answering in the wrong shape. `details`: the last text and what did not fit."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, reason="format", details=details)

# (model asked for, usage, estimated, provider) -> cost in dollars or None when the price is unknown
MeterFn = Callable[[str, Usage, bool, Provider], "float | None"]
GateFn = Callable[[], Awaitable[None]]


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        *,
        model: str = "",
        budget: RunBudget | None = None,
        trace: TraceFn | None = None,
        defaults: dict[str, Any] | None = None,
        meter: MeterFn | None = None,
        gate: GateFn | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.budget = budget or RunBudget(max_steps=0)
        self._trace = trace
        self.defaults = defaults or {}
        self.meter = meter
        self.gate = gate

    def fork(
        self, *, provider: Provider | None = None, model: str | None = None, meter: MeterFn | None = None,
        gate: GateFn | None = None, **defaults: Any,
    ) -> LLMClient:
        """Another client sharing this one's budget and trace (and meter and gate, unless given others)."""
        return LLMClient(
            provider or self.provider,
            model=self.model if model is None else model,
            budget=self.budget,
            trace=self._trace,
            defaults={**self.defaults, **defaults},
            meter=meter or self.meter,
            gate=gate or self.gate,
        )

    async def _begin(self) -> None:
        if self.gate is not None:
            await self.gate()
        self.budget.begin_step()

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

    def _charge(
        self, usage: Usage, messages: list[ChatMessage], text: str, model: str, *, estimated: bool = False
    ) -> Usage:
        if usage.total == 0:  # backend reported nothing: estimate so limits still work
            usage = Usage(
                sum(len(m.content) for m in messages) // 4, len(text) // 4
            )
            estimated = True
        cost = self.meter(model, usage, estimated, self.provider) if self.meter else None
        self.budget.charge(usage.prompt_tokens, usage.completion_tokens, cost or 0.0)
        return usage

    def trace(self, kind: str, **data: Any) -> None:
        if self._trace:
            self._trace(kind, **data)

    async def stream(self, messages: list[ChatMessage], **opts: Any) -> AsyncIterator[StreamEvent]:
        await self._begin()
        request = self._request(messages, opts)
        parts: list[str] = []
        done = False
        try:
            async for event in self.provider.stream(request):
                if event.kind == "text":
                    parts.append(event.text)
                    yield event
                elif event.kind == "reasoning":
                    yield event
                elif event.kind == "done":
                    done = True
                    usage = self._charge(event.usage or Usage(), messages, "".join(parts), request.model)
                    yield StreamEvent("done", usage=usage, finish_reason=event.finish_reason)
        except (asyncio.CancelledError, GeneratorExit):
            # Stopped by the user or lost a race: the service still bills what it already did.
            if not done:
                self._charge(Usage(), messages, "".join(parts), request.model, estimated=True)
            raise

    async def generate(self, messages: list[ChatMessage], **opts: Any) -> GenerationResult:
        await self._begin()
        request = self._request(messages, opts)
        result = await self.provider.generate(request)
        result.usage = self._charge(result.usage, messages, result.text, request.model)
        return result

    async def generate_json(
        self, messages: list[ChatMessage], *, schema: dict[str, Any] | None = None, retries: int = 1,
        **opts: Any,
    ) -> Any:
        """Ask for JSON and return the parsed value, checked against `schema` when one is given.

        The provider's JSON mode is used (`json_schema` with a schema, else `json_object`); Claude
        gets it as a forced tool, Gemini as responseJsonSchema. The request is also said in words,
        so backends without a JSON mode (text completion, small local models, proxies) understand
        it too; one that rejects the mode is asked again without it. The first JSON value in the
        reply is taken (code fences and prose around it are tolerated). When the value is not valid
        JSON or does not fit the schema (core/structured.problems), the model is told what was wrong
        and asked again, up to `retries` more times. Then StructuredOutputError is raised.
        Every attempt is a paid request charged to the budget.
        """
        fmt: dict[str, Any] = (
            {"type": "json_schema", "name": "response", "schema": schema} if schema else {"type": "json_object"}
        )
        history = _with_note(messages, structured.instruction(schema))
        native = True
        text, found = "", []
        for _ in range(max(0, retries) + 1):
            try:
                result = await self.generate(history, response_format=fmt if native else None, **opts)
            except ProviderError as exc:
                if not native or exc.reason != "request":
                    raise
                native = False  # the backend does not take the JSON mode: words only
                result = await self.generate(history, **opts)
            text = result.text
            try:
                value = extract_json(text)
            except ProviderError:
                found = []
            else:
                found = structured.problems(value, schema) if schema else []
                if not found:
                    return value
            self.trace("json_retry", problems=found)
            history = [*history, ChatMessage("assistant", text), ChatMessage("user", structured.retry_note(found))]
        raise StructuredOutputError(
            "The model did not answer in the required shape" + (f": {found[0]}" if found else " (no valid JSON)"),
            details={"text": text[:500], "problems": found},
        )


def _with_note(messages: list[ChatMessage], note: str) -> list[ChatMessage]:
    """The note joins the last user turn (or comes as one): every backend reads that last."""
    if messages and messages[-1].role == "user":
        last = messages[-1]
        return [*messages[:-1], replace(last, content=f"{last.content}\n\n{note}")]
    return [*messages, ChatMessage("user", note)]


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
