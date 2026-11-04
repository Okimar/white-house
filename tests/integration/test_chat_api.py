"""Chat flow over HTTP: send, stream events, stop, regenerate, errors, limits, custom generators."""

import asyncio
import json

import pytest

from tavern.core.generation import GenerationJob
from tavern.providers.base import ChatMessage

LLM_URL = "http://llm/v1"


def parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            events.append(json.loads(data[0]))
    return events


async def finish(ctx, run_id: str) -> None:
    """Wait for a background run to end (cancelled runs end with CancelledError, ignore it)."""
    run = ctx.runs.get(run_id)
    await asyncio.wait([run.task], timeout=10)
    assert run.finished


async def run_events(client, run_id: str) -> list[dict]:
    response = await client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200
    return parse_sse(response.text)


async def make_setup(client, base_url: str) -> dict:
    """A character with a greeting, an active connection and an opened chat."""
    char = (await client.post("/api/characters", json={
        "name": "Ann", "description": "{{char}} is kind.", "first_mes": "Hi {{user}}!",
        "alternate_greetings": ["Hello again."],
    })).json()
    conn = (await client.post("/api/connections", json={
        "name": "fake", "base_url": base_url, "model": "m", "api_key": "sk-secret-123",
    })).json()
    chat = (await client.post("/api/chats", json={"character_id": char["id"]})).json()
    return {"char": char, "conn": conn, "chat": chat["chat"], "opening": chat["messages"]}


@pytest.fixture
async def setup(client, fake_llm):
    """In-process fake LLM: fast, but responses arrive buffered (no incremental streaming)."""
    return await make_setup(client, LLM_URL)


@pytest.fixture
async def live_setup(client, live_llm):
    """Fake LLM on a real socket: needed when a test acts while a reply is still streaming."""
    return await make_setup(client, live_llm.base_url)


async def send(client, chat_id, text="hello"):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


async def messages(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]


async def test_new_chat_opens_with_greeting_and_alternates_as_swipes(setup):
    (greeting,) = setup["opening"]
    assert greeting["role"] == "assistant" and greeting["content"] == "Hi User!"
    assert greeting["swipe_count"] == 2


async def test_send_streams_and_saves_reply(client, ctx, fake_llm, setup):
    fake_llm.queue("Nice to meet you")
    chat_id = setup["chat"]["id"]
    run_id = await send(client, chat_id)
    await finish(ctx, run_id)

    events = await run_events(client, run_id)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Nice to meet you"

    chat_messages = await messages(client, chat_id)
    assert [(m["role"], m["content"]) for m in chat_messages[1:]] == [
        ("user", "hello"), ("assistant", "Nice to meet you")]

    sent = fake_llm.requests[0]
    assert sent["model"] == "m" and sent["stream"] is True
    assert sent["messages"][0]["role"] == "system" and "Ann is kind." in sent["messages"][0]["content"]
    assert sent["messages"][-1] == {"role": "user", "content": "hello"}


async def test_thinking_written_in_the_reply_goes_to_its_reasoning(client, ctx, fake_llm, setup):
    fake_llm.queue("<thinking>Plan: wave back</thinking>\n\nShe waves.")
    chat_id = setup["chat"]["id"]
    run_id = await send(client, chat_id)
    await finish(ctx, run_id)
    events = await run_events(client, run_id)
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "She waves."
    assert "".join(e["text"] for e in events if e["type"] == "reasoning") == "Plan: wave back"
    reply = (await messages(client, chat_id))[-1]
    assert reply["content"] == "She waves." and reply["swipe_meta"]["reasoning"] == "Plan: wave back"
    assert "split_thinking" not in fake_llm.requests[0]


async def test_thinking_display_setting_takes_only_known_values(client):
    assert (await client.put("/api/settings", json={"thinking_display": "open"})).status_code == 200
    assert (await client.put("/api/settings", json={"thinking_display": "sideways"})).status_code == 400


async def test_api_key_is_never_returned(client, setup):
    listing = (await client.get("/api/connections")).json()
    assert listing["connections"][0]["has_key"] is True
    assert "sk-secret-123" not in json.dumps(listing)
    assert "sk-secret-123" not in (await client.get("/api/settings")).text


