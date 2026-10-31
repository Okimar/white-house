"""Spending (stage 6е): every request recorded, prices (service's and own), sums, limits."""

from __future__ import annotations

import asyncio
import json

from tavern.core.llm import LLMClient
from tavern.providers.base import ChatMessage, StreamEvent, Usage
from tavern.services import spending

# The fake's model list: $1 per million prompt tokens, $2 per million completion tokens.


async def setup(client, ctx, *, inspect=True, **params) -> tuple[dict, dict]:
    conn = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "fake-model", "params": params})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    if inspect:
        assert (await client.post(f"/api/connections/{conn['id']}/inspect")).status_code == 200
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    return conn, chat


async def follow(client, ctx, response) -> list[dict]:
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    body = (await client.get(f"/api/runs/{run_id}/events")).text
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


async def send(client, ctx, chat, text="hi") -> list[dict]:
    return await follow(client, ctx, await client.post(f"/api/chats/{chat['id']}/send", json={"text": text}))


async def last_reply(client, chat) -> dict:
    return (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]


async def test_a_reply_is_recorded_with_the_service_price(client, ctx, fake_llm):
    _, chat = await setup(client, ctx)
    await send(client, ctx, chat)
    reply = await last_reply(client, chat)
    usage = reply["swipe_meta"]["usage"]
    assert usage["prompt_tokens"] > 0 and usage["completion_tokens"] > 0 and not usage["estimated"]
    expected = (usage["prompt_tokens"] * 1 + usage["completion_tokens"] * 2) / 1_000_000
    assert abs(usage["cost"] - expected) < 1e-9
    summary = (await client.get(f"/api/spending?chat={chat['id']}")).json()
    assert summary["chat"]["requests"] == 1 and abs(summary["chat"]["cost"] - expected) < 1e-9
    assert summary["day"]["requests"] == summary["month"]["requests"] == 1 and summary["over"] == ""
    per_message = (await client.get(f"/api/messages/{reply['id']}/spending")).json()
    assert per_message["prompt_tokens"] == usage["prompt_tokens"]
    report = (await client.get("/api/spending/report")).json()
    assert report["models"][0]["model"] == "fake-model" and report["models"][0]["price"]["source"] == "service"
    assert report["models"][0]["connection_name"] == "Main" and len(report["days"]) == 1


async def test_own_price_wins_and_unknown_price_counts_tokens_only(client, ctx, fake_llm):
    _, chat = await setup(client, ctx, inspect=False)
    await send(client, ctx, chat)
    usage = (await last_reply(client, chat))["swipe_meta"]["usage"]
    assert usage["cost"] is None and usage["unpriced"] and usage["prompt_tokens"] > 0
    saved = await client.put("/api/spending/settings", json={"prices": {"fake-model": {"in": 1000, "out": 0}}})
    assert saved.status_code == 200 and saved.json()["prices"]["fake-model"] == {"in": 1000.0, "out": 0.0}
    await follow(client, ctx, await client.post(f"/api/chats/{chat['id']}/regenerate"))
    usage = (await last_reply(client, chat))["swipe_meta"]["usage"]
    assert abs(usage["cost"] - usage["prompt_tokens"] * 1000 / 1_000_000) < 1e-9
    assert (await client.get(f"/api/spending?chat={chat['id']}")).json()["chat"]["unpriced"] == 1


async def test_limit_warns_or_stops(client, ctx, fake_llm):
    _, chat = await setup(client, ctx)
    await client.put("/api/spending/settings", json={
        "limits": {"day": 0.000001}, "currency": {"code": "₽", "rate": 100}})
    await send(client, ctx, chat)  # nothing spent before this one
    events = await send(client, ctx, chat)
    warning = next(e for e in events if e["type"] == "spend_warning")
    assert "день" in warning["text"] and "₽" in warning["text"]
    assert events[-1]["type"] == "done"
    await client.put("/api/spending/settings", json={
        "limits": {"day": 0.000001}, "currency": {"code": "₽", "rate": 100}, "on_limit": "stop"})
    before = len((await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"])
    response = await client.post(f"/api/chats/{chat['id']}/send", json={"text": "more"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "spend_limit"
    assert len((await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"]) == before
    assert (await client.post(f"/api/chats/{chat['id']}/regenerate")).status_code == 409
    assert (await client.get("/api/spending")).json()["over"] == "day"


async def test_variants_carry_their_own_cost(client, ctx, fake_llm):
    _, chat = await setup(client, ctx)
    await client.put("/api/parallel", json={"enabled": True, "targets": [{"connection": "", "count": 2}]})
    await send(client, ctx, chat)
    reply = await last_reply(client, chat)
    swipes = (await client.get(f"/api/messages/{reply['id']}/swipes")).json()["swipes"]
    assert len(swipes) == 3 and all(s["meta"]["usage"]["requests"] == 1 for s in swipes)
    assert (await client.get(f"/api/messages/{reply['id']}/spending")).json()["requests"] == 3


async def test_a_fallback_is_priced_as_the_model_that_answered(client, ctx, fake_llm):
    _, chat = await setup(client, ctx, fallbacks=[{"on": ["refused"], "model": "cheap"}])
    await client.put("/api/spending/settings", json={"prices": {"cheap": {"in": 0, "out": 0}}})
    fake_llm.queue("I'm sorry, but I can't continue.", "The story goes on")
    await send(client, ctx, chat)
    usage = (await last_reply(client, chat))["swipe_meta"]["usage"]
    assert usage["cost"] > 0 and usage["requests"] == 2  # the refusal was paid for too
    rows = ctx.db.fetchall("SELECT model, cost FROM spending ORDER BY id")
    assert [r["model"] for r in rows] == ["fake-model", "cheap"] and rows[1]["cost"] == 0


async def test_settings_are_checked(client):
    assert (await client.get("/api/spending/settings")).json() == spending.defaults()
    for bad in ({"show": {"weather": True}}, {"limits": {"day": -1}}, {"on_limit": "panic"},
                {"currency": {"code": "₽"}}, {"prices": {"m": {"in": "a lot"}}}):
        assert (await client.put("/api/spending/settings", json=bad)).status_code == 400, bad
    ok = (await client.put("/api/spending/settings", json={"show": {"message": True}})).json()
    assert ok["show"] == {"message": True, "chat": False, "day": False, "month": False}


class Scripted:
    kind = "test"

    def __init__(self, *events: StreamEvent, hang: bool = False) -> None:
        self.events, self.hang = events, hang

    async def stream(self, request):
        for event in self.events:
            yield event
        if self.hang:
            await asyncio.sleep(10)


async def test_meter_sees_reported_usage_and_stopped_replies():
    seen: list[tuple[str, Usage, bool]] = []

    def meter(model, usage, estimated, provider):
        seen.append((model, usage, estimated))
        return 0.5

    llm = LLMClient(Scripted(StreamEvent("text", "ok"), StreamEvent("done", usage=Usage(10, 3))),
                    model="m", meter=meter)
    assert [e.kind async for e in llm.stream([ChatMessage("user", "q")])] == ["text", "done"]
    assert seen == [("m", Usage(10, 3), False)] and llm.budget.cost == 0.5

    stopped = llm.fork(provider=Scripted(StreamEvent("text", "a" * 40), hang=True))

    async def consume():
        async for _ in stopped.stream([ChatMessage("user", "q" * 400)]):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert seen[-1] == ("m", Usage(100, 10), True)
