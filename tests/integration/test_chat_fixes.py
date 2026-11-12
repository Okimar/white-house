"""Regression tests: chat import keeps the cast, branches can be started, search says where it found."""

from __future__ import annotations

import asyncio
import json

from tavern.storage.db import load_migrations


async def finish(ctx, run_id: str) -> None:
    run = ctx.runs.get(run_id)
    await asyncio.wait([run.task], timeout=10)
    assert run.finished


async def make_chat(client, fake_llm):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "ANN-CARD", "first_mes": "Hi"})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return ann, chat["id"]


async def send(client, ctx, chat_id: str, text: str) -> None:
    await finish(ctx, (await client.post(f"/api/chats/{chat_id}/send", json={"text": text})).json()["run_id"])


async def test_an_imported_chat_keeps_its_members_and_the_card_reaches_the_model(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    bob = (await client.post("/api/characters", json={"name": "Bob", "description": "BOB-CARD"})).json()
    await client.post(f"/api/chats/{chat_id}/members", json={"character_id": bob["id"], "depth": 3, "importance": 7,
                                                             "muted": True, "blurb": "smith"})
    exported = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).content
    new_id = (await client.post("/api/chats/import.jsonl", content=exported)).json()["chat"]["id"]

    members = (await client.get(f"/api/chats/{new_id}/members")).json()["members"]
    assert {m["name"] for m in members} == {"Ann", "Bob"}
    copied = next(m for m in members if m["name"] == "Bob")
    assert (copied["depth"], copied["importance"], copied["muted"], copied["blurb"]) == (3, 7, True, "smith")

    fake_llm.queue("reply")
    await send(client, ctx, new_id, "hello")
    assert "ANN-CARD" in json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)


async def test_an_imported_chat_keeps_who_wrote_and_what_each_version_carried(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    fake_llm.queue("<thinking>why</thinking>first")
    await send(client, ctx, chat_id, "hello")
    reply = ctx.chats.active_path(chat_id)[-1]
    fake_llm.queue("<thinking>why two</thinking>second")
    await finish(ctx, (await client.post(f"/api/chats/{chat_id}/regenerate")).json()["run_id"])

    exported = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).content
    new_id = (await client.post("/api/chats/import.jsonl", content=exported)).json()["chat"]["id"]
    copy = ctx.chats.active_path(new_id)[-1]
    assert copy["character_id"] == ann["id"]
    versions = ctx.chats.list_swipes(copy["id"])
    assert [v["content"] for v in versions] == ["first", "second"]
    assert [v["meta"]["reasoning"] for v in versions] == ["why", "why two"]
    assert copy["id"] != reply["id"]


async def test_a_chat_file_without_members_gets_its_main_character_and_my_default_one(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    me = (await client.post("/api/characters", json={"name": "Me"})).json()
    await client.put("/api/settings", json={"default_me": me["id"]})
    rows = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).text.splitlines()
    older_format = "\n".join(r for r in rows if json.loads(r)["type"] not in ("member", "swipe_meta"))
    new_id = (await client.post("/api/chats/import.jsonl", content=older_format)).json()["chat"]["id"]
    members = {m["name"]: m["is_me"] for m in (await client.get(f"/api/chats/{new_id}/members")).json()["members"]}
    assert members == {"Ann": False, "Me": True}


async def test_import_skips_members_and_authors_this_install_does_not_have(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    bob = (await client.post("/api/characters", json={"name": "Bob"})).json()
    await client.post(f"/api/chats/{chat_id}/members", json={"character_id": bob["id"]})
    ctx.chats.add_message(chat_id, role="assistant", content="from Bob", name="Bob", character_id=bob["id"],
                          parent_id=ctx.chats.get_chat(chat_id)["leaf_id"])
    exported = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).content
    await client.delete(f"/api/characters/{bob['id']}")
    response = await client.post("/api/chats/import.jsonl", content=exported)
    assert response.status_code == 201
    new_id = response.json()["chat"]["id"]
    # the missing character is not dropped: it stays as an empty place under its name (no card, never speaks)
    listed = (await client.get(f"/api/chats/{new_id}/members")).json()["members"]
    assert [(m["name"], m["empty"]) for m in listed] == [("Ann", False), ("Bob", True)]
    assert [m["name"] for m in ctx.members.list(new_id)] == ["Ann"]
    assert response.json()["empty_members"] == ["Bob"]
    last = ctx.chats.active_path(new_id)[-1]
    assert (last["name"], last["character_id"]) == ("Bob", None)


async def test_chats_imported_before_the_fix_get_their_main_character_back(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    broken = ctx.chats.create_chat(character_id=ann["id"], title="old import")
    free = ctx.chats.create_chat(character_id=None, title="no character")
    assert ctx.members.list(broken["id"]) == []
    migration = next(m for m in load_migrations() if m.name == "imported_chat_members")
    ctx.db.execute(migration.sql)
    assert [m["name"] for m in ctx.members.list(broken["id"])] == ["Ann"]
    assert [m["name"] for m in ctx.members.list(chat_id)] == ["Ann"]
    assert ctx.members.list(free["id"]) == []


async def test_a_new_branch_starts_at_an_old_message_and_the_old_one_stays(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    fake_llm.queue("one", "two")
    await send(client, ctx, chat_id, "first")
    await send(client, ctx, chat_id, "second")
    path = ctx.chats.active_path(chat_id)
    fork = path[2]  # the reply "one"
    old_next = path[3]

    moved = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": fork["id"], "exact": True})
    assert moved.status_code == 200 and moved.json()["chat"]["leaf_id"] == fork["id"]
    assert [m["content"] for m in ctx.chats.active_path(chat_id)] == ["Hi", "first", "one"]

    fake_llm.queue("three")
    await send(client, ctx, chat_id, "other way")
    assert [m["content"] for m in ctx.chats.active_path(chat_id)] == ["Hi", "first", "one", "other way", "three"]
    children = (await client.get(f"/api/messages/{fork['id']}/branches")).json()["branches"]
    assert old_next["id"] in {m["id"] for m in children} and len(children) == 2

    back = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": old_next["id"]})
    assert ctx.chats.get_message(back.json()["chat"]["leaf_id"])["content"] == "two"


async def test_search_says_which_finds_are_on_the_open_branch_and_skips_service_messages(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    root = ctx.chats.active_path(chat_id)[0]
    ctx.chats.add_message(chat_id, role="user", content="lighthouse left", parent_id=root["id"], set_leaf=False)
    here = ctx.chats.add_message(chat_id, role="user", content="lighthouse here", parent_id=root["id"])
    ctx.chats.add_message(chat_id, role="system", content="lighthouse service", parent_id=here["id"], hidden=True,
                          set_leaf=False)
    found = (await client.get(f"/api/chats/{chat_id}/search", params={"q": "lighthouse"})).json()["messages"]
    assert {m["content"]: m["on_path"] for m in found} == {"lighthouse left": False, "lighthouse here": True}


async def test_a_chat_is_renamed_and_keeps_its_place(client, ctx, fake_llm):
    ann, chat_id = await make_chat(client, fake_llm)
    response = await client.put(f"/api/chats/{chat_id}", json={"title": "Гавань"})
    assert response.status_code == 200 and response.json()["title"] == "Гавань"
    assert (await client.put(f"/api/chats/{chat_id}", json={"title": ""})).status_code == 400
