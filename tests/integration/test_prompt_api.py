"""Stage 4а over HTTP: chat members, presets (with ST import), what the model saw, exact token count."""

from __future__ import annotations

import asyncio
import json

from tavern.storage.db import Database
from tavern.storage.migrations import load_migrations


async def make_character(client, name, **fields):
    return (await client.post("/api/characters", json={"name": name, **fields})).json()


async def setup(client):
    ann = await make_character(client, "Ann", description="Ann is kind.", first_mes="Hello")
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return ann, chat["id"]


async def finish(ctx, response):
    assert response.status_code == 202, response.text
    run = ctx.runs.get(response.json()["run_id"])
    await asyncio.wait([run.task], timeout=10)


async def last_message(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]


async def test_new_chat_has_its_character_and_default_me_as_members(client, ctx, fake_llm):
    kira = await make_character(client, "Kira", description="A traveller.")
    await client.put("/api/settings", json={"default_me": kira["id"]})
    ann, chat_id = await setup(client)
    members = (await client.get(f"/api/chats/{chat_id}/members")).json()["members"]
    assert [(m["name"], m["is_me"]) for m in members] == [("Ann", False), ("Kira", True)]

    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi {{char}}"}))
    sent = fake_llm.requests[-1]["messages"]
    assert "Kira (played by the user)" in sent[0]["content"]
    mine = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-2]
    assert mine["name"] == "Kira"


