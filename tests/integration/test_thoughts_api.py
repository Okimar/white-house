"""Stage 8в: the 🌳 tree of thoughts writing replies through the HTTP API on the fake model:
model access, live progress, thoughts kept with the version and never sent again, rounds, a failing
thinker, the plugin's per-reply limit."""

from __future__ import annotations

import asyncio
import json


async def chat_with_thoughts(client) -> dict:
    conn = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "fake-model"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    assert (await client.post("/api/plugins/thoughts/enable", json={})).status_code == 200
    assert (await client.put(f"/api/chats/{chat['id']}/meta", json={"generator": "thoughts"})).status_code == 200
    return chat


async def send(client, ctx, chat: dict, text: str = "привет") -> list[dict]:
    response = await client.post(f"/api/chats/{chat['id']}/send", json={"text": text})
    run_id = response.json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    body = (await client.get(f"/api/runs/{run_id}/events")).text
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def thoughts_of(events: list[dict]) -> list[list[dict]]:
    return events[-1]["message"]["swipe_meta"]["thoughts"]["rounds"]


async def test_thinking_needs_model_access(client, ctx, fake_llm):
    chat = await chat_with_thoughts(client)
    events = await send(client, ctx, chat)
    assert events[-1]["type"] == "error" and events[-1]["error"]["code"] == "plugin_no_models"
    assert fake_llm.requests == []


async def test_thinkers_think_then_the_reply_is_written(client, ctx, fake_llm):
    chat = await chat_with_thoughts(client)
    await client.put("/api/plugins/thoughts/access", json={"models": True})
    events = await send(client, ctx, chat)
    assert events[-1]["type"] == "done"
    assert events[-1]["message"]["content"] == "Echo: привет"  # the closing note is a system turn

    [round_] = thoughts_of(events)
    assert [(t["name"], t["state"]) for t in round_] == [("Мир", "done"), ("Сюжет", "done")]
    assert round_[0]["text"].startswith("Echo: Сейчас ты — «Мир»")

    live = [e for e in events if e["type"] == "trace" and e.get("plugin") == "thoughts"]
    assert [(e["kind"], e.get("state")) for e in live[:2]] == [("thought", "wait"), ("thought", "wait")]
    assert sorted(e["name"] for e in live if e.get("state") == "done") == ["Мир", "Сюжет"]
    assert live[-1]["kind"] == "thought_reply"

    final = fake_llm.requests[-1]["messages"]
    assert final[-1]["role"] == "system" and "[Мир]: Echo:" in final[-1]["content"]
    rows = ctx.db.fetchall("SELECT purpose FROM spending")
    assert [r["purpose"] for r in rows] == ["plugin:thoughts"] * 3

    # The next reply: the earlier thoughts stay with their version and never reach the model again.
    count = len(fake_llm.requests)
    await send(client, ctx, chat, "дальше")
    for request in fake_llm.requests[count:]:
        assert not any("Сейчас ты — «Мир»" in str(m.get("content")) for m in request["messages"][:-1])


async def test_rounds_see_each_other_and_custom_thinkers(client, ctx, fake_llm):
    chat = await chat_with_thoughts(client)
    await client.put("/api/plugins/thoughts/access", json={"models": True})
    assert (await client.put("/api/plugins/thoughts/settings", json={
        "rounds": 2, "thinkers": [{"name": "Критик", "task": "ищи слабые места"}]})).status_code == 200
    events = await send(client, ctx, chat)
    rounds = thoughts_of(events)
    assert [[t["name"] for t in r] for r in rounds] == [["Критик"], ["Критик"]]
    thinker_requests = [r["messages"][-1]["content"] for r in fake_llm.requests[:2]]
    assert "ищи слабые места" in thinker_requests[0] and "прошлом круге" not in thinker_requests[0]
    # Alone, the critic has no one else to read in round two.
    assert "прошлом круге" not in thinker_requests[1]
    assert len(fake_llm.requests) == 3


async def test_a_failing_thinker_is_marked_and_the_reply_goes_on(client, ctx, fake_llm):
    chat = await chat_with_thoughts(client)
    await client.put("/api/plugins/thoughts/access", json={"models": True})
    fake_llm.fail_next(400, "плохой запрос")
    events = await send(client, ctx, chat)
    assert events[-1]["type"] == "done" and events[-1]["message"]["content"] == "Echo: привет"
    states = sorted(t["state"] for t in thoughts_of(events)[0])
    assert states == ["done", "error"]

    await client.put("/api/plugins/thoughts/settings", json={"thinkers": [{"name": "Один"}]})
    fake_llm.fail_next(400, "плохой запрос")
    events = await send(client, ctx, chat)  # every thinker failed: no reply without thoughts
    assert events[-1]["type"] == "error" and "Ни один думающий" in events[-1]["error"]["message"]


async def test_the_per_reply_limit_ends_the_reply(client, ctx, fake_llm):
    chat = await chat_with_thoughts(client)
    await client.put("/api/plugins/thoughts/access", json={"models": True, "per_reply": 2})
    events = await send(client, ctx, chat)  # two thinkers + the reply = three requests
    assert events[-1]["type"] == "error" and events[-1]["error"]["code"] == "plugin_limit"