async def test_regenerate_adds_a_swipe_to_the_last_reply(client, ctx, fake_llm, setup):
    chat_id = setup["chat"]["id"]
    fake_llm.queue("first", "second")
    await finish(ctx, await send(client, chat_id))
    response = await client.post(f"/api/chats/{chat_id}/regenerate")
    await finish(ctx, response.json()["run_id"])

    reply = (await messages(client, chat_id))[-1]
    assert (reply["content"], reply["swipe_count"], reply["active_swipe"]) == ("second", 2, 1)
    # the regenerated prompt must not contain the discarded reply
    assert all(m["content"] != "first" for m in fake_llm.requests[1]["messages"])


async def test_provider_error_keeps_user_message_and_drops_empty_reply(client, ctx, fake_llm, setup):
    chat_id = setup["chat"]["id"]
    fake_llm.fail_next(500, "model exploded")
    run_id = await send(client, chat_id)
    await finish(ctx, run_id)

    error = [e for e in await run_events(client, run_id) if e["type"] == "error"][0]
    assert error["error"]["code"] == "provider_error" and "model exploded" in error["error"]["message"]
    assert [m["role"] for m in await messages(client, chat_id)] == ["assistant", "user"]
    assert ctx.runs.active(chat_id) is None  # chat is free again


async def test_regenerate_failure_restores_previous_swipe(client, ctx, fake_llm, setup):
    chat_id = setup["chat"]["id"]
    fake_llm.queue("keep me")
    await finish(ctx, await send(client, chat_id))
    fake_llm.fail_next(500, "boom")
    response = await client.post(f"/api/chats/{chat_id}/regenerate")
    await finish(ctx, response.json()["run_id"])
    reply = (await messages(client, chat_id))[-1]
    assert (reply["content"], reply["swipe_count"]) == ("keep me", 1)


async def test_stop_keeps_partial_text(client, ctx, live_llm, live_setup):
    live_llm.chunk_delay = 0.05
    live_llm.queue(" ".join(f"w{i}" for i in range(100)))
    chat_id = live_setup["chat"]["id"]
    run_id = await send(client, chat_id)
    run = ctx.runs.get(run_id)
    for _ in range(200):  # wait for a few deltas
        if sum(e["type"] == "delta" for e in run.events) >= 3:
            break
        await asyncio.sleep(0.01)
    assert (await client.post(f"/api/chats/{chat_id}/stop")).json() == {"stopped": True}
    await finish(ctx, run_id)

    assert run.events[-1]["type"] == "stopped"
    reply = (await messages(client, chat_id))[-1]
    assert reply["content"].startswith("w0 w1") and len(reply["content"].split()) < 100
    assert (await client.post(f"/api/chats/{chat_id}/stop")).json() == {"stopped": False}


