"""Message roles: saving a message as user/assistant/system, changing roles one by one, by numbers
or for the whole branch, and prompts that every backend accepts (no conversation without a user turn)."""

from __future__ import annotations

import asyncio

import pytest

from tavern.core.prompt import START_TURN, build_prompt
from tavern.errors import AppError
from tavern.services.chat import parse_numbers


async def setup_chat(client, base_url="http://llm/v1"):
    character = (await client.post("/api/characters", json={"name": "Ann", "first_mes": "Opening"})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": base_url, "model": "m"})
    return (await client.post("/api/chats", json={"character_id": character["id"]})).json()["chat"]["id"]


async def finish(ctx, run_id: str) -> None:
    run = ctx.runs.get(run_id)
    await asyncio.wait([run.task], timeout=10)
    assert run.finished


async def path(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]


async def test_message_is_saved_with_chosen_role_without_generation(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    for role, text in (("system", "Night falls."), ("assistant", "Ann waves."), ("user", "Hi")):
        response = await client.post(f"/api/chats/{chat_id}/messages", json={"text": text, "role": role})
        assert response.status_code == 201, response.text
        assert response.json()["role"] == role
    messages = await path(client, chat_id)
    assert [(m["role"], m["content"]) for m in messages] == [
        ("assistant", "Opening"), ("system", "Night falls."), ("assistant", "Ann waves."), ("user", "Hi"),
    ]
    assert messages[2]["name"] == "Ann"
    assert fake_llm.requests == []  # nothing was generated


async def test_unknown_role_and_empty_text_are_rejected(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    bad = await client.post(f"/api/chats/{chat_id}/messages", json={"text": "x", "role": "narrator"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_role"
    empty = await client.post(f"/api/chats/{chat_id}/messages", json={"text": "  ", "role": "user"})
    assert empty.status_code == 400


async def test_roles_change_by_id_by_numbers_and_for_all(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    for text in ("one", "two", "three", "four"):
        await client.post(f"/api/chats/{chat_id}/messages", json={"text": text, "role": "user"})
    messages = await path(client, chat_id)  # 1 Opening, 2 one, 3 two, 4 three, 5 four

    single = await client.put(f"/api/chats/{chat_id}/roles", json={"role": "system", "ids": [messages[1]["id"]]})
    assert single.status_code == 200
    assert [m["role"] for m in single.json()["messages"]] == ["system"]

    by_numbers = await client.put(f"/api/chats/{chat_id}/roles", json={"role": "assistant", "numbers": "3-4, 5"})
    assert by_numbers.status_code == 200 and len(by_numbers.json()["messages"]) == 3
    after = await path(client, chat_id)
    assert [m["role"] for m in after] == ["assistant", "system", "assistant", "assistant", "assistant"]
    assert after[3]["name"] == "Ann"

    everything = await client.put(f"/api/chats/{chat_id}/roles", json={"role": "user", "all": True})
    assert everything.status_code == 200
    assert {m["role"] for m in await path(client, chat_id)} == {"user"}


async def test_role_change_errors_change_nothing(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    before = await path(client, chat_id)
    out_of_range = await client.put(f"/api/chats/{chat_id}/roles", json={"role": "user", "numbers": "1-9"})
    assert out_of_range.status_code == 400 and out_of_range.json()["error"]["code"] == "bad_numbers"
    nothing = await client.put(f"/api/chats/{chat_id}/roles", json={"role": "user"})
    assert nothing.json()["error"]["code"] == "nothing_selected"
    other = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    foreign = ctx.chats.add_message(other, role="user", content="x")
    mixed = await client.put(
        f"/api/chats/{chat_id}/roles", json={"role": "system", "ids": [before[0]["id"], foreign["id"]]}
    )
    assert mixed.status_code == 404
    assert await path(client, chat_id) == before  # the valid id was not changed either


async def test_roles_cannot_change_during_generation(client, ctx, live_llm):
    chat_id = await setup_chat(client, base_url=live_llm.base_url)
    live_llm.chunk_delay = 0.05
    live_llm.queue(" ".join(["slow"] * 50))
    run_id = (await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi"})).json()["run_id"]
    response = await client.put(f"/api/chats/{chat_id}/roles", json={"role": "system", "all": True})
    assert response.status_code == 409 and response.json()["error"]["code"] == "busy"
    ctx.runs.stop(chat_id)
    await finish(ctx, run_id)


async def test_impersonate_in_new_chat_ends_with_a_user_turn(client, ctx, fake_llm):
    # Gemini rejects [system, assistant] with "contents is not specified": a user turn must exist.
    chat_id = await setup_chat(client)
    fake_llm.queue("I knock.")
    run_id = (await client.post(f"/api/chats/{chat_id}/impersonate")).json()["run_id"]
    await finish(ctx, run_id)
    sent = fake_llm.requests[-1]["messages"]
    assert sent[-1]["role"] == "user" and "next message of User" in sent[-1]["content"]
    last = (await path(client, chat_id))[-1]
    assert (last["role"], last["content"]) == ("user", "I knock.")


async def test_reply_arrives_with_the_chosen_role(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    fake_llm.queue("Rain begins.")
    sent = await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi", "reply_role": "system"})
    assert sent.status_code == 202, sent.text
    await finish(ctx, sent.json()["run_id"])
    reply = (await path(client, chat_id))[-1]
    assert (reply["role"], reply["content"], reply["name"]) == ("system", "Rain begins.", "")
    assert ctx.runs.get(sent.json()["run_id"]).events[0]["message"]["role"] == "system"


async def test_continue_writes_as_chosen_role(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    fake_llm.queue("Thunder.")
    run_id = (await client.post(f"/api/chats/{chat_id}/continue", json={"role": "system"})).json()["run_id"]
    await finish(ctx, run_id)
    fake_llm.queue("I step in.")
    run_id = (await client.post(f"/api/chats/{chat_id}/continue", json={"role": "user"})).json()["run_id"]
    await finish(ctx, run_id)
    assert [(m["role"], m["content"]) for m in (await path(client, chat_id))[1:]] == [
        ("system", "Thunder."), ("user", "I step in."),
    ]
    assert "next message of User" in fake_llm.requests[-1]["messages"][-1]["content"]


async def test_bad_reply_role_writes_nothing(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    before = await path(client, chat_id)
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi", "reply_role": "narrator"})
    assert response.status_code == 400 and response.json()["error"]["code"] == "bad_role"
    assert await path(client, chat_id) == before


async def test_system_reply_can_be_regenerated(client, ctx, fake_llm):
    chat_id = await setup_chat(client)
    fake_llm.queue("First.")
    run_id = (await client.post(f"/api/chats/{chat_id}/continue", json={"role": "system"})).json()["run_id"]
    await finish(ctx, run_id)
    fake_llm.queue("Second.")
    response = await client.post(f"/api/chats/{chat_id}/regenerate")
    assert response.status_code == 202, response.text
    await finish(ctx, response.json()["run_id"])
    last = (await path(client, chat_id))[-1]
    assert (last["role"], last["content"], last["swipe_count"]) == ("system", "Second.", 2)


def test_prompt_without_user_turn_gets_a_start_turn():
    prompt = build_prompt({"name": "Ann", "description": "d"}, [{"role": "assistant", "content": "Hello"}])
    assert [(m.role, m.content) for m in prompt[1:]] == [("user", START_TURN), ("assistant", "Hello")]
    with_user = build_prompt(None, [{"role": "user", "content": "hi"}])
    assert [m.content for m in with_user] == ["hi"]
    assert build_prompt(None, []) == []


def test_instruction_without_character_names_the_assistant():
    from tavern.services.chat import IMPERSONATE_INSTRUCTION

    prompt = build_prompt(None, [{"role": "assistant", "content": "Hi"}], user_name="Bob",
                          instruction=IMPERSONATE_INSTRUCTION)
    last = prompt[-1].content
    assert "no reply from Assistant" in last
    assert "from ." not in last


def test_parse_numbers():
    assert parse_numbers("1-3, 5", 9) == {1, 2, 3, 5}
    assert parse_numbers(" 4 - 2 ;7 ", 9) == {2, 3, 4, 7}
    with pytest.raises(AppError):
        parse_numbers("0", 9)
    with pytest.raises(AppError):
        parse_numbers("two", 9)
