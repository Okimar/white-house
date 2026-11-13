"""Stage 5: lorebooks - storage, levels, the finer rules of the engine, ST files, over HTTP."""

from __future__ import annotations

import asyncio
import json

from tavern.core.prompt import estimate_tokens
from tavern.storage.db import Database
from tavern.storage.migrations import load_migrations


def test_upgrade_keeps_bubbles_links_vectors_hits_and_history(tmp_path):
    db = Database(tmp_path / "old.sqlite3")
    db.migrate([m for m in load_migrations() if m.version <= 8])
    db.execute("INSERT INTO characters (id, name, data) VALUES ('c', 'Ann', '{}')")
    db.execute("INSERT INTO chats (id, title) VALUES ('h', 'H')")
    # the child is written before its parent on purpose: the copy must not care about order
    db.execute("INSERT INTO character_blocks (id, character_id, parent_id, title, chance) VALUES ('k', 'c', NULL, 'Kid', 40)")
    db.execute("INSERT INTO character_blocks (id, character_id, title) VALUES ('p', 'c', 'Parent')")
    db.execute("UPDATE character_blocks SET parent_id = 'p' WHERE id = 'k'")
    db.execute("INSERT INTO character_blocks (id, character_id, title) VALUES ('z', 'c', 'Other')")
    db.execute("INSERT INTO block_links (character_id, a, b) VALUES ('c', 'k', 'z')")
    db.execute("INSERT INTO block_embeddings VALUES ('p', 'm', 'hash', x'00')")
    db.execute("INSERT INTO chat_block_hits (chat_id, block_id, hit_gen, missed) VALUES ('h', 'k', 4, 1)")
    db.execute("INSERT INTO character_versions (id, character_id, started_at) VALUES (1, 'c', 't')")
    db.execute("INSERT INTO character_steps (character_id, version_id, at, what, before) VALUES ('c', 1, 't', 'w', x'00')")

    db.migrate()
    kid = db.fetchone("SELECT * FROM character_blocks WHERE id = 'k'")
    assert (kid["owner_id"], kid["character_id"], kid["lorebook_id"], kid["parent_id"], kid["chance"], kid["rules"]) \
        == ("c", "c", None, "p", 40, "{}")
    assert [tuple(r) for r in db.fetchall("SELECT owner_id, a, b FROM block_links")] == [("c", "k", "z")]
    assert db.fetchone("SELECT text_hash FROM block_embeddings")["text_hash"] == "hash"
    assert tuple(db.fetchone("SELECT hit_gen, missed FROM chat_block_hits")) == (4, 1)
    assert db.fetchone("SELECT owner_id FROM character_steps")["owner_id"] == "c"

    # deleting things still takes what hangs on them
    db.execute("DELETE FROM character_blocks WHERE id = 'p'")
    assert db.fetchone("SELECT COUNT(*) AS n FROM character_blocks")["n"] == 1  # the kid went with it
    assert db.fetchone("SELECT COUNT(*) AS n FROM block_links")["n"] == 0
    db.execute("DELETE FROM characters WHERE id = 'c'")
    assert db.fetchone("SELECT COUNT(*) AS n FROM character_blocks")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM character_steps")["n"] == 0  # history goes with its owner
    db.close()


# --- over HTTP --------------------------------------------------------------------------------


async def setup(client, **card):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind.", **card})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    return ann, chat_id


async def book(client, name, *entries, **fields):
    made = await client.post("/api/lorebooks", json={"name": name, **fields})
    assert made.status_code == 201, made.text
    book_id = made.json()["id"]
    for entry in entries:
        response = await client.post(f"/api/lorebooks/{book_id}/blocks", json=entry)
        assert response.status_code == 201, response.text
    return book_id


async def use(client, scope, ids, target=None):
    response = await client.put("/api/lorebook-uses", json={"scope": scope, "target": target, "lorebook_ids": ids})
    assert response.status_code == 200, response.text


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


async def seen(client, chat_id):
    reply = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    return (await client.get(f"/api/messages/{reply['id']}/prompt")).json()


