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
import time
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
        # ADR 0019 R2: a cancelled request may still be billed, so a step cancelled `quiet_after` times in a row
        # waits `quiet_seconds` of real quiet before it is made again (a reply in that time restarts the wait)
        self._last_busy = 0.0  # monotonic time user work last started or ended
        self.quiet_after = 3
        self.quiet_seconds = 20.0

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
        self._last_busy = time.monotonic()
        if self._idle is not None:
            self._idle.clear()
        if self._enabled():
            for task in list(self._steps):
                if not task.done():
                    self._preempted.add(task)
                    task.cancel()

    def leave(self) -> None:
        self._count = max(0, self._count - 1)
        self._last_busy = time.monotonic()
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

    async def run_background(self, factory: Callable[[], Awaitable[T]],
                             on_cancel: Callable[[int, bool], None] | None = None) -> T:
        """Run one step of background work, giving way to user work (see the module docstring).

        `on_cancel(times, quiet)` tells how often this step was cancelled in a row, and whether it now waits the
        quiet window (called again with quiet=False when it starts)."""
        cancelled = 0
        while True:
            await self.wait_idle()
            if cancelled >= self.quiet_after:
                if on_cancel is not None:
                    on_cancel(cancelled, True)
                left = self._last_busy + self.quiet_seconds - time.monotonic()
                if left > 0:
                    await asyncio.sleep(left)
                    continue  # look again: a reply may have come meanwhile
                cancelled = 0
                if on_cancel is not None:
                    on_cancel(0, False)
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
                    cancelled += 1
                    if on_cancel is not None:
                        on_cancel(cancelled, False)
                    continue
            return task.result()
