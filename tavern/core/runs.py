"""Generation runs: one background task per chat, observable by any number of SSE clients.

A run outlives the HTTP connection that started it: if the browser tab sleeps (common on Android)
the reply still completes and is saved. Clients attach with `Run.subscribe()` and first receive
every event emitted so far, so reconnecting shows the full state.

Event dicts always have "type". Types used by the core:
  start    {run_id, user_message?, message}      run accepted, messages exist in the database
  delta    {id?, text}                            piece of the reply (id: which one, in a turn)
  turn_start {run_id, user_message?}              a turn of several replies began (stage 7б)
  reply_done / reply_error {message} / {id, error}  one reply of a turn finished / failed
  trace    {kind, ...}                            progress for plugin panels (agent steps, etc.)
  done     {message, usage}                       finished normally
  stopped  {message}                              cancelled by the user; partial text was kept
  error    {error: {code, message}}               failed; partial text (if any) was kept
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from tavern.core.budget import RunBudget
from tavern.core.foreground import Foreground
from tavern.errors import Conflict
from tavern.util.ids import new_id

log = logging.getLogger(__name__)

KEEP_FINISHED = 20


class Run:
    def __init__(self, chat_id: str, budget: RunBudget) -> None:
        self.id = new_id()
        self.chat_id = chat_id
        self.budget = budget
        self.events: list[dict[str, Any]] = []
        self.finished = False
        self.task: asyncio.Task[None] | None = None
        self._queues: set[asyncio.Queue[tuple[int, dict[str, Any]] | None]] = set()

    def emit(self, type: str, **data: Any) -> None:
        event = {"type": type, **data}
        self.events.append(event)
        for queue in self._queues:
            queue.put_nowait((len(self.events) - 1, event))

    def trace(self, kind: str, **data: Any) -> None:
        self.emit("trace", kind=kind, **data)

    def finish(self) -> None:
        self.finished = True
        for queue in self._queues:
            queue.put_nowait(None)

    async def subscribe(
        self, after: int = -1, heartbeat: float = 0.0
    ) -> AsyncIterator[tuple[int, dict[str, Any]]]:
        """Yield (index, event): past events with index > `after`, then live ones until the end.

        Reconnecting clients pass the last index they saw (SSE `Last-Event-ID`). With `heartbeat`
        seconds set, a `{"type": "ping"}` event (index -1) is yielded during silence.
        """
        queue: asyncio.Queue[tuple[int, dict[str, Any]] | None] = asyncio.Queue()
        for index, event in enumerate(self.events):
            if index > after:
                queue.put_nowait((index, event))
        if self.finished:
            queue.put_nowait(None)
        else:
            self._queues.add(queue)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), heartbeat) if heartbeat else await queue.get()
                except asyncio.TimeoutError:
                    yield -1, {"type": "ping"}  # keeps proxies and mobile browsers from dropping the stream
                    continue
                if item is None:
                    return
                yield item
        finally:
            self._queues.discard(queue)

    def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def summary(self) -> dict[str, Any]:
        return {"id": self.id, "chat_id": self.chat_id, "finished": self.finished,
                "budget": self.budget.snapshot()}


class RunManager:
    def __init__(self, foreground: Foreground | None = None) -> None:
        # every run is user work: background model work gives way while one is going on (core/foreground.py)
        self.foreground = foreground or Foreground()
        self._active: dict[str, Run] = {}
        self._recent: OrderedDict[str, Run] = OrderedDict()

    def start(
        self, chat_id: str, budget: RunBudget, body: Callable[[Run], Awaitable[None]]
    ) -> Run:
        if chat_id in self._active:
            raise Conflict("A generation is already running in this chat", code="busy")
        run = Run(chat_id, budget)
        self._active[chat_id] = run
        self.foreground.enter()
        run.task = asyncio.create_task(self._guard(run, body), name=f"run-{run.id}")
        run.task.add_done_callback(lambda _t: self._cleanup(run))
        return run

    async def _guard(self, run: Run, body: Callable[[Run], Awaitable[None]]) -> None:
        try:
            await body(run)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - last-resort net; bodies report their own errors
            log.exception("Generation run %s crashed", run.id)
            run.emit("error", error={"code": "internal", "message": "Generation crashed, see logs"})

    def _cleanup(self, run: Run) -> None:
        # Done callback (not `finally`): it also runs when the task was cancelled before starting.
        self._active.pop(run.chat_id, None)
        self.foreground.leave()
        run.finish()
        self._recent[run.id] = run
        while len(self._recent) > KEEP_FINISHED:
            self._recent.popitem(last=False)

    def active(self, chat_id: str) -> Run | None:
        return self._active.get(chat_id)

    def running(self) -> list[str]:
        """Keys of the runs going on now (chat ids, "assistant:<id>")."""
        return list(self._active)

    def get(self, run_id: str) -> Run | None:
        for run in self._active.values():
            if run.id == run_id:
                return run
        return self._recent.get(run_id)

    def stop(self, chat_id: str) -> bool:
        run = self._active.get(chat_id)
        if run is None:
            return False
        run.stop()
        return True

    async def shutdown(self) -> None:
        tasks = [r.task for r in self._active.values() if r.task is not None]
        for run in list(self._active.values()):
            run.stop()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