def sent(fake_llm):
    return "\n".join(m["content"] for m in fake_llm.requests[-1]["messages"])


async def test_books_are_made_listed_renamed_and_checked(client, ctx):
    events = []
    ctx.events.on("lorebooks.changed", lambda **kw: events.append(kw["lorebook_id"]))
    world = await book(client, "Мир", {"title": "Город", "content": "Большой.", "mode": "always"})
    listed = (await client.get("/api/lorebooks")).json()
    assert [(b["name"], b["blocks"], b["global"]) for b in listed["lorebooks"]] == [("Мир", 1, False)]
    assert listed["settings"] == {"budget_tokens": 2000, "budget_percent": 25, "recursion": 2}  # 25 %: SillyTavern's
    renamed = await client.put(f"/api/lorebooks/{world}", json={"name": "Мир Эйры", "description": "Север."})
    assert renamed.json()["name"] == "Мир Эйры"
    assert (await client.post(f"/api/lorebooks/{world}/undo")).json()["undone"]["what"] == "Книга изменена"
    assert (await client.get(f"/api/lorebooks/{world}")).json()["name"] == "Мир"
    assert (await client.post(f"/api/lorebooks/{world}/history/close")).status_code == 200  # closing the window
    assert (await client.post("/api/lorebooks/nope/history/close")).status_code == 404
    assert (await client.post("/api/lorebooks", json={"name": " "})).json()["error"]["code"] == "bad_lorebook"
    bad_rule = await client.post(f"/api/lorebooks/{world}/blocks", json={"title": "x", "rules": {"logic": "maybe"}})
    assert bad_rule.json()["error"]["code"] == "bad_block"
    bad_setting = await client.put("/api/settings", json={"lorebooks": {"recursion": 99}})
    assert bad_setting.json()["error"]["code"] == "bad_lorebook"
    assert (await client.delete(f"/api/lorebooks/{world}")).status_code == 200
    assert (await client.get("/api/lorebooks")).json()["lorebooks"] == []
    assert events == [world] * 3


