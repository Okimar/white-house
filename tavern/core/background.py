"""Background jobs that give way to the user (`ctx.jobs`): one job per (owner, key), progress kept and shown.

A job is a coroutine `work(job)` that works through a to-do list in small steps. It is the general form of what
`core/foreground.py` allows: each step goes through `await job.step(factory)`, so a user request (a chat reply,
an assistant request) cancels the step in flight at once and the same step is made again when the user's work
is over. Rules for job authors (the same as `Foreground.run_background`): a step must be safe to redo, and its
result is kept only after the step returned. A good job is *todo-driven*: every pass asks its tables "what is
next?", so a restart, a crash or a cancelled step loses nothing.

    ctx.jobs.schedule("memory", f"chat:{chat_id}", work, delay=2.0)

`schedule` again while the job waits or runs does not start a second job: it only asks for one more pass after
the current one ("run once more"). `status()` gives the page what to show: running, paused (user work is busy),
done / total, a note, an error. `cancel(owner, key=None)` stops one job or all of an owner's (a plugin switched
off does it). Progress goes to every open page as the notice `job`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from tavern.errors import AppError

if TYPE_CHECKING:
    from tavern.context import AppContext

log = logging.getLogger(__name__)

T = TypeVar("T")

MAX_RATE_WAIT = 300.0  # seconds a job may sleep on rate limits before it gives up (progress stays in its tables)


class JobGaveUp(AppError):
    """A job waited out rate limits for too long: it stops, the next schedule continues from its tables."""

    code = "job_rate_limit"


@dataclass
class _State:
    owner: str
    key: str
    work: Callable[[JobContext], Awaitable[None]]
    task: asyncio.Task[None] | None = None
    again: bool = False
    running: bool = False
    stopping: bool = False
    done: int = 0
    total: int = 0
    note: str = ""
    error: str = ""
    waited: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class JobContext:
    """What a job's `work` gets: steps that yield to the user, progress, rate-limit sleeping."""

    def __init__(self, jobs: Jobs, state: _State) -> None:
        self._jobs = jobs
        self._state = state

    @property
    def stopped(self) -> bool:
        return self._state.stopping

    async def step(self, factory: Callable[[], Awaitable[T]]) -> T:
        """One small step of work; cancelled at once while user work is busy and made again afterwards."""
        return await self._jobs.ctx.foreground.run_background(factory)

    def progress(self, done: int, total: int, note: str = "") -> None:
        state = self._state
        state.done, state.total, state.note = done, total, note
        self._jobs._announce(state)

    async def rate_wait(self, seconds: float) -> None:
        """Sleep after a 429. The sleeps are counted; past MAX_RATE_WAIT the job gives up (JobGaveUp)."""
        state = self._state
        if state.waited + seconds > MAX_RATE_WAIT:
            raise JobGaveUp("Сервис просит подождать слишком долго. Прогресс сохранён: работа продолжится позже.")
        state.waited += seconds
        state.note = "лимит запросов: жду"
        self._jobs._announce(state)
        await asyncio.sleep(seconds)


class Jobs:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self._jobs: dict[tuple[str, str], _State] = {}

    def schedule(self, owner: str, key: str, work: Callable[[JobContext], Awaitable[None]],
                 *, delay: float = 2.0) -> None:
        """Start the job after `delay` seconds (edits that come one after another are worked together).
        One job per (owner, key): scheduling again while it waits or runs only asks for another pass."""
        current = self._jobs.get((owner, key))
        if current is not None and current.task is not None and not current.task.done():
            current.again = True
            current.work = work
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (a sync caller outside the server): nothing to run on
        state = _State(owner, key, work)
        self._jobs[(owner, key)] = state
        state.task = loop.create_task(self._run(state, delay))

    async def _run(self, state: _State, delay: float) -> None:
        job = JobContext(self, state)
        try:
            await asyncio.sleep(delay)
            while True:
                state.again = False
                state.running = True
                state.error = ""
                state.waited = 0.0
                self._announce(state)
                try:
                    await state.work(job)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a job must not die loudly; the page shows the note
                    state.error = exc.message if isinstance(exc, AppError) else f"{type(exc).__name__}: {exc}"
                    log.warning("Background job %s/%s failed: %s", state.owner, state.key, state.error,
                                exc_info=not isinstance(exc, AppError))
                    state.again = False
                finally:
                    state.running = False
                if not state.again:
                    break
        finally:
            if self._jobs.get((state.owner, state.key)) is state:
                # keep the last status readable until the next schedule, but the task is over
                state.task = None
            self._announce(state)

    def status(self, owner: str, key: str) -> dict[str, Any]:
        state = self._jobs.get((owner, key))
        if state is None:
            return {"running": False, "queued": False, "paused": False, "done": 0, "total": 0, "note": "", "error": ""}
        alive = state.task is not None and not state.task.done()
        return {"running": state.running and alive, "queued": alive and not state.running,
                "paused": state.running and alive and self.ctx.foreground.active,
                "done": state.done, "total": state.total, "note": state.note, "error": state.error}

    def cancel(self, owner: str, key: str | None = None) -> int:
        """Stop one job (or every job of `owner`). Returns how many were stopped."""
        count = 0
        for (job_owner, job_key), state in list(self._jobs.items()):
            if job_owner != owner or (key is not None and job_key != key):
                continue
            state.stopping = True
            if state.task is not None and not state.task.done():
                state.task.cancel()
                count += 1
            self._jobs.pop((job_owner, job_key), None)
        return count

    def cancel_all(self) -> None:
        for owner in {o for o, _ in self._jobs}:
            self.cancel(owner)

    def _announce(self, state: _State) -> None:
        self.ctx.notices.emit("job", owner=state.owner, key=state.key, **self.status(state.owner, state.key))
