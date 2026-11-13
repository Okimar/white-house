"""Stage 4в over HTTP: author's notes on three levels; once / hidden / visible; edits by numbers."""

from __future__ import annotations

import asyncio


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    return ann, chat_id


async def note(client, scope, owner_id=None, **fields):
    response = await client.post("/api/notes", json={"scope": scope, "owner_id": owner_id, **fields})
    assert response.status_code == 201, response.text
    return response.json()


async def wait(ctx, response):
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


async def send(client, ctx, chat_id, text):
    await wait(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": text}))


def sent(fake_llm):
    return [m["content"] for m in fake_llm.requests[-1]["messages"]]


async def test_three_levels_fire_by_interval_at_their_depth(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await note(client, "global", text="GLOBAL", depth=0)
    await note(client, "character", ann["id"], text="ANN NOTE", depth=0, interval=1)  # every other reply
    await note(client, "chat", chat_id, text="CHAT {{char}}", depth=0, role="user")

    await send(client, ctx, chat_id, "hello")  # reply 1: the every-2nd note stays out
    first = sent(fake_llm)
    # one depth, as SillyTavern: the user text first, the system one nearest the next message
    assert first[-2] == "CHAT Ann" and first[-1] == "GLOBAL"
    await send(client, ctx, chat_id, "again")  # reply 2: global and Ann's go as one system message
    assert sent(fake_llm)[-2:] == ["CHAT Ann", "GLOBAL\nANN NOTE"]

    window = (await client.get(f"/api/chats/{chat_id}/notes")).json()
    assert [n["text"] for n in window["global"]] == ["GLOBAL"]
    assert window["characters"][0]["notes"][0]["text"] == "ANN NOTE"
    assert window["chat"][0]["role"] == "user"


async def test_once_note_is_not_kept_and_hidden_one_is_kept_out_of_sight(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await note(client, "chat", chat_id, text="ONCE", depth=0)  # the default kind
    await note(client, "chat", chat_id, text="HIDDEN", depth=0, mode="hidden")
    await send(client, ctx, chat_id, "one")
    await send(client, ctx, chat_id, "two")
    everything = "\n".join(sent(fake_llm))
    assert everything.count("ONCE") == 1  # the earlier one is not in the history
    assert everything.count("HIDDEN") == 2  # the kept one after "one" and the fresh one

    kept = (await client.get(f"/api/chats/{chat_id}/message-notes")).json()["notes"]
    messages = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    users = [m["id"] for m in messages if m["role"] == "user"]
    assert {(k["kind"], k["text"]) for k in kept} == {("hidden", "HIDDEN")}
    assert sorted(k["message_id"] for k in kept) == sorted(users)
    assert (await client.delete(f"/api/chats/{chat_id}/message-notes?kind=hidden")).json() == {"removed": 2}
    assert (await client.get(f"/api/chats/{chat_id}/message-notes")).json()["notes"] == []


async def test_saved_note_stays_in_history_and_regenerating_does_not_double_it(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    made = await note(client, "chat", chat_id, text="KEEP", depth=0, mode="visible")
    await send(client, ctx, chat_id, "one")
    await client.put(f"/api/notes/{made['id']}", json={"enabled": False})
    await send(client, ctx, chat_id, "two")
    assert sent(fake_llm).count("KEEP") == 1  # still there after "one", though the note is off now

    await client.put(f"/api/notes/{made['id']}", json={"enabled": True})
    await send(client, ctx, chat_id, "three")
    await wait(ctx, await client.post(f"/api/chats/{chat_id}/regenerate"))
    assert sent(fake_llm).count("KEEP") == 2  # after "one" and after "three", not a third copy
    saved = (await client.get(f"/api/chats/{chat_id}/message-notes?kinds=visible")).json()["notes"]
    assert len(saved) == 2

    reply = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    saw = (await client.get(f"/api/messages/{reply['id']}/prompt?swipe=1")).json()
    assert [e["kind"] for e in saw["entries"]].count("note") == 2


async def test_edit_by_numbers(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    seen = []
    ctx.events.on("message_notes.changed", lambda **kw: seen.append(kw["chat_id"]))
    for text in ("a", "b", "c"):
        await send(client, ctx, chat_id, text)  # messages 1..6

    picked = (await client.post(f"/api/chats/{chat_id}/message-notes/by-numbers",
                                json={"numbers": "1-2 5"})).json()["items"]
    assert [p["number"] for p in picked] == [1, 2, 5] and all(p["notes"] == [] for p in picked)

    made = await client.post(f"/api/chats/{chat_id}/message-notes/add-by-numbers",
                             json={"numbers": "1-2 5", "text": "Night.", "role": "user"})
    assert len(made.json()["notes"]) == 3
    one = picked[0]["message"]["id"]
    for text in ("Rain.", "Wind.", "Fog."):  # any number of notes on one message
        extra = await client.post(f"/api/chats/{chat_id}/message-notes", json={"message_id": one, "text": text})
        assert extra.status_code == 201 and extra.json()["kind"] == "visible"
    changed = await client.put(f"/api/message-notes/{extra.json()['id']}", json={"text": "Snow.", "kind": "hidden"})
    assert (changed.json()["text"], changed.json()["kind"]) == ("Snow.", "hidden")
    again = await client.post(f"/api/chats/{chat_id}/message-notes/add-by-numbers",
                              json={"numbers": "1", "text": "Moon.", "kind": "hidden"})
    assert again.json()["notes"][0]["kind"] == "hidden"
    first = (await client.post(f"/api/chats/{chat_id}/message-notes/by-numbers", json={"numbers": "1"})).json()
    assert [n["text"] for n in first["items"][0]["notes"]] == ["Night.", "Rain.", "Wind.", "Snow.", "Moon."]

    switched = await client.post(f"/api/chats/{chat_id}/message-notes/kind-by-numbers",
                                 json={"numbers": "1-2", "kind": "hidden"})
    assert switched.json() == {"changed": 6}
    kinds = {n["kind"] for n in (await client.get(f"/api/chats/{chat_id}/message-notes")).json()["notes"]
             if n["message_id"] in (one, picked[1]["message"]["id"])}
    assert kinds == {"hidden"}

    await send(client, ctx, chat_id, "d")
    assert sent(fake_llm).count("Night.") == 3
    assert "Snow." in " ".join(sent(fake_llm))  # neighbouring system notes go as one message

    removed = await client.post(f"/api/chats/{chat_id}/message-notes/delete-by-numbers", json={"numbers": "1"})
    assert removed.json() == {"removed": 5}
    bad = await client.post(f"/api/chats/{chat_id}/message-notes/by-numbers", json={"numbers": "99"})
    assert bad.json()["error"]["code"] == "bad_numbers"
    assert seen == [chat_id] * 8  # add for all, 3 adds, edit, add for one, kind, delete


async def test_notes_display_setting(client):
    assert (await client.put("/api/settings", json={"notes_display": "all"})).status_code == 200
    assert (await client.get("/api/settings")).json()["notes_display"] == "all"
    bad = await client.put("/api/settings", json={"notes_display": "many"})
    assert bad.json()["error"]["code"] == "bad_request"


async def test_delete_confirmation_can_be_switched_off(client):
    assert (await client.put("/api/settings", json={"skip_delete_confirm": True})).json()["skip_delete_confirm"] is True
    assert (await client.put("/api/settings", json={"open_new_bubble": True})).json()["open_new_bubble"] is True
    assert (await client.put("/api/settings", json={"open_new_bubble": "yes"})).status_code == 400
    bad = await client.put("/api/settings", json={"skip_delete_confirm": "yes"})
    assert bad.json()["error"]["code"] == "bad_request"


async def test_notes_are_checked_and_announced(client, ctx):
    ann, chat_id = await setup(client)
    seen = []
    ctx.events.on("notes.changed", lambda **kw: seen.append((kw["scope"], kw["owner_id"])))
    for body, code in (({"scope": "chat"}, "bad_note"), ({"scope": "x"}, "bad_note"),
                       ({"scope": "global", "depth": -1}, "bad_note"),
                       ({"scope": "global", "interval": -1}, "bad_note"),
                       ({"scope": "global", "mode": "saved"}, "bad_note"),
                       ({"scope": "global", "role": "narrator"}, "bad_note"),
                       ({"scope": "global", "colour": "red"}, "bad_request")):
        response = await client.post("/api/notes", json=body)
        assert response.json()["error"]["code"] == code, body
    made = await note(client, "character", ann["id"], text="x", title="Mood")
    assert (made["depth"], made["role"], made["interval"], made["mode"], made["enabled"]) == (
        4, "system", 0, "once", True)
    await client.put(f"/api/notes/{made['id']}", json={"depth": 2})
    await client.delete(f"/api/notes/{made['id']}")
    assert seen == [("character", ann["id"])] * 3

    await note(client, "chat", chat_id, text="gone with the chat")
    await client.delete(f"/api/chats/{chat_id}")
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM author_notes")["n"] == 0


async def test_chat_file_keeps_its_notes(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await note(client, "chat", chat_id, text="CHAT NOTE", depth=2, interval=3, mode="visible")
    await send(client, ctx, chat_id, "hi")
    await client.post(f"/api/chats/{chat_id}/message-notes/add-by-numbers", json={"numbers": "2", "text": "By hand"})
    exported = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).text

    copy = (await client.post("/api/chats/import.jsonl", content=exported.encode())).json()["chat"]
    notes = (await client.get(f"/api/chats/{copy['id']}/notes")).json()["chat"]
    assert [(n["text"], n["depth"], n["interval"], n["mode"]) for n in notes] == [("CHAT NOTE", 2, 3, "visible")]
    attached = (await client.get(f"/api/chats/{copy['id']}/message-notes")).json()["notes"]
    messages = (await client.get(f"/api/chats/{copy['id']}/messages")).json()["messages"]
    assert [a["message_id"] for a in attached if a["text"] == "By hand"] == [messages[1]["id"]]


async def test_old_notes_are_moved_to_the_new_kinds(tmp_path):
    from tavern.storage.db import Database
    from tavern.storage.migrations import load_migrations

    db = Database(tmp_path / "old.sqlite3")
    shipped = load_migrations()
    db.migrate([m for m in shipped if m.version <= 5])
    db.execute("INSERT INTO characters (id, name, data) VALUES ('c', 'C', '{}')")
    db.execute("INSERT INTO chats (id, title) VALUES ('h', 'H')")
    db.execute("INSERT INTO messages (id, chat_id, depth, role) VALUES ('m', 'h', 0, 'user')")
    for note_id, mode, interval in (("a", "hidden", 1), ("b", "saved", 3)):
        db.execute("INSERT INTO author_notes (id, scope, chat_id, mode, interval, created_at, updated_at) "
                   "VALUES (?, 'chat', 'h', ?, ?, 'now', 'now')", (note_id, mode, interval))
    db.execute("INSERT INTO message_notes (id, chat_id, message_id, kind, note_id, text, created_at) "
               "VALUES ('k', 'h', 'm', 'saved', 'b', 'kept', 'now'), ('x', 'h', 'm', 'mark', 'a', 'mark', 'now')")
    db.migrate()
    notes = {r["id"]: (r["mode"], r["interval"]) for r in db.fetchall("SELECT * FROM author_notes")}
    assert notes == {"a": ("once", 0), "b": ("visible", 2)}
    assert [(r["id"], r["kind"]) for r in db.fetchall("SELECT * FROM message_notes")] == [("k", "visible")]
    db.close()
