"""Event bus with two kinds of hooks, both isolated from handler failures.

- **action**: `await bus.emit("chat.message_received", message=...)`. Handlers run in priority
  order and cannot change anything; their return values are ignored.
- **filter**: `value = await bus.filter("prompt.built", value, chat=...)`. Each handler receives
  the current value and returns the new one (returning None keeps the value unchanged).

A handler that raises is logged and skipped; the pipeline continues. Handlers may be sync or
async. Lower `priority` runs first. `off(owner)` removes everything a plugin registered.
"""

from __future__ import annotations

import inspect
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(order=True)
class _Handler:
    priority: int
    seq: int
    fn: Callable[..., Any]
    owner: str


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[_Handler]] = defaultdict(list)
        self._seq = 0
        self.errors: list[tuple[str, str, BaseException]] = []

    def on(self, event: str, fn: Callable[..., Any], *, priority: int = 100, owner: str = "") -> None:
        self._seq += 1
        handlers = self._handlers[event]
        handlers.append(_Handler(priority, self._seq, fn, owner))
        handlers.sort()

    def off(self, owner: str) -> int:
        """Remove every handler registered by `owner`. Returns how many were removed."""
        removed = 0
        for event, handlers in self._handlers.items():
            kept = [h for h in handlers if h.owner != owner]
            removed += len(handlers) - len(kept)
            self._handlers[event] = kept
        return removed

    def handlers(self, event: str) -> list[_Handler]:
        return list(self._handlers.get(event, ()))

    async def emit(self, event: str, **payload: Any) -> None:
        for handler in self.handlers(event):
            await self._call(event, handler, **payload)

    async def filter(self, event: str, value: Any, **payload: Any) -> Any:
        for handler in self.handlers(event):
            result = await self._call(event, handler, value, **payload)
            if result is not None:
                value = result
        return value

    async def _call(self, event: str, handler: _Handler, *args: Any, **kwargs: Any) -> Any:
        try:
            result = handler.fn(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            log.exception("Handler %r for %r failed (owner=%r)", handler.fn, event, handler.owner)
            self.errors.append((event, handler.owner, exc))
            return None
