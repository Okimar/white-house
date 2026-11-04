"""Fallback steps: what to try when a connection fails before writing anything.

A connection may list steps in its setting `fallbacks`, tried in order:
    {"on": ["refused", "limit", ...], "connection": "<id>" or "" (this one), "model": "" (same),
     "key": "<key id>" or "" (the usual order)}
`on` names the failures a step answers (ProviderError.reason, plus "empty" for an empty reply).
Another key with its own proxy is "another proxy"; another connection is "another API".

Refusals that come as ordinary text ("I can't help with that") are caught by phrases: while a step
answers "refused", a reply is held back until it is longer than HOLD_CHARS. A short reply that
matches a phrase counts as a refusal; anything longer streams on and is never judged.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Any

from tavern.providers.base import GenerationRequest, ModelInfo, Provider, ProviderError, StreamEvent, Usage

log = logging.getLogger(__name__)

REASONS = ("refused", "empty", "limit", "auth", "network", "server", "request")
HOLD_CHARS = 400
DEFAULT_PHRASES = [
    "I can't help with", "I cannot help with", "I can't assist", "I cannot assist", "I'm sorry, but I can't",
    "I am sorry, but I cannot", "I won't be able to", "I'm not able to continue", "As an AI",
    "не могу помочь", "не могу выполнить", "не могу продолжить", "я не могу с этим", "Извините, но я не могу",
]


def check_steps(raw: Any) -> list[dict[str, Any]]:
    """The `fallbacks` setting from untrusted JSON. Raises ValueError with a message for the user."""
    if not isinstance(raw, list) or len(raw) > 10:
        raise ValueError("Запасные пути: список до 10 шагов")
    steps = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Запасной путь должен быть объектом")
        on = item.get("on") or []
        if not isinstance(on, list) or not on or any(r not in REASONS for r in on):
            raise ValueError(f"Запасной путь: «когда» — из {', '.join(REASONS)}")
        step: dict[str, Any] = {"on": list(dict.fromkeys(on))}
        for name in ("connection", "model", "key"):
            value = item.get(name, "")
            if not isinstance(value, str) or len(value) > 200:
                raise ValueError(f"Запасной путь: «{name}» — строка")
            step[name] = value.strip()
        steps.append(step)
    return steps


class FallbackProvider(Provider):
    """The connection's own provider first, then each step whose `on` matches the failure."""

    def __init__(
        self,
        primary: Provider,
        steps: list[dict[str, Any]],
        build: Callable[[dict[str, Any]], tuple[Provider, str, str]],
        *,
        phrases: list[str] | None = None,
    ) -> None:
        self.kind = primary.kind
        self._primary = primary
        self._steps = steps
        self._build = build  # step -> (provider, model or "", label shown with the reply)
        self._made: list[Provider] = []
        self._phrases = [p.lower() for p in (DEFAULT_PHRASES if phrases is None else phrases) if p.strip()]
        self._judge = bool(self._phrases) and any("refused" in s["on"] for s in steps)
        self.used = ""  # label of the fallback that answered; "" when the connection itself did
        # (connection id or "" = this one, model) of the attempt running now; spending prices by it
        self.serving: tuple[str, str] = ("", "")
        # Finished attempts thrown away (refusal, empty reply): the service billed them anyway.
        # Spending drains this list: [((connection id, model), usage)].
        self.discarded: list[tuple[tuple[str, str], Usage]] = []

    def _is_refusal(self, text: str) -> bool:
        low = re.sub(r"\s+", " ", text.lower())
        return any(p in low for p in self._phrases)

    async def _attempt(self, provider: Provider, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        """One provider's stream. While refusals are watched, a short start is held back; an empty
        or refusing reply raises before anything was passed on."""
        held: list[StreamEvent] = []
        size = 0
        passing = False
        async for event in provider.stream(request):
            if passing or event.kind == "reasoning":  # only the reply's words are judged: thinking goes live
                yield event
                continue
            if event.kind == "done":
                text = "".join(e.text for e in held if e.kind == "text")
                if (not text.strip() or (self._judge and self._is_refusal(text))) and event.usage:
                    self.discarded.append((self.serving, event.usage))
                if not text.strip():
                    raise ProviderError("The model returned an empty reply", reason="empty")
                if self._judge and self._is_refusal(text):
                    raise ProviderError(f"The model refused: {text[:120]}", reason="refused")
                for e in held:
                    yield e
                yield event
                return
            held.append(event)
            if event.kind == "text":
                size += len(event.text)
            if size and (not self._judge or size > HOLD_CHARS):
                passing = True
                for e in held:
                    yield e
                held = []

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        attempts = [(self._primary, request, "", None)]
        failure: ProviderError | None = None
        steps = iter(self._steps)
        while True:
            provider, req, label, step = attempts[-1]
            self.serving = ((step or {}).get("connection", ""), req.model)
            started = False
            try:
                async for event in self._attempt(provider, req):
                    started = started or event.kind != "reasoning"  # no reply yet: another step may still write it
                    yield event
                self.used = label
                return
            except ProviderError as exc:
                if started:
                    raise
                failure = exc
                log.info("%s failed (%s); looking for a fallback", label or "The connection", exc.reason or "?")
            step = next((s for s in steps if failure.reason in s["on"]), None)
            if step is None:
                raise failure
            built, model, step_label = self._build(step)
            self._made.append(built)
            attempts.append((built, replace(request, model=model or request.model), step_label, step))

    async def list_models(self) -> list[str]:
        return await self._primary.list_models()

    async def describe_models(self) -> list[ModelInfo]:
        return await self._primary.describe_models()

    async def generate_image(self, prompt: str, *, model: str = "", size: str = "1024x1024") -> bytes:
        return await self._primary.generate_image(prompt, model=model, size=size)

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        return await self._primary.embed(texts, model=model)

    async def aclose(self) -> None:
        await self._primary.aclose()
        for provider in self._made:
            await provider.aclose()
