"""Empty places in a chat's cast (an imported character that has no card here) and «Заменить».

An imported chat never links a character by name: every author becomes an empty place under its name,
which never speaks and is not in the prompt; «Заменить» gives the place a real character and re-points the
messages it wrote. Members of any kind can be replaced; their settings stay.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from tavern.services import speakers


def _st_file(names=("Ann", "Bob")) -> bytes:
    rows = [{"chat_metadata": {}, "user_name": "unused", "character_name": "unused"}]
    rows.append({"name": "Me", "is_user": True, "mes": "hello", "extra": {}})
    for name in names:
        rows.append({"name": name, "is_user": False, "mes": f"from {name}", "extra": {}})
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8")


async def _import(client, body: bytes):
    return await client.post("/api/chats/import.jsonl", content=body, headers={"X-Filename": quote("c.jsonl")})


async def _character(client, name, **fields):
    return (await client.post("/api/characters", json={"name": name, **fields})).json()


async def _members(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/members")).json()["members"]


async def test_unknown_authors_become_empty_places_and_no_character_is_made(client, ctx):
    before = len(ctx.characters.list())
    response = await _import(client, _st_file())
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["empty_members"] == ["Ann", "Bob"]  # the warning's names; "Me" is the user
    assert len(ctx.characters.list()) == before
    members = await _members(client, data["chat"]["id"])
    assert [(m["name"], m["empty"], m["character_id"]) for m in members] == [("Ann", True, None), ("Bob", True, None)]
    assert all(m["slot_id"].startswith("slot-") for m in members)
    assert ctx.members.list(data["chat"]["id"]) == []  # nothing else in the app sees them
    assert data["chat"]["character_id"] is None


async def test_a_name_equal_to_an_existing_character_is_not_linked(client, ctx):
    ann = await _character(client, "Ann")
    data = (await _import(client, _st_file(("Ann",)))).json()
    chat_id = data["chat"]["id"]
    assert data["chat"]["character_id"] is None and data["empty_members"] == ["Ann"]
    assert ctx.members.list(chat_id) == []
    reply = next(m for m in ctx.chats.active_path(chat_id) if m["role"] == "assistant")
    assert reply["character_id"] is None and reply["name"] == "Ann"
    assert [c["id"] for c in ctx.characters.list()] == [ann["id"]]
    assert (await _members(client, chat_id))[0]["empty"] is True


async def test_replacing_an_empty_place_keeps_settings_and_repoints_messages(client, ctx):
    chat_id = (await _import(client, _st_file())).json()["chat"]["id"]
    seen = []
    ctx.events.on("chat.members_changed", lambda **kw: seen.append([m["name"] for m in kw["members"]]))
    slot = (await _members(client, chat_id))[0]
    tuned = await client.put(f"/api/chats/{chat_id}/members/{slot['slot_id']}",
                             json={"talkativeness": 37, "muted": True, "depth": 4, "blurb": "old friend"})
    assert tuned.status_code == 200, tuned.text
    mine = await _character(client, "Anna")

    done = await client.post(f"/api/chats/{chat_id}/members/{slot['slot_id']}/replace",
                             json={"character_id": mine["id"]})
    assert done.status_code == 200, done.text
    members = done.json()["members"]
    anna = next(m for m in members if m["character_id"] == mine["id"])
    assert (anna["empty"], anna["talkativeness"], anna["muted"], anna["depth"], anna["blurb"]) == (
        False, 37, True, 4, "old friend")
    assert [m["name"] for m in members] == ["Anna", "Bob"]  # Bob is still an empty place
    assert seen[-1] == ["Anna"]
    wrote = {m["name"]: m["character_id"] for m in ctx.chats.active_path(chat_id) if m["role"] == "assistant"}
    assert wrote == {"Ann": mine["id"], "Bob": None}  # the author's name stays, the id is the new character
    assert ctx.chats.get_chat(chat_id)["character_id"] == mine["id"]


async def test_replacing_a_real_member_keeps_settings_and_repoints_messages(client, ctx):
    ann = await _character(client, "Ann", first_mes="Hi")
    bob = await _character(client, "Bob")
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    await client.put(f"/api/chats/{chat['id']}/members/{ann['id']}", json={"talkativeness": 82, "importance": 3})
    assert ctx.chats.active_path(chat["id"])[0]["character_id"] == ann["id"]

    done = await client.post(f"/api/chats/{chat['id']}/members/{ann['id']}/replace", json={"character_id": bob["id"]})
    assert done.status_code == 200, done.text
    [member] = done.json()["members"]
    assert (member["character_id"], member["talkativeness"], member["importance"]) == (bob["id"], 82, 3)
    assert ctx.chats.active_path(chat["id"])[0]["character_id"] == bob["id"]
    assert ctx.chats.get_chat(chat["id"])["character_id"] == bob["id"]

    other = await _character(client, "Cid")
    await client.post(f"/api/chats/{chat['id']}/members", json={"character_id": other["id"]})
    clash = await client.post(f"/api/chats/{chat['id']}/members/{bob['id']}/replace", json={"character_id": other["id"]})
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "already_member"
    missing = await client.post(f"/api/chats/{chat['id']}/members/{bob['id']}/replace", json={"character_id": "nope"})
    assert missing.status_code == 404


async def test_an_empty_place_never_speaks(client, ctx, fake_llm):
    ann = await _character(client, "Ann", first_mes="Hi")
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    slot = ctx.members.add_slot(chat_id, "Ghost", talkativeness=100)
    assert slot["empty"] and [m["name"] for m in ctx.members.list(chat_id, include_empty=True)] == ["Ann", "Ghost"]
    # asked by hand: "ответь ты" is refused
    refused = await client.post(f"/api/chats/{chat_id}/continue", json={"speaker": slot["slot_id"]})
    assert refused.json()["error"]["code"] == "bad_speaker"
    # the speaker logic never meets it, even when it is handed a member without a character id
    rows = ctx.members.list(chat_id, include_empty=True)
    assert [m["name"] for m in speakers.characters(rows)] == ["Ann"]
    assert [m["name"] for m in speakers.may_speak(rows)] == ["Ann"]
    assert not speakers.several(rows)
    assert speakers.choose(speakers.defaults(), rows, []) == ann["id"]
    assert speakers.author({"role": "assistant", "name": "Ghost", "character_id": None}, rows) is None


async def test_the_empty_place_cannot_be_me_and_can_be_removed(client, ctx):
    chat_id = (await _import(client, _st_file(("Ann",)))).json()["chat"]["id"]
    slot = (await _members(client, chat_id))[0]
    bad = await client.put(f"/api/chats/{chat_id}/members/{slot['slot_id']}", json={"is_me": True})
    assert bad.status_code == 400
    gone = await client.delete(f"/api/chats/{chat_id}/members/{slot['slot_id']}")
    assert gone.status_code == 200 and gone.json()["members"] == []


async def test_talkativeness_takes_every_whole_percent(client, ctx):
    ann = await _character(client, "Ann", first_mes="Hi")
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    for value in (37, 0, 100, 1):
        saved = await client.put(f"/api/chats/{chat_id}/members/{ann['id']}", json={"talkativeness": value})
        assert saved.status_code == 200 and saved.json()["members"][0]["talkativeness"] == value
        assert (await _members(client, chat_id))[0]["talkativeness"] == value
    for bad in (101, -1, 12.5, "40", True):
        refused = await client.put(f"/api/chats/{chat_id}/members/{ann['id']}", json={"talkativeness": bad})
        assert refused.status_code == 400, bad
    assert (await _members(client, chat_id))[0]["talkativeness"] == 1


async def test_our_own_file_carries_empty_places_and_a_missing_character_becomes_one(client, ctx):
    chat_id = (await _import(client, _st_file(("Ann",)))).json()["chat"]["id"]
    slot = (await _members(client, chat_id))[0]
    await client.put(f"/api/chats/{chat_id}/members/{slot['slot_id']}", json={"talkativeness": 37})
    exported = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).content
    again = (await client.post("/api/chats/import.jsonl", content=exported)).json()
    members = await _members(client, again["chat"]["id"])
    assert [(m["name"], m["empty"], m["talkativeness"]) for m in members] == [("Ann", True, 37)]
    assert again["empty_members"] == ["Ann"]
