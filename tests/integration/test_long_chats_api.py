"""Wave 3 over HTTP: the reply's room comes off the window, and the model's own prompt count teaches the ratio that
turns the window into estimated tokens (docs/ST_PARITY.md section 10)."""

from __future__ import annotations

import asyncio

from tavern.core import tokens


async def setup(client, **params):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind. " * 120})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    active = (await client.get("/api/connections")).json()["active"]
    if params:
        assert (await client.put(f"/api/connections/{active}", json={"params": params})).status_code == 200
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return active, chat["id"]


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


async def seen(client, chat_id):
    reply = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    return (await client.get(f"/api/messages/{reply['id']}/prompt")).json()


async def test_the_reply_length_is_kept_free_and_the_ratio_is_learned(client, ctx, fake_llm):
    connection_id, chat_id = await setup(client, max_context_tokens=4000, max_tokens=500)
    fake_llm.token_scale = 1.5  # this «model» counts half again as many tokens as our estimate
    fake_llm.queue("Hello.")
    await send(client, ctx, chat_id, "hi")
    meta = (await seen(client, chat_id))["meta"]
    assert (meta["max_context_tokens"], meta["response_reserve"], meta["token_ratio"]) == (4000, 500, 1.0)
    learned = tokens.ratio(ctx.settings.get(tokens.RATIO_KEY), connection_id, "m")
    assert 1.45 <= learned <= 1.6  # learned from the service's own count: the next reply's window follows it

    fake_llm.queue("Again.")
    await send(client, ctx, chat_id, "more")
    assert (await seen(client, chat_id))["meta"]["token_ratio"] == learned


async def test_without_a_window_nothing_is_reserved(client, ctx, fake_llm):
    _, chat_id = await setup(client, max_tokens=500)
    fake_llm.queue("Hello.")
    await send(client, ctx, chat_id, "hi")
    meta = (await seen(client, chat_id))["meta"]
    assert (meta["max_context_tokens"], meta["response_reserve"]) == (0, 0)
