"""Connection profiles over HTTP and fallbacks in a real chat (stage 6г)."""

from __future__ import annotations

import asyncio


async def connection(client, name: str, model: str, **params) -> dict:
    return (await client.post("/api/connections", json={
        "name": name, "base_url": "http://llm/v1", "model": model, "params": params})).json()


async def test_save_switch_and_apply_parts(client, ctx):
    a = await connection(client, "A", "model-a", temperature=0.5)
    b = await connection(client, "B", "model-b")
    await client.put("/api/settings", json={"active_connection": a["id"]})
    full = (await client.post("/api/profiles", json={"name": "Всё A"})).json()
    assert full["parts"] == {"connection": True, "model": True, "params": True, "preset": True}
    model_only = (await client.post("/api/profiles", json={
        "name": "Только модель", "parts": {"model": True}})).json()

    await client.put("/api/settings", json={"active_connection": b["id"]})
    await client.put(f"/api/connections/{a['id']}", json={"model": "changed", "params": {}})
    applied = (await client.post(f"/api/profiles/{full['id']}/apply")).json()
    assert applied["connection"]["id"] == a["id"] and applied["active"] == full["id"]
    assert applied["connection"]["model"] == "model-a" and applied["connection"]["params"] == {"temperature": 0.5}

    await client.put("/api/settings", json={"active_connection": b["id"]})
    applied = (await client.post(f"/api/profiles/{model_only['id']}/apply")).json()
    assert applied["connection"]["id"] == b["id"] and applied["connection"]["model"] == "model-a"

    assert (await client.post("/api/profiles", json={"name": "x", "parts": {}})).status_code == 400
    renamed = (await client.put(f"/api/profiles/{full['id']}", json={"name": "A!", "recapture": True})).json()
    assert renamed["name"] == "A!" and renamed["connection_id"] == b["id"]
    await client.delete(f"/api/connections/{b['id']}")
    assert (await client.post(f"/api/profiles/{full['id']}/apply")).status_code == 409
    await client.delete(f"/api/profiles/{full['id']}")
    listed = (await client.get("/api/profiles")).json()
    assert [p["name"] for p in listed["profiles"]] == ["Только модель"]


async def test_chat_falls_back_and_says_so(client, ctx, fake_llm):
    spare = await connection(client, "Запасной", "spare-model")
    main = await connection(client, "Основной", "main-model", fallbacks=[
        {"on": ["refused"], "connection": spare["id"], "model": "uncensored"}])
    await client.put("/api/settings", json={"active_connection": main["id"]})
    fake_llm.queue("Извините, но я не могу продолжить.", "The story goes on")
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "go"})).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    reply = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert reply["content"] == "The story goes on"
    assert reply["swipe_meta"]["via"] == "Запасной · uncensored"
    assert [r["model"] for r in fake_llm.requests] == ["main-model", "uncensored"]


async def test_bad_fallback_settings_are_refused(client):
    response = await client.post("/api/connections", json={
        "name": "x", "base_url": "http://llm/v1", "params": {"fallbacks": [{"on": ["rain"]}]}})
    assert response.status_code == 400 and "Запасной путь" in response.json()["error"]["message"]