async def test_all_four_levels_work_together_and_a_book_counts_once(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    me = (await client.post("/api/characters", json={"name": "Me"})).json()
    await client.post(f"/api/chats/{chat_id}/members", json={"character_id": me["id"], "is_me": True})
    shared = await book(client, "Общий", {"title": "Солнце", "content": "Светит всегда.", "mode": "always"})
    chatty = await book(client, "Чат", {"title": "Таверна", "content": "Пахнет элем.", "mode": "always"})
    hers = await book(client, "Анин", {"title": "Сад", "content": "Розы у дома.", "mode": "always"})
    mine = await book(client, "Мой", {"title": "Меч", "content": "Ржавый меч.", "mode": "always"})
    await use(client, "global", [shared])
    await use(client, "chat", [chatty, shared], target=chat_id)
    await use(client, "character", [hers], target=ann["id"])
    await use(client, "character", [mine], target=me["id"])

    await send(client, ctx, chat_id, "Привет")
    text = sent(fake_llm)
    for piece in ("[Чат › Таверна]\nПахнет элем.", "[Анин › Сад]\nРозы у дома.", "[Мой › Меч]\nРжавый меч.",
                  "[Общий › Солнце]\nСветит всегда."):
        assert piece in text
    assert text.count("Светит всегда.") == 1  # on globally and in the chat: once
    saw = await seen(client, chat_id)
    lore = [e["label"] for e in saw["entries"] if e["kind"] == "lore"]
    assert lore == ["Чат › Таверна", "Общий › Солнце", "Анин › Сад", "Мой › Меч"]  # chat, members, global
    assert (await client.get(f"/api/lorebook-uses?scope=chat&target={chat_id}")).json() == \
        {"lorebook_ids": [chatty, shared]}
    listed = {b["name"]: b for b in (await client.get("/api/lorebooks")).json()["lorebooks"]}
    assert (listed["Общий"]["global"], listed["Общий"]["chats"], listed["Анин"]["characters"]) == (True, 1, 1)
    await client.delete(f"/api/chats/{chat_id}")
    assert ctx.lorebooks.places_of(chatty) == []  # a deleted chat takes its switches along


async def test_entries_go_to_their_places(client, ctx, fake_llm):
    ann, chat_id = await setup(client, mes_example="<START>\n{{char}}: Hi!")
    note = await client.post("/api/notes", json={"scope": "chat", "owner_id": chat_id, "text": "NOTE", "depth": 1})
    assert note.status_code == 201, note.text
    world = await book(client, "W",
                       {"title": "Before", "content": "BEFORE", "mode": "always"},
                       {"title": "After", "content": "AFTER", "mode": "always", "rules": {"place": "card_after"}},
                       {"title": "Deep", "content": "DEEP", "mode": "always", "depth": 0},
                       {"title": "Top", "content": "ANTOP", "mode": "always", "rules": {"place": "an_top"}},
                       {"title": "Bottom", "content": "ANBOTTOM", "mode": "always", "rules": {"place": "an_bottom"}},
                       {"title": "Ex", "content": "EXTOP", "mode": "always", "rules": {"place": "examples_top"}})
    await use(client, "chat", [world], target=chat_id)
    await send(client, ctx, chat_id, "hello")
    text = sent(fake_llm)
    order = [text.index(x) for x in ("BEFORE", "Ann is kind.", "AFTER", "EXTOP", "Hi!", "ANTOP", "NOTE",
                                     "ANBOTTOM", "hello", "DEEP")]
    assert order == sorted(order), text


async def test_budget_drops_the_last_entries_and_says_so(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    long = "слово " * 300
    world = await book(client, "W", *({"title": f"T{i}", "content": f"{long} #{i}", "mode": "always"}
                                      for i in range(4)))
    await use(client, "chat", [world], target=chat_id)
    one = ctx.blocks.list(world)[0]["content"]
    await client.put("/api/settings", json={"lorebooks": {"budget_tokens": estimate_tokens(one) * 2 + 1}})
    await send(client, ctx, chat_id, "hi")
    text = sent(fake_llm)
    assert "#0" in text and "#1" in text and "#2" not in text and "#3" not in text
    assert (await seen(client, chat_id))["meta"]["dropped"]["lore"] == ["T2", "T3"]


async def test_chains_start_from_the_chat_and_from_character_bubbles(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await client.post(f"/api/characters/{ann['id']}/blocks",
                      json={"title": "Прошлое", "content": "Была в гильдии воров.", "mode": "always"})
    world = await book(client, "W",
                       {"title": "Гильдия", "content": "Ею правит Крыса.", "sticky": 0},
                       {"title": "Крыса", "content": "Хромой главарь.", "sticky": 0})
    await use(client, "global", [world])
    await send(client, ctx, chat_id, "погода")
    text = sent(fake_llm)
    assert "Ею правит Крыса." in text and "Хромой главарь." in text  # bubble -> guild -> rat
    await client.put("/api/settings", json={"lorebooks": {"recursion": 0}})
    await send(client, ctx, chat_id, "погода")
    assert "Хромой главарь." not in sent(fake_llm)


def world_file():
    return {
        "name": "Эйра",
        "scan_depth": 4,
        "entries": {
            "1": {"uid": 1, "displayIndex": 1, "key": ["дракон"], "keysecondary": ["ночь"], "selective": True,
                  "selectiveLogic": 3, "comment": "Дракон Ысс", "content": "Спит в горе.", "constant": False,
                  "disable": False, "order": 50, "position": 4, "depth": 2, "role": 1, "probability": 40,
                  "useProbability": True, "sticky": 2, "cooldown": 3, "delay": 1, "scanDepth": 5,
                  "caseSensitive": True, "matchWholeWords": True, "group": "beasts,night", "groupOverride": True,
                  "groupWeight": 70, "excludeRecursion": True, "preventRecursion": True,
                  "characterFilter": {"isExclude": True, "names": ["Ann", "Stranger"], "tags": ["x"]},
                  "automationId": "keep-me"},
            "0": {"uid": 0, "displayIndex": 0, "key": ["город"], "comment": "", "content": "Столица.",
                  "constant": True, "position": 1},
        },
    }


async def test_st_world_file_comes_in_and_goes_back(client, ctx):
    ann, _ = await setup(client)
    made = await client.post("/api/lorebooks/import?name=file.json", content=json.dumps(world_file()).encode())
    assert made.status_code == 201, made.text
    book_id = made.json()["id"]
    assert made.json()["name"] == "Эйра" and made.json()["extra"] == {"scan_depth": 4}
    city, dragon = ctx.blocks.list(book_id)
    assert (city["title"], city["mode"], city["rules"]) == ("город", "always", {"title_key": True, "place": "card_after"})
    assert (dragon["title"], dragon["mode"], dragon["depth"], dragon["role"], dragon["chance"], dragon["sticky"]) == \
        ("Дракон Ысс", "search", 2, "user", 40, 2)
    assert dragon["rules"] == {
        "title_key": False, "keys2": ["ночь"], "logic": "and_all", "scan": 5, "cooldown": 3, "delay": 1,
        "case": True, "whole": True, "groups": ["beasts", "night"], "main": True, "weight": 70,
        "not_woken": True, "no_wake": True, "filter": {"mode": "except", "ids": [ann["id"]]}, "order": 50}

    out = (await client.get(f"/api/lorebooks/{book_id}/export.json")).json()
    entry = next(e for e in out["entries"].values() if e["comment"] == "Дракон Ысс")
    original = world_file()["entries"]["1"]
    for key in ("key", "keysecondary", "selectiveLogic", "position", "depth", "role", "probability", "sticky",
                "cooldown", "delay", "scanDepth", "caseSensitive", "matchWholeWords", "group", "groupOverride",
                "groupWeight", "excludeRecursion", "preventRecursion", "automationId", "order"):
        assert entry[key] == original[key], key
    assert entry["characterFilter"] == {"isExclude": True, "names": ["Ann"], "tags": ["x"]}
    assert out["scan_depth"] == 4 and out["name"] == "Эйра"


async def test_our_export_brings_back_nesting_and_links(client, ctx):
    world = await book(client, "W", {"title": "Город", "content": "c"})
    city = ctx.blocks.list(world)[0]["id"]
    street = (await client.post(f"/api/lorebooks/{world}/blocks",
                                json={"title": "Улица", "content": "s", "parent_id": city,
                                      "rules": {"groups": ["g"]}})).json()["id"]
    await client.post(f"/api/lorebooks/{world}/links", json={"a": city, "b": street})
    out = (await client.get(f"/api/lorebooks/{world}/export.json")).content
    again = (await client.post("/api/lorebooks/import", content=out)).json()["id"]
    blocks = {b["title"]: b for b in ctx.blocks.list(again)}
    assert blocks["Улица"]["parent_id"] == blocks["Город"]["id"] and blocks["Улица"]["rules"] == {"groups": ["g"]}
    assert ctx.blocks.links([again]) == [tuple(sorted((blocks["Город"]["id"], blocks["Улица"]["id"])))]


async def test_a_card_naming_its_lorebook_gets_it(client, ctx):
    world = await book(client, "Эйра")
    card = {"spec": "chara_card_v2", "spec_version": "2.0",
            "data": {"name": "Bo", "extensions": {"world": "эйра"}}}
    bo = (await client.post("/api/characters", json=card)).json()
    assert ctx.lorebooks.uses("character", bo["id"]) == [world]


async def test_import_names_the_book_by_the_file_inside_name_not_the_file_name(client):
    tree = {"name": "The Garden 1.5", "description": "d", "entries": {}, "tavern_tree": {"blocks": []}}
    made = await client.post("/api/lorebooks/import?name=true.json", content=json.dumps(tree).encode())
    assert made.status_code == 201, made.text
    assert made.json()["name"] == "The Garden 1.5"
    padded = await client.post("/api/lorebooks/import?name=a.json", content=json.dumps({"name": "  Sad  ", "entries": {}}).encode())
    assert padded.json()["name"] == "Sad"
    blank = await client.post("/api/lorebooks/import?name=true.json", content=json.dumps({"name": "   ", "entries": {}}).encode())
    assert blank.json()["name"] == "true"