async def test_members_can_be_added_changed_and_removed(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    bob = await make_character(client, "Bob", description="Bob the smith.")
    seen = []
    ctx.events.on("chat.members_changed", lambda **kw: seen.append(kw["chat_id"]))

    added = await client.post(f"/api/chats/{chat_id}/members", json={"character_id": bob["id"], "depth": 1})
    assert added.status_code == 201
    again = await client.post(f"/api/chats/{chat_id}/members", json={"character_id": bob["id"]})
    assert again.json()["error"]["code"] == "already_member"
    bad = await client.put(f"/api/chats/{chat_id}/members/{bob['id']}", json={"importance": 11})
    assert bad.json()["error"]["code"] == "bad_member"

    # Two characters: with full cards Bob's goes at its depth even while Ann speaks.
    await client.put(f"/api/chats/{chat_id}/speakers", json={"others": "full"})
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi", "speaker": ann["id"]}))
    contents = [m["content"] for m in fake_llm.requests[-1]["messages"]]
    assert "Bob the smith." in contents[-3] and contents[-2] == "hi"
    assert contents[-1] == "[Write the next reply only as Ann. Do not write for the other characters or User.]"

    # making the main character "me" moves the main role to Bob
    changed = await client.put(f"/api/chats/{chat_id}/members/{ann['id']}", json={"is_me": True})
    assert changed.status_code == 200
    assert (await client.get(f"/api/chats/{chat_id}")).json()["chat"]["character_id"] == bob["id"]
    removed = await client.delete(f"/api/chats/{chat_id}/members/{bob['id']}")
    assert [m["name"] for m in removed.json()["members"]] == ["Ann"]
    assert seen == [chat_id] * 3


async def test_preset_parameters_and_blocks_reach_the_model(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    created = await client.post("/api/presets", json={"name": "Brief", "from": "default"})
    preset = created.json()
    data = preset["data"]
    data["params"] = {"temperature": 0.3, "max_tokens": 77}
    data["blocks"].append({"id": "style", "kind": "text", "name": "Style", "text": "Be brief.", "role": "system"})
    assert (await client.put(f"/api/presets/{preset['id']}", json={"name": "Brief", "data": data})).status_code == 200
    await client.put(f"/api/chats/{chat_id}/meta", json={"preset_id": preset["id"]})

    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi"}))
    request = fake_llm.requests[-1]
    assert request["temperature"] == 0.3 and request["max_tokens"] == 77
    assert request["messages"][-1]["content"] == "Be brief."

    builtin = await client.put("/api/presets/default", json={"name": "x", "data": data})
    assert builtin.json()["error"]["code"] == "builtin_preset"
    broken = await client.put(f"/api/presets/{preset['id']}", json={"name": "B", "data": {"blocks": [{"kind": "??"}]}})
    assert broken.json()["error"]["code"] == "bad_preset"


async def test_st_preset_import_and_export(client):
    st = {"temperature": 0.9, "openai_max_context": 5000, "prompts": [
        {"identifier": "main", "name": "Main", "role": "system", "content": "Hi {{char}}"},
        {"identifier": "chatHistory", "name": "History", "marker": True}],
        "prompt_order": [{"character_id": 100001, "order": [
            {"identifier": "main", "enabled": True}, {"identifier": "chatHistory", "enabled": True}]}]}
    response = await client.post("/api/presets/import/st?name=Cool.json", content=json.dumps(st).encode())
    assert response.status_code == 201, response.text
    preset = response.json()
    assert preset["name"] == "Cool" and preset["data"]["params"] == {"temperature": 0.9}
    exported = await client.get(f"/api/presets/{preset['id']}/export/st")
    assert exported.json()["openai_max_context"] == 5000
    assert "Cool.json" in exported.headers["content-disposition"]
    text_completion = await client.post("/api/presets/import/st", content=b'{"temp": 1, "rep_pen": 1.1}')
    assert text_completion.json()["error"]["code"] == "bad_preset"


async def test_own_format_keeps_everything_and_st_format_drops_extras(client):
    created = (await client.post("/api/presets", json={"name": "Full", "from": "default"})).json()
    data = created["data"]
    next(b for b in data["blocks"] if b["kind"] == "history")["history"] = {"mode": "last", "count": 7, "max_tokens": 0,
                                                                          "thoughts": 2}
    next(b for b in data["blocks"] if b.get("when") == "no_write_for_me")["text"] = "Hands off {{user}}."
    await client.put(f"/api/presets/{created['id']}", json={"name": "Full", "data": data})

    native = await client.get(f"/api/presets/{created['id']}/export?format=tavern")
    assert "Full.tavern-preset.json" in native.headers["content-disposition"]
    back = (await client.post("/api/presets/import?name=x.json", content=native.content)).json()
    assert back["name"] == "Full" and back["data"] == data

    st = await client.get(f"/api/presets/{created['id']}/export?format=st")
    assert "no_write_for_me" not in st.text and "prompt_order" in st.json()
    via_st = (await client.post("/api/presets/import?name=Old.json", content=st.content)).json()
    history = next(b for b in via_st["data"]["blocks"] if b["kind"] == "history")
    assert via_st["name"] == "Old" and history["history"]["mode"] == "all"  # ST has no place for it

    newer = await client.post("/api/presets/import", content=b'{"tavern_preset": 99, "data": {}}')
    assert newer.json()["error"]["code"] == "bad_preset"


async def test_what_the_model_saw_survives_later_edits(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "first"}))
    reply = await last_message(client, chat_id)

    saw = (await client.get(f"/api/messages/{reply['id']}/prompt")).json()
    assert saw["meta"]["model"] == "m" and saw["meta"]["preset_id"] == "default"
    assert [e["text"] for e in saw["entries"] if e["kind"] == "message"] == ["Hello", "first"]

    # edit the card and the user's message: the old record must not change
    card = (await client.get(f"/api/characters/{ann['id']}")).json()["data"]
    await client.put(f"/api/characters/{ann['id']}", json={**card, "description": "Ann is grumpy."})
    user_msg = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-2]
    await client.put(f"/api/messages/{user_msg['id']}", json={"content": "edited"})
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/regenerate"))

    old = (await client.get(f"/api/messages/{reply['id']}/prompt?swipe=0")).json()
    assert any("Ann is kind." in e["text"] for e in old["entries"])
    assert "first" in [e["text"] for e in old["entries"]]
    new = (await client.get(f"/api/messages/{reply['id']}/prompt?swipe=1")).json()
    assert any("Ann is grumpy." in e["text"] for e in new["entries"])

    versions = (await client.get("/api/prompt/versions", params={"source": f"character:{ann['id']}:card"})).json()
    assert len(versions["versions"]) == 2

    greeting = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][0]
    missing = await client.get(f"/api/messages/{greeting['id']}/prompt")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "no_prompt"


async def test_exact_token_count_asks_the_api(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "count me"}))
    reply = await last_message(client, chat_id)
    before = len(fake_llm.requests)
    counted = await client.post(f"/api/messages/{reply['id']}/prompt/count")
    assert counted.status_code == 200, counted.text
    assert counted.json()["tokens"] > 0
    assert fake_llm.requests[before]["max_tokens"] == 1


async def test_deleting_a_chat_removes_its_prompt_texts(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "bye"}))
    assert ctx.db.fetchone("SELECT COUNT(*) FROM prompt_pieces")[0] > 0
    await client.delete(f"/api/chats/{chat_id}")
    assert ctx.db.fetchone("SELECT COUNT(*) FROM prompt_pieces")[0] == 0


def test_upgrade_makes_existing_chat_characters_members(tmp_path):
    db = Database(tmp_path / "old.sqlite3")
    db.migrate(load_migrations()[:2])
    db.execute("INSERT INTO characters (id, name, data) VALUES ('a', 'Ann', '{}')")
    db.execute("INSERT INTO chats (id, character_id, title) VALUES ('c', 'a', 'old chat')")
    db.migrate()
    assert db.schema_version == load_migrations()[-1].version
    row = db.fetchone("SELECT * FROM chat_members")
    assert (row["chat_id"], row["character_id"], row["is_me"], row["depth"]) == ("c", "a", 0, None)