async def test_second_send_while_running_is_rejected(client, ctx, live_llm, live_setup):
    live_llm.chunk_delay = 0.05
    live_llm.queue(" ".join(["slow"] * 50))
    chat_id = live_setup["chat"]["id"]
    run_id = await send(client, chat_id)
    again = await client.post(f"/api/chats/{chat_id}/send", json={"text": "more"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "busy"
    assert len(await messages(client, chat_id)) == 3  # greeting, user, reply: nothing extra added
    ctx.runs.stop(chat_id)
    await finish(ctx, run_id)


async def test_send_without_connection_changes_nothing(client, ctx, fake_llm):
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    response = await client.post(f"/api/chats/{chat['id']}/send", json={"text": "hi"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_connection"
    assert await messages(client, chat["id"]) == []


async def test_empty_message_rejected(client, setup):
    response = await client.post(f"/api/chats/{setup['chat']['id']}/send", json={"text": "   "})
    assert response.status_code == 400


async def test_reconnect_resumes_after_last_event_id(client, ctx, fake_llm, setup):
    fake_llm.queue("a b c d")
    run_id = await send(client, setup["chat"]["id"])
    await finish(ctx, run_id)
    everything = (await client.get(f"/api/runs/{run_id}/events")).text
    resumed = (await client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "2"})).text
    all_events, tail = parse_sse(everything), parse_sse(resumed)
    assert tail == all_events[3:]


async def test_pagination_of_long_chat(client, ctx, setup):
    chat_id = setup["chat"]["id"]
    for i in range(30):
        ctx.chats.add_message(chat_id, role="user", content=f"m{i}",
                              parent_id=ctx.chats.get_chat(chat_id)["leaf_id"])
    page = (await client.get(f"/api/chats/{chat_id}", params={"limit": 10})).json()
    assert [m["content"] for m in page["messages"]][-1] == "m29" and len(page["messages"]) == 10
    assert page["total"] == 31 and page["has_more"] is True
    older = (await client.get(f"/api/chats/{chat_id}/messages",
                              params={"limit": 100, "before_depth": page["messages"][0]["depth"]})).json()
    assert len(older["messages"]) == 21 and older["has_more"] is False


async def test_edit_swipe_and_delete_messages(client, ctx, fake_llm, setup):
    chat_id = setup["chat"]["id"]
    greeting = setup["opening"][0]
    edited = await client.put(f"/api/messages/{greeting['id']}", json={"content": "Welcome!"})
    assert edited.json()["content"] == "Welcome!"
    swiped = await client.put(f"/api/messages/{greeting['id']}/swipe", json={"index": 1})
    assert swiped.json()["content"] == "Hello again."
    assert (await client.put(f"/api/messages/{greeting['id']}/swipe", json={"index": 9})).status_code == 409
    deleted = await client.delete(f"/api/messages/{greeting['id']}")
    assert deleted.json()["chat"]["leaf_id"] is None
    assert await messages(client, chat_id) == []


# --- pipeline replacement, budget, trace (foundation for agentic plugins) --------------------


class TwoStepGenerator:
    """Plugin-style generator: a hidden 'thinking' call, then the final answer built from it."""

    id = "two-step"

    async def generate(self, job: GenerationJob):
        job.run.trace("step", name="think")
        thought = await job.llm.generate([ChatMessage("user", "think about the plot")])
        job.run.trace("step", name="write", thought=thought.text)
        messages = await job.build_messages()
        messages.append(ChatMessage("system", f"Plan: {thought.text}"))
        async for event in job.llm.stream(messages):
            if event.kind == "text":
                yield event.text


async def test_plugin_generator_replaces_pipeline_and_traces_progress(client, ctx, fake_llm, setup):
    ctx.generators.register(TwoStepGenerator())
    await client.put(f"/api/chats/{setup['chat']['id']}/meta", json={"generator": "two-step"})
    fake_llm.queue("secret thought", "final answer")
    run_id = await send(client, setup["chat"]["id"])
    await finish(ctx, run_id)

    events = await run_events(client, run_id)
    traces = [e for e in events if e["type"] == "trace" and e["kind"] == "step"]
    assert [t["name"] for t in traces] == ["think", "write"]
    assert (await messages(client, setup["chat"]["id"]))[-1]["content"] == "final answer"
    assert fake_llm.requests[1]["messages"][-1]["content"] == "Plan: secret thought"
    done = events[-1]
    assert done["type"] == "done" and done["budget"]["steps"] == 2


async def test_step_budget_stops_runaway_plugin(client, ctx, fake_llm, setup):
    ctx.generators.register(TwoStepGenerator())
    await client.put("/api/settings", json={"generator": "two-step", "run_budget": {"max_steps": 1}})
    run_id = await send(client, setup["chat"]["id"])
    await finish(ctx, run_id)

    error = [e for e in await run_events(client, run_id) if e["type"] == "error"][0]
    assert error["error"]["code"] == "budget_exceeded"
    assert error["error"]["details"]["limit"] == "steps"
    assert len(fake_llm.requests) == 1  # the second call was never sent


async def test_unknown_generator_falls_back_to_default(client, ctx, fake_llm, setup):
    await client.put(f"/api/chats/{setup['chat']['id']}/meta", json={"generator": "removed-plugin"})
    fake_llm.queue("still works")
    await finish(ctx, await send(client, setup["chat"]["id"]))
    assert (await messages(client, setup["chat"]["id"]))[-1]["content"] == "still works"


async def test_messages_excluded_from_prompt_are_not_sent_but_stay_stored(client, ctx, fake_llm, setup):
    chat_id = setup["chat"]["id"]
    ctx.chats.add_message(chat_id, role="system", content="agent scratchpad", hidden=True,
                          exclude_from_prompt=True, owner="agents",
                          parent_id=ctx.chats.get_chat(chat_id)["leaf_id"])
    await finish(ctx, await send(client, chat_id))
    sent = json.dumps(fake_llm.requests[0]["messages"])
    assert "agent scratchpad" not in sent
    assert any(m["owner"] == "agents" for m in await messages(client, chat_id))
