"""Stage B: the vector counter (current / stale / missing), «Автовекторизация», and no vectors in files."""

from __future__ import annotations

from tests.conftest import settle_vectors

import asyncio
import json

from tavern.services import character_tree


async def setup(client):
    conn = (await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})).json()
    book = (await client.post("/api/lorebooks", json={"name": "Мир"})).json()
    for n in range(3):
        r = await client.post(f"/api/lorebooks/{book['id']}/blocks",
                              json={"title": f"П{n}", "content": f"Текст {n}", "keywords": ["k"], "mode": "search"})
        assert r.status_code == 201, r.text
    return conn, book


async def status(client, book):
    return (await client.get(f"/api/lorebooks/{book['id']}/blocks")).json()


async def test_counter_current_stale_missing_and_model_change(client, fake_llm):
    conn, book = await setup(client)
    assert (await status(client, book))["vector_status"]["connected"] is False
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    s = (await status(client, book))["vector_status"]
    assert (s["total"], s["current"], s["stale"], s["missing"]) == (3, 0, 0, 3)

    assert (await client.post(f"/api/lorebooks/{book['id']}/vectorize")).json()["todo"] == 3
    await settle_vectors()
    data = await status(client, book)
    assert data["vector_status"]["current"] == 3 and len(data["vectorized"]) == 3

    first = data["blocks"][0]
    await client.put(f"/api/blocks/{first['id']}", json={"content": "Другой текст"})
    s = (await status(client, book))["vector_status"]
    assert (s["current"], s["stale"], s["missing"]) == (2, 1, 0)

    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await settle_vectors()
    other = (await client.post("/api/connections", json={"name": "f2", "base_url": "http://llm/v1", "model": "m2"})).json()
    await client.put("/api/settings", json={"embedding_connection": other["id"]})
    s = (await status(client, book))["vector_status"]
    assert (s["current"], s["missing"]) == (0, 3)


async def test_auto_on_vectorizes_an_edit_in_the_background(client, fake_llm, monkeypatch):
    monkeypatch.setattr(character_tree, "AUTO_VECTOR_DELAY", 0.05)
    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"], "auto_vectors": True})
    first = (await status(client, book))["blocks"][0]
    await client.put(f"/api/blocks/{first['id']}", json={"content": "Новый текст"})
    for _ in range(100):
        if (await status(client, book))["vector_status"]["current"] == 3:
            break
        await asyncio.sleep(0.05)
    assert (await status(client, book))["vector_status"]["current"] == 3


async def test_auto_off_spends_nothing(client, ctx, fake_llm, monkeypatch):
    monkeypatch.setattr(character_tree, "AUTO_VECTOR_DELAY", 0.01)
    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    first = (await status(client, book))["blocks"][0]
    await client.put(f"/api/blocks/{first['id']}", json={"content": "Новый текст"})
    await asyncio.sleep(0.3)
    assert fake_llm.embedding_requests == []
    assert (await status(client, book))["vector_status"]["missing"] == 3


async def test_exports_carry_no_vectors_and_auto_import_vectorizes(client, fake_llm, monkeypatch):
    monkeypatch.setattr(character_tree, "AUTO_VECTOR_DELAY", 0.05)
    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await settle_vectors()
    exported = (await client.get(f"/api/lorebooks/{book['id']}/export.json")).text
    assert "vector" not in exported.lower() and "embedding" not in exported.lower()

    await client.put("/api/settings", json={"auto_vectors": True})
    fake_llm.embedding_requests.clear()
    new = (await client.post("/api/lorebooks/import", json=json.loads(exported))).json()
    for _ in range(100):
        if (await status(client, new))["vector_status"]["current"] == 3:
            break
        await asyncio.sleep(0.05)
    assert (await status(client, new))["vector_status"]["current"] == 3


async def test_light_status_route_has_no_blocks(client, fake_llm):
    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    data = (await client.get(f"/api/lorebooks/{book['id']}/vector-status")).json()
    assert set(data) == {"vectorized", "vector_status"} and data["vector_status"]["missing"] == 3


async def test_vectorize_splits_requests_and_waits_out_a_rate_limit(client, ctx, fake_llm, monkeypatch):
    """A free Voyage account: 429 «reduced rate limits» (3 requests, 10K tokens a minute). Small requests, wait, ask again."""
    from tavern.providers.base import ProviderError

    monkeypatch.setattr(character_tree, "BATCH_TOKENS", 5)  # every bubble is a request of its own
    monkeypatch.setattr(character_tree, "RATE_WAIT", 0.01)
    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    calls: list[int] = []

    class Stub:
        async def embed(self, texts, *, model=""):
            calls.append(len(texts))
            if len(calls) % 2 == 1:
                raise ProviderError("reduced rate limits", reason="limit", upstream_status=429)
            return [[1.0, 0.0] for _ in texts]

        async def aclose(self):
            pass

    monkeypatch.setattr(ctx.providers, "create", lambda *a, **k: Stub())
    assert (await client.post(f"/api/lorebooks/{book['id']}/vectorize")).json()["todo"] == 3
    await settle_vectors()
    assert calls == [1] * 6  # three requests, each refused once and then accepted
    assert (await status(client, book))["vector_status"]["current"] == 3


async def test_vectorize_gives_up_after_the_wait_limit_and_keeps_progress(client, ctx, fake_llm, monkeypatch):
    from tavern.providers.base import ProviderError

    monkeypatch.setattr(character_tree, "BATCH_TOKENS", 5)
    monkeypatch.setattr(character_tree, "RATE_WAIT", 0.01)
    monkeypatch.setattr(character_tree, "MAX_WAIT", 0.03)
    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    calls: list[int] = []

    class Stub:
        async def embed(self, texts, *, model=""):
            calls.append(1)
            if len(calls) > 1:  # the first bubble goes through, then the limit never lifts
                raise ProviderError("reduced rate limits", reason="limit", upstream_status=429)
            return [[1.0, 0.0] for _ in texts]

        async def aclose(self):
            pass

    monkeypatch.setattr(ctx.providers, "create", lambda *a, **k: Stub())
    assert (await client.post(f"/api/lorebooks/{book['id']}/vectorize")).json()["started"] is True
    await settle_vectors()
    s = (await client.get(f"/api/lorebooks/{book['id']}/vector-status")).json()["vector_status"]
    assert s["current"] == 1 and s["missing"] == 2  # progress kept
    assert s["job"]["running"] is False and "Лимит Voyage" in s["job"]["error"]


async def test_reply_time_meaning_search_does_not_wait_on_a_rate_limit(client, ctx, fake_llm, monkeypatch):
    from tavern.providers.base import ProviderError

    conn, book = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
    await settle_vectors()
    first = (await status(client, book))["blocks"][0]
    await client.put(f"/api/blocks/{first['id']}", json={"content": "Изменено"})
    monkeypatch.setattr(character_tree, "RATE_WAIT", 60)  # would hang the test if the reply waited

    class Stub:
        async def embed(self, texts, *, model=""):
            raise ProviderError("reduced rate limits", reason="limit", upstream_status=429)

        async def aclose(self):
            pass

    monkeypatch.setattr(ctx.providers, "create", lambda *a, **k: Stub())
    blocks = ctx.blocks.list(book["id"])
    assert await asyncio.wait_for(character_tree.meaning_scores(ctx, blocks, "запрос"), 5) == {}
