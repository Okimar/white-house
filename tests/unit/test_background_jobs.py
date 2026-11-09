"""core/background.py: jobs that give way to the user's requests (preempt, redo), progress, one job per key,
cancel by owner, rate-limit sleeping with a cap."""

from __future__ import annotations

import asyncio


from tavern.core import background
from tavern.core.background import JobGaveUp


async def wait_until(cond, tries: int = 300) -> None:
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def test_a_job_runs_after_its_delay_and_reports_progress(ctx):
    seen = []

    async def work(job):
        job.progress(1, 3, "один")
        seen.append(ctx.jobs.status("p", "k"))
        job.progress(3, 3, "")

    ctx.jobs.schedule("p", "k", work, delay=0.01)
    assert ctx.jobs.status("p", "k")["queued"] is True
    await wait_until(lambda: seen)
    assert seen[0]["running"] is True and seen[0]["done"] == 1 and seen[0]["total"] == 3 and seen[0]["note"] == "один"
    await wait_until(lambda: not ctx.jobs.status("p", "k")["running"])
    status = ctx.jobs.status("p", "k")
    assert status["done"] == 3 and status["error"] == "" and status["queued"] is False


async def test_scheduling_again_while_it_runs_asks_for_one_more_pass_not_a_second_job(ctx):
    started = asyncio.Event()
    release = asyncio.Event()
    passes = []

    async def work(job):
        passes.append(1)
        started.set()
        await release.wait()

    ctx.jobs.schedule("p", "k", work, delay=0)
    await asyncio.wait_for(started.wait(), 2)
    ctx.jobs.schedule("p", "k", work, delay=0)
    ctx.jobs.schedule("p", "k", work, delay=0)  # still one more pass only
    assert len(passes) == 1
    release.set()
    await wait_until(lambda: not ctx.jobs.status("p", "k")["running"] and len(passes) == 2)
    await asyncio.sleep(0.05)
    assert len(passes) == 2


async def test_user_work_cancels_the_step_in_flight_and_it_is_redone_afterwards(ctx):
    attempts = {"started": 0, "cancelled": 0, "finished": 0}
    hold = asyncio.Event()

    async def step():
        attempts["started"] += 1
        try:
            await hold.wait()
        except asyncio.CancelledError:
            attempts["cancelled"] += 1
            raise
        attempts["finished"] += 1
        return "made"

    results = []

    async def work(job):
        results.append(await job.step(step))

    ctx.jobs.schedule("p", "k", work, delay=0)
    await wait_until(lambda: attempts["started"] == 1)
    ctx.foreground.enter()  # a reply starts
    await wait_until(lambda: attempts["cancelled"] == 1)
    assert ctx.jobs.status("p", "k")["paused"] is True
    await asyncio.sleep(0.05)
    assert attempts["started"] == 1  # nothing runs during the reply
    hold.set()
    ctx.foreground.leave()
    await wait_until(lambda: results)
    assert results == ["made"] and attempts["started"] == 2 and attempts["finished"] == 1


async def test_cancel_stops_one_job_or_all_of_an_owner(ctx):
    async def forever(job):
        await asyncio.sleep(60)

    ctx.jobs.schedule("a", "1", forever, delay=0)
    ctx.jobs.schedule("a", "2", forever, delay=0)
    ctx.jobs.schedule("b", "1", forever, delay=0)
    await asyncio.sleep(0.02)
    assert ctx.jobs.cancel("a", "1") == 1
    assert ctx.jobs.status("a", "1")["running"] is False
    assert ctx.jobs.status("a", "2")["running"] is True
    assert ctx.jobs.cancel("a") == 1
    assert ctx.jobs.status("b", "1")["running"] is True
    ctx.jobs.cancel_all()
    await asyncio.sleep(0.02)
    assert ctx.jobs.status("b", "1")["running"] is False


async def test_a_failing_job_keeps_its_error_and_can_be_scheduled_again(ctx):
    calls = []

    async def work(job):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("сломалось")

    ctx.jobs.schedule("p", "k", work, delay=0)
    await wait_until(lambda: calls and not ctx.jobs.status("p", "k")["running"])
    await asyncio.sleep(0.02)
    assert "сломалось" in ctx.jobs.status("p", "k")["error"]
    ctx.jobs.schedule("p", "k", work, delay=0)
    await wait_until(lambda: len(calls) == 2)
    await asyncio.sleep(0.02)
    assert ctx.jobs.status("p", "k")["error"] == ""


async def test_rate_wait_counts_its_sleeps_and_gives_up_past_the_cap(ctx, monkeypatch):
    monkeypatch.setattr(background, "MAX_RATE_WAIT", 0.05)
    slept = []

    async def work(job):
        for _ in range(10):
            await job.rate_wait(0.02)
            slept.append(1)

    ctx.jobs.schedule("p", "k", work, delay=0)
    await wait_until(lambda: ctx.jobs.status("p", "k")["error"])
    assert len(slept) == 2  # 0.02 + 0.02 fit under 0.05, the third would not
    assert isinstance(JobGaveUp("x"), Exception)


async def test_progress_goes_to_every_open_page_as_a_notice(ctx):
    got = []

    async def listen():
        async for event in ctx.notices.subscribe():
            if event["type"] == "job":
                got.append(event)

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.01)

    async def work(job):
        job.progress(2, 5, "идёт")

    ctx.jobs.schedule("p", "k", work, delay=0)
    await wait_until(lambda: any(e["done"] == 2 for e in got))
    task.cancel()
    assert got[0]["owner"] == "p" and got[0]["key"] == "k"


def test_schedule_without_a_running_loop_does_nothing(ctx):
    async def work(job):
        raise AssertionError("must not run")

    ctx.jobs.schedule("p", "k", work)  # no loop: silently nothing
    assert ctx.jobs.status("p", "k")["queued"] is False
