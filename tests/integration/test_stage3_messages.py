"""Stage 3 tests for continuation, impersonation, branches and chat search."""

from __future__ import annotations

import asyncio


async def finish(ctx, run_id: str) -> None:
    run = ctx.runs.get(run_id)
    await asyncio.wait([run.task], timeout=10)
    assert run.finished


async def setup_chat(client, ctx, fake_llm, base_url="http://llm/v1"):
    character = (await client.post("/api/characters", json={
        "name": "Ann", "first_mes": "Opening", "description": "kind",
    })).json()
    await client.post("/api/connections", json={
        "name": "fake", "base_url": base_url, "model": "m",
    })
    chat = (await client.post("/api/chats", json={"character_id": character["id"]})).json()["chat"]
    return chat["id"]


async def test_continue_generates_assistant_child_without_new_user_message(client, ctx, fake_llm):
    chat_id = await setup_chat(client, ctx, fake_llm)
    fake_llm.queue("continued")
    response = await client.post(f"/api/chats/{chat_id}/continue")
    assert response.status_code == 202
    await finish(ctx, response.json()["run_id"])
    messages = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    assert [m["role"] for m in messages] == ["assistant", "assistant"]
    assert messages[-1]["content"] == "continued"
    assert messages[-1]["parent_id"] == messages[-2]["id"]


async def test_impersonate_generates_a_user_message(client, ctx, fake_llm):
    chat_id = await setup_chat(client, ctx, fake_llm)
    fake_llm.queue("I open the door")
    response = await client.post(f"/api/chats/{chat_id}/impersonate")
    assert response.status_code == 202
    await finish(ctx, response.json()["run_id"])
    messages = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "I open the door"


async def test_branch_children_can_be_listed_and_selected(client, ctx, fake_llm):
    chat_id = await setup_chat(client, ctx, fake_llm)
    root = ctx.chats.active_path(chat_id)[0]
    first = ctx.chats.add_message(chat_id, role="user", content="one", parent_id=root["id"])
    left = ctx.chats.add_message(chat_id, role="assistant", content="left", parent_id=first["id"])
    right = ctx.chats.add_message(chat_id, role="assistant", content="right", parent_id=first["id"], set_leaf=False)
    listed = await client.get(f"/api/messages/{first['id']}/branches")
    assert listed.status_code == 200
    assert {m["id"] for m in listed.json()["branches"]} == {left["id"], right["id"]}
    selected = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": right["id"]})
    assert selected.status_code == 200
    assert selected.json()["chat"]["leaf_id"] == right["id"]
    history = await client.get(f"/api/chats/{chat_id}/history")
    assert history.status_code == 200
    assert {m["id"] for m in history.json()["messages"]} >= {left["id"], right["id"]}


async def test_chat_search_returns_matching_messages(client, ctx, fake_llm):
    chat_id = await setup_chat(client, ctx, fake_llm)
    root = ctx.chats.active_path(chat_id)[0]
    ctx.chats.add_message(chat_id, role="user", content="The hidden lighthouse", parent_id=root["id"])
    response = await client.get(f"/api/chats/{chat_id}/search", params={"q": "LIGHTHOUSE"})
    assert response.status_code == 200
    assert [m["content"] for m in response.json()["messages"]] == ["The hidden lighthouse"]
    literal = await client.get(f"/api/chats/{chat_id}/search", params={"q": "%lighthouse"})
    assert literal.json()["messages"] == []
    empty = await client.get(f"/api/chats/{chat_id}/search", params={"q": "  "})
    assert empty.status_code == 400 and empty.json()["error"]["code"] == "empty_query"


async def test_selecting_a_branch_opens_its_newest_continuation(client, ctx, fake_llm):
    chat_id = await setup_chat(client, ctx, fake_llm)
    root = ctx.chats.active_path(chat_id)[0]
    old = ctx.chats.add_message(chat_id, role="user", content="old", parent_id=root["id"])
    old_reply = ctx.chats.add_message(chat_id, role="assistant", content="old reply", parent_id=old["id"])
    ctx.chats.add_message(chat_id, role="user", content="new", parent_id=root["id"])
    selected = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": old["id"]})
    assert selected.json()["chat"]["leaf_id"] == old_reply["id"]
    path = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    assert [m["content"] for m in path] == ["Opening", "old", "old reply"]


async def test_branch_switch_is_refused_while_generating(client, ctx, live_llm):
    chat_id = await setup_chat(client, ctx, live_llm, base_url=live_llm.base_url)
    root = ctx.chats.active_path(chat_id)[0]
    live_llm.chunk_delay = 0.05
    live_llm.queue(" ".join(["slow"] * 50))
    run_id = (await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi"})).json()["run_id"]
    response = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": root["id"]})
    assert response.status_code == 400 and response.json()["error"]["code"] == "busy"
    ctx.runs.stop(chat_id)
    await finish(ctx, run_id)
    assert ctx.chats.get_chat(chat_id)["leaf_id"] != root["id"]


async def test_history_of_unknown_chat_is_404(client):
    response = await client.get("/api/chats/missing/history")
    assert response.status_code == 404


async def test_branch_selection_rejects_leaf_from_another_chat(client, ctx, fake_llm):
    chat_id = await setup_chat(client, ctx, fake_llm)
    other = ctx.chats.create_chat(character_id=None, title="Other")
    foreign = ctx.chats.add_message(other["id"], role="user", content="foreign")
    response = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": foreign["id"]})
    assert response.status_code == 400 and response.json()["error"]["code"] == "wrong_chat"
