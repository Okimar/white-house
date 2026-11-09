"""Background vectorization gives way to the user's requests (core/foreground.py): cancelled at once when a
reply starts, nothing runs during it, it resumes and finishes after, the setting turns it off."""

from __future__ import annotations

import asyncio
from typing import Any

from tests.conftest import settle_vectors

from tavern.core.budget import RunBudget
from tavern.providers.base import ProviderError
from tavern.services import character_tree


class GatedProvider:
    """Embeds with counters; `hold` (an Event) keeps a call open until set, cancellations are counted."""

    def __init__(self) -> None:
        self.started = 0
        self.cancelled = 0
        self.finished = 0
        self.hold: asyncio.Event | None = None
        self.limit_first = 0  # answer the first N calls with a 429

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.started += 1
        if self.limit_first > 0:
            self.limit_first -= 1
            raise ProviderError("rate", reason="limit", upstream_status=429)
        try:
            if self.hold is not None:
                await self.hold.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.finished += 1
        return [[1.0, 0.0] for _ in texts]

    async def aclose(self) -> None:
        pass


async def setup(client, ctx, monkeypatch, blocks: int = 3):
    conn = (await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    book = (await client.post("/api/lorebooks", json={"name": "Мир"})).json()
    for n in range(blocks):
        r = await client.post(f"/api/lorebooks/{book['id']}/blocks",
                              json={"title": f"П{n}", "content": f"Текст {n}", "keywords": ["k"], "mode": "search"})
        assert r.status_code == 201, r.text
    provider = GatedProvider()
    real_create = ctx.providers.create
    monkeypatch.setattr(ctx.providers, "create",
                        lambda connection: provider if connection["id"] == conn["id"] else real_create(connection))
    monkeypatch.setattr(character_tree, "BATCH_ITEMS", 1)  # one batch per bubble, so there are several steps
    return book, provider


async def wait_for(cond, tries: int = 300) -> None:
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def vstatus(client, book) -> dict[str, Any]:
    return (await client.get(f"/api/lorebooks/{book['id']}/blocks")).json()["vector_status"]


async def test_reply_cancels_the_batch_at_once_and_vectorization_resumes_after(client, ctx, monkeypatch):
    book, provider = await setup(client, ctx, monkeypatch)
    provider.hold = asyncio.Event()
    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await wait_for(lambda: provider.started == 1)

    ctx.foreground.enter()  # a reply starts
    await wait_for(lambda: provider.cancelled == 1)
    status = await vstatus(client, book)
    assert status["job"]["running"] and status["job"]["paused"]
    provider.hold.set()
    await asyncio.sleep(0.1)
    assert provider.started == 1 and provider.finished == 0  # nothing runs while the reply is busy

    ctx.foreground.leave()
    await settle_vectors()
    status = await vstatus(client, book)
    assert status["current"] == 3 and not status["job"]["running"] and not status["job"]["paused"]
    assert provider.finished == 3 and provider.started == 4  # the cancelled batch was asked again


async def test_a_real_reply_marks_busy_and_is_not_delayed_by_background_work(client, ctx, fake_llm, monkeypatch):
    book, provider = await setup(client, ctx, monkeypatch)
    conn = (await client.post("/api/connections", json={"name": "chat", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"],
                                            "retrieval": {"meaning_on": False}})
    char = (await client.post("/api/characters", json={"name": "Ann", "description": "x"})).json()
    chat_id = (await client.post("/api/chats", json={"character_id": char["id"]})).json()["chat"]["id"]
    provider.hold = asyncio.Event()  # a background batch hangs until released
    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await wait_for(lambda: provider.started == 1)

    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": "привет"})
    assert response.status_code == 202, response.text
    run = ctx.runs.get(response.json()["run_id"])
    assert ctx.foreground.active
    await asyncio.wait_for(run.task, 10)  # finishes although the background batch never returned
    assert run.events[-1]["type"] == "done"
    assert provider.cancelled == 1 and not ctx.foreground.active
    provider.hold.set()
    await settle_vectors()
    assert (await vstatus(client, book))["current"] == 3


async def test_stopped_or_failed_run_releases_the_background(ctx):
    async def crash(run: Any) -> None:
        raise ValueError("crash")

    async def forever(run: Any) -> None:
        await asyncio.sleep(60)

    run = ctx.runs.start("c1", RunBudget(), crash)
    assert ctx.foreground.active
    await asyncio.wait([run.task], timeout=2)
    await asyncio.sleep(0)
    assert not ctx.foreground.active
    run2 = ctx.runs.start("c2", RunBudget(), forever)
    assert ctx.foreground.active
    run2.stop()
    await asyncio.wait([run2.task], timeout=2)
    await asyncio.sleep(0)
    assert not ctx.foreground.active


async def test_with_the_setting_off_background_runs_during_a_reply(client, ctx, monkeypatch):
    book, provider = await setup(client, ctx, monkeypatch)
    await client.put("/api/settings", json={"background_yield": False})
    provider.hold = asyncio.Event()
    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await wait_for(lambda: provider.started == 1)
    ctx.foreground.enter()
    await asyncio.sleep(0.05)
    assert provider.cancelled == 0
    provider.hold.set()
    await settle_vectors()  # finishes while the "reply" is still busy
    assert provider.finished == 3
    ctx.foreground.leave()


async def test_paused_time_does_not_count_toward_the_rate_limit_budget(client, ctx, monkeypatch):
    book, provider = await setup(client, ctx, monkeypatch, blocks=1)
    monkeypatch.setattr(character_tree, "RATE_WAIT", 0.01)
    monkeypatch.setattr(character_tree, "MAX_WAIT", 0.035)  # room for only 3 waits
    provider.limit_first = 3
    ctx.foreground.enter()
    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await asyncio.sleep(0.3)  # paused far longer than MAX_WAIT: nothing asked, nothing spent
    assert provider.started == 0
    ctx.foreground.leave()
    await settle_vectors()
    status = await vstatus(client, book)
    assert status["current"] == 1 and status["job"]["error"] == ""


async def test_an_awaited_vectorize_is_not_gated_by_a_busy_run(client, ctx, monkeypatch):
    """fill_layers / the doctor may run inside a busy assistant run: waiting for idle would wait on itself."""
    book, provider = await setup(client, ctx, monkeypatch)
    ctx.foreground.enter()
    made = await asyncio.wait_for(character_tree.vectorize(ctx, [book["id"]]), 5)
    ctx.foreground.leave()
    assert made == 3 and provider.finished == 3 and provider.cancelled == 0
    assert (await vstatus(client, book))["current"] == 3
