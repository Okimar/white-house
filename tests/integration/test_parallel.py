"""Several requests at once (stage 6д): variants, first wins, pick, failures, and the helpers."""

from __future__ import annotations

import asyncio
import json

import pytest

from tavern.core.llm import LLMClient
from tavern.providers.base import ChatMessage, ProviderError, StreamEvent
from tavern.services import parallel


async def setup(client, **plan) -> tuple[dict, dict]:
    main = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "main-model", "params": {"temperature": 0.3}})).json()
    other = (await client.post("/api/connections", json={
        "name": "Other", "service": "custom_gemini", "base_url": "http://llm/v1beta", "model": "fake-model",
        "params": {"thinking_budget": 64}})).json()
    await client.put("/api/settings", json={"active_connection": main["id"]})
    targets = plan.pop("targets", [{"connection": "", "model": "alt-model", "count": 2},
                                   {"connection": other["id"]}])
    saved = await client.put("/api/parallel", json={"enabled": True, "targets": targets, **plan})
    assert saved.status_code == 200, saved.text
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    return chat, other


async def send(client, ctx, chat) -> list[dict]:
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "hi"})).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    text = (await client.get(f"/api/runs/{run_id}/events")).text
    return [json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")]


async def test_settings_are_checked(client):
    assert (await client.get("/api/parallel")).json() == {"enabled": False, "mode": "variants", "targets": []}
    for bad in ({"mode": "all"}, {"targets": [{"count": 0}]}, {"targets": [{"count": 5}, {"count": 5}]}):
        assert (await client.put("/api/parallel", json=bad)).status_code == 400
    assert (await client.put("/api/parallel", json={"targets": [{"connection": "gone"}]})).status_code == 404


async def test_variants_become_versions_and_the_main_one_stays(client, ctx, fake_llm):
    chat, _ = await setup(client)
    events = await send(client, ctx, chat)
    reply = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert reply["swipe_count"] == 4 and reply["active_swipe"] == 0
    swipes = (await client.get(f"/api/messages/{reply['id']}/swipes")).json()["swipes"]
    assert [s["meta"]["via"] for s in swipes] == [
        "Main · main-model (основной)", "Main · alt-model #1", "Main · alt-model #2", "Other · fake-model"]
    assert all(s["content"] == "Echo: hi" for s in swipes)
    # Each request goes with its own connection's settings.
    assert sorted(r["model"] for r in fake_llm.requests) == ["alt-model", "alt-model", "main-model"]
    assert all(r["temperature"] == 0.3 for r in fake_llm.requests)
    assert fake_llm.gemini_requests[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 64}
    assert events[-1]["type"] == "done" and events[-1]["pick"] is False


async def test_pick_mode_says_so(client, ctx, fake_llm):
    chat, _ = await setup(client, mode="pick", targets=[{"connection": "", "count": 1}])
    events = await send(client, ctx, chat)
    assert events[-1]["pick"] is True and events[-1]["message"]["swipe_count"] == 2


async def test_first_wins_keeps_one_version(client, ctx, fake_llm):
    chat, _ = await setup(client, mode="first")
    await send(client, ctx, chat)
    reply = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert reply["swipe_count"] == 1 and reply["content"] == "Echo: hi"
    assert reply["swipe_meta"]["via"] in {"Main · main-model", "Main · alt-model #1", "Main · alt-model #2",
                                          "Other · fake-model"}


async def test_a_failed_variant_does_not_sink_the_reply(client, ctx, fake_llm):
    bad = (await client.post("/api/connections", json={
        "name": "Bad", "base_url": "http://llm/v1", "model": "m", "api_key": "sk-bad"})).json()
    fake_llm.key_status = {"sk-bad": 401}
    chat, _ = await setup(client, targets=[{"connection": bad["id"]}])
    events = await send(client, ctx, chat)
    failed = next(e for e in events if e["type"] == "variants_failed")
    assert failed["failed"][0]["label"] == "Bad · m" and "sk-bad" not in json.dumps(failed)
    reply = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert reply["swipe_count"] == 1 and reply["content"] == "Echo: hi"


async def test_off_sends_one_request(client, ctx, fake_llm):
    chat, _ = await setup(client)
    await client.put("/api/parallel", json={"enabled": False, "targets": []})
    await send(client, ctx, chat)
    assert len(fake_llm.requests) == 1


class Scripted:
    """A provider that yields given events after a delay, or fails."""

    kind = "test"

    def __init__(self, events, delay=0.0, fail=False):
        self.events, self.delay, self.fail = events, delay, fail

    async def stream(self, request):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ProviderError("broken", reason="server")
        for event in self.events:
            yield event


def events(*texts):
    return [*(StreamEvent("text", t) for t in texts), StreamEvent("done")]


async def test_race_takes_the_first_writer():
    slow = Scripted(events("slow"), delay=0.2)
    fast = Scripted([StreamEvent("reasoning", "hm"), *events("fast", "!")], delay=0.01)
    out = [(label, e.kind, e.text) async for label, e in parallel.race([("slow", slow.stream(None)),
                                                                          ("fast", fast.stream(None))])]
    assert out == [("fast", "reasoning", "hm"), ("fast", "text", "fast"), ("fast", "text", "!"), ("fast", "done", "")]


async def test_race_fails_only_when_everyone_fails():
    broken = Scripted([], fail=True)
    late = Scripted(events("ok"), delay=0.05)
    assert [e.text async for _, e in parallel.race([("a", broken.stream(None)), ("b", late.stream(None))])] == ["ok", ""]
    with pytest.raises(ProviderError):
        async for _ in parallel.race([("a", Scripted([], fail=True).stream(None))]):
            pass


async def test_gather_keeps_order_and_errors():
    llm = LLMClient(Scripted(events("main")))
    targets = [parallel.Target(Scripted(events("one")), "m1", "one"),
               parallel.Target(Scripted([], fail=True), "m2", "two")]
    results = await parallel.gather(llm, targets, [ChatMessage("user", "q")])
    assert [(r.label, r.text, r.error) for r in results] == [("one", "one", ""), ("two", "", "broken")]
