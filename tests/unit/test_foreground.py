"""core/foreground.py: the busy counter, giving way, cancelling a step at once, redoing it, the off switch."""

from __future__ import annotations

import asyncio

import pytest

from tavern.core.foreground import Foreground


async def tick(n: int = 3) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def test_counter_allows_overlapping_user_work():
    fg = Foreground()
    assert not fg.active
    fg.enter()
    fg.enter()
    assert fg.active and fg.count == 2
    fg.leave()
    assert fg.active
    fg.leave()
    fg.leave()  # an extra leave never goes below zero
    assert not fg.active and fg.count == 0


def test_busy_context_manager_releases_on_error():
    fg = Foreground()
    with pytest.raises(ValueError):
        with fg.busy():
            assert fg.active
            raise ValueError
    assert not fg.active


async def test_step_runs_at_once_when_idle():
    fg = Foreground()
    assert await fg.run_background(lambda: asyncio.sleep(0, "ok")) == "ok"


async def test_step_waits_while_busy_and_runs_after():
    fg = Foreground()
    calls: list[int] = []

    async def step() -> str:
        calls.append(1)
        return "done"

    fg.enter()
    task = asyncio.ensure_future(fg.run_background(step))
    await tick()
    assert calls == [] and not task.done()
    fg.leave()
    assert await asyncio.wait_for(task, 1) == "done" and calls == [1]


async def test_running_step_is_cancelled_at_once_and_redone_after_idle():
    fg = Foreground()
    started: list[int] = []
    cancelled: list[int] = []
    release = asyncio.Event()

    async def step() -> str:
        started.append(1)
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
        return "finished"

    task = asyncio.ensure_future(fg.run_background(step))
    await tick()
    assert started == [1]
    fg.enter()  # does not wait for the step
    await tick()
    assert cancelled == [1] and not task.done()
    fg.enter()  # nested: the second user work keeps it paused after the first ends
    fg.leave()
    await tick()
    assert started == [1]
    fg.leave()
    await tick()
    assert started == [1, 1]
    release.set()
    assert await asyncio.wait_for(task, 1) == "finished"


async def test_setting_off_means_no_waiting_and_no_cancelling():
    fg = Foreground(lambda: False)
    release = asyncio.Event()

    async def step() -> str:
        await release.wait()
        return "x"

    fg.enter()
    task = asyncio.ensure_future(fg.run_background(step))
    await tick()
    fg.enter()
    await tick()
    assert not task.done()  # still running beside the user work
    release.set()
    assert await asyncio.wait_for(task, 1) == "x"


async def test_outer_cancel_stops_the_step():
    fg = Foreground()
    cancelled: list[int] = []

    async def step() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    task = asyncio.ensure_future(fg.run_background(step))
    await tick()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await tick()
    assert cancelled == [1]


async def test_step_error_is_raised_not_retried():
    fg = Foreground()

    async def step() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await fg.run_background(step)
