"""Stage B: the vector counter (current / stale / missing), «Автовекторизация», and no vectors in files."""

from __future__ import annotations

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

    assert (await client.post(f"/api/lorebooks/{book['id']}/vectorize")).json()["made"] == 3
    data = await status(client, book)
    assert data["vector_status"]["current"] == 3 and len(data["vectorized"]) == 3

    first = data["blocks"][0]
    await client.put(f"/api/blocks/{first['id']}", json={"content": "Другой текст"})
    s = (await status(client, book))["vector_status"]
    assert (s["current"], s["stale"], s["missing"]) == (2, 1, 0)

    await client.post(f"/api/lorebooks/{book['id']}/vectorize")
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
