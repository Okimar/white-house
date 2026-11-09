"""Foreground work comes first: background model work never runs beside the user's requests.

Owner's rule: while the user's own request (a chat reply of any kind, an assistant request) is going on,
background work (vectorization now, anything that calls a model later) stops AT ONCE, keeps its progress,
and carries on by itself when the user's work is over. A free embedding account allows only a few requests
a minute, so a background batch must never eat the limit a reply's meaning search needs.

How it is used:
  * user work marks itself busy: `with fg.busy():` (or `fg.enter()` / `fg.leave()` in pairs). The counter
    allows overlapping work (two chats at once). `RunManager.start` does it for every run, so all replies and
    all assistant requests are covered without each caller remembering.
  * background work runs each small step (one batch, one request) through `await fg.run_background(factory)`.
    `factory` makes a fresh coroutine for the step. If user work is busy the step waits first; if user work
    starts while the step runs, the step's task is cancelled immediately (the HTTP request in flight is dropped,
    not awaited) and the same step is made again once everything is idle. A step must therefore be safe to
    redo: keep results only after the step returns.
  * the setting `background_yield` (default on, «Прерывать фоновую работу при запросе») switches this off:
    steps then just run, beside replies, as before.
A reply never waits for background work: `enter()` only cancels tasks and returns. Waiting happens on an
asyncio.Event, so the loop is never blocked; an outer cancel (app shutdown) cancels the running step too.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class Foreground:
    def __init__(self, enabled: Callable[[], bool] | None = None) -> None:
        self._enabled = enabled or (lambda: True)
        self._count = 0
        self._idle: asyncio.Event | None = None  # made on first use, inside the running loop
        self._steps: set[asyncio.Task[Any]] = set()
        self._preempted: set[asyncio.Task[Any]] = set()

    @property
    def active(self) -> bool:
        """Background work has to give way now: user work is busy and the setting is on."""
        return self._count > 0 and self._enabled()

    @property
    def count(self) -> int:
        return self._count

    def _event(self) -> asyncio.Event:
        if self._idle is None:
            self._idle = asyncio.Event()
            if self._count == 0:
                self._idle.set()
        return self._idle

    def enter(self) -> None:
        """User work starts: background steps in flight are cancelled at once. Never waits."""
        self._count += 1
        if self._idle is not None:
            self._idle.clear()
        if self._enabled():
            for task in list(self._steps):
                if not task.done():
                    self._preempted.add(task)
                    task.cancel()

    def leave(self) -> None:
        self._count = max(0, self._count - 1)
        if self._count == 0 and self._idle is not None:
            self._idle.set()

    @contextmanager
    def busy(self) -> Iterator[None]:
        self.enter()
        try:
            yield
        finally:
            self.leave()

    async def wait_idle(self) -> None:
        """Wait until no user work is busy (returns at once with the setting off)."""
        while self.active:
            await self._event().wait()

    async def run_background(self, factory: Callable[[], Awaitable[T]]) -> T:
        """Run one step of background work, giving way to user work (see the module docstring)."""
        while True:
            await self.wait_idle()
            task = asyncio.ensure_future(factory())
            self._steps.add(task)
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:  # the background job itself is stopped (shutdown, new start)
                task.cancel()
                raise
            finally:
                self._steps.discard(task)
            if task in self._preempted:
                self._preempted.discard(task)
                if task.cancelled():
                    log.debug("Background step cancelled for user work, will be redone")
                    continue
            return task.result()
