"""services/messages.py (ADR 0019 section 2.2-C): edits, hides, deletes, swipes, branch changes and new messages
fire events, from the page's routes and from the assistant's `app_call` (the same routes)."""

from __future__ import annotations

import asyncio

from tavern.services.assistant_app import app_call
from tavern.services.assistant_tools import Env

EVENTS = ("message.added", "message.edited", "message.excluded", "message.deleted", "message.swipe_selected",
          "chat.branch_selected", "chat.created")


class Spy:
    def __init__(self, ctx):
        self.seen: list[tuple[str, dict]] = []
        for name in EVENTS:
            ctx.events.on(name, lambda _n=name, **payload: self.seen.append((_n, payload)))

    def names(self) -> list[str]:
        return [n for n, _ in self.seen]

    def last(self, name: str) -> dict:
        return next(p for n, p in reversed(self.seen) if n == name)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def make_chat(client):
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    ids = []
    for i, role in enumerate(("user", "assistant", "user")):
        r = await client.post(f"/api/chats/{chat['id']}/messages", json={"text": f"текст {i}", "role": role})
        ids.append(r.json()["id"])
    return chat, ids


async def test_a_new_chat_and_new_messages_fire_events(client, ctx):
    spy = Spy(ctx)
    chat, ids = await make_chat(client)
    await settle()
    assert spy.names() == ["chat.created", "message.added", "message.added", "message.added"]
    assert spy.last("message.added")["chat_id"] == chat["id"] and spy.last("message.added")["message"]["id"] == ids[-1]


async def test_the_users_message_in_send_fires_added(client, ctx, fake_llm):
    conn = (await client.post("/api/connections", json={"name": "f", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    spy = Spy(ctx)
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "привет"})).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    await settle()
    added = [p["message"] for n, p in spy.seen if n == "message.added"]
    assert [m["role"] for m in added] == ["user"] and added[0]["content"] == "привет"


async def test_edit_and_hide_fire_with_what_was_before(client, ctx):
    chat, ids = await make_chat(client)
    spy = Spy(ctx)
    r = await client.put(f"/api/messages/{ids[0]}", json={"content": "новое"})
    assert r.status_code == 200
    edited = spy.last("message.edited")
    assert edited["before"] == "текст 0" and edited["message"]["content"] == "новое" and edited["chat_id"] == chat["id"]
    await client.put(f"/api/messages/{ids[0]}", json={"exclude_from_prompt": True})
    assert spy.last("message.excluded")["excluded"] is True
    await client.put(f"/api/messages/{ids[0]}", json={"exclude_from_prompt": True})  # no change: no event
    assert spy.names().count("message.excluded") == 1
    assert spy.names().count("message.edited") == 1


async def test_delete_fires_with_the_row_before_and_the_children_that_moved_up(client, ctx):
    chat, ids = await make_chat(client)
    spy = Spy(ctx)
    r = await client.delete(f"/api/messages/{ids[1]}")
    assert r.status_code == 200
    deleted = spy.last("message.deleted")
    assert deleted["message"]["id"] == ids[1] and deleted["message"]["content"] == "текст 1"
    assert deleted["children"] == [ids[2]] and deleted["chat_id"] == chat["id"]


async def test_swipe_choice_and_branch_choice_fire(client, ctx):
    chat, ids = await make_chat(client)
    ctx.chats.add_swipe(ids[1], "другой вариант")
    spy = Spy(ctx)
    r = await client.put(f"/api/messages/{ids[1]}/swipe", json={"index": 0})  # a new swipe is the shown one
    assert r.status_code == 200
    swiped = spy.last("message.swipe_selected")
    assert swiped["before_idx"] == 1 and swiped["message"]["active_swipe"] == 0
    branch = ctx.chats.add_message(chat["id"], role="user", content="ветка", parent_id=ids[1])
    ctx.chats.set_leaf(chat["id"], ids[2])  # the page shows the first branch
    r = await client.post(f"/api/chats/{chat['id']}/branch", json={"leaf_id": branch["id"]})
    assert r.status_code == 200
    moved = spy.last("chat.branch_selected")
    assert moved["leaf_id"] == branch["id"] and moved["before_leaf"] == ids[2]


async def test_regenerate_fires_swipe_selected_for_the_new_version(client, ctx, fake_llm):
    conn = (await client.post("/api/connections", json={"name": "f", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    chat, ids = await make_chat(client)
    await client.post(f"/api/chats/{chat['id']}/messages", json={"text": "ответ", "role": "assistant"})
    spy = Spy(ctx)
    run_id = (await client.post(f"/api/chats/{chat['id']}/regenerate")).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    await settle()
    assert spy.last("message.swipe_selected")["before_idx"] == 0


async def test_the_assistants_app_call_fires_the_same_events(client, ctx):
    chat, ids = await make_chat(client)
    spy = Spy(ctx)
    env = Env(ctx)
    await app_call(env, {"method": "PUT", "path": f"/messages/{ids[0]}", "body": {"content": "от помощника"}})
    await app_call(env, {"method": "DELETE", "path": f"/messages/{ids[2]}"})
    assert spy.names() == ["message.edited", "message.deleted"]
    assert spy.last("message.edited")["before"] == "текст 0"


async def test_a_failing_handler_does_not_break_the_route(client, ctx):
    def broken(**_):
        raise RuntimeError("boom")

    ctx.events.on("message.edited", broken)
    chat, ids = await make_chat(client)
    r = await client.put(f"/api/messages/{ids[0]}", json={"content": "ок"})
    assert r.status_code == 200 and r.json()["content"] == "ок"
