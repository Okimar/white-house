"""Memory++ M6 through the HTTP API on the fake model: «Лица», mini-cards of side characters. Off by default and
migration 0006; a recurring side character gets a flat mini-card made by the small model (a one-message cameo and a
main character do not); later chapters update it (never a locked one); it goes into a reply only when named or found by
the reply's one meaning request, compact, as one entry; reuse in another chat (a favourite only when named, «Использовать
в этом чате» off keeps it out); hand edit with a lock, versions, delete / restore, merge, aliases and undo; «Сделать
настоящим персонажем»; development and «Слово и нити» of a side character through the M4 / M5 paths; a reply cancels
the request and it is made again. Also the owner's request of 2026-11-11: one model for every task of the memory, and an
empty model is the connection's own."""

from __future__ import annotations

import asyncio
import json

from tests.integration.test_memory_chapters import chapter_chat, rerun, view
from tests.integration.test_memory_plugin import idle, memory_module, objects, say, switch
from tests.integration.test_memory_summaries import answer, models_on, plugin_module
from tests.integration.test_memory_threads import entries, reply, threads_answer

CARD = "Борин — седой ворчливый хозяин таверны с деревянной ногой."
CONTENTS = {1: "Борин наливает кружку и молчит.", 3: "Трактирщик Борин ворчит про цены.", 5: "Мимо проходит Грег."}


def faces_answer(*items: dict) -> str:
    return json.dumps({"faces": list(items)}, ensure_ascii=False)


def face(name: str, card: str, code: str = "", aliases=()) -> dict:
    return {"face": code, "name": name, "aliases": list(aliases), "card": card}


FIRST = faces_answer(face("Борин", CARD, aliases=("трактирщик",)),
                     face("Грег", "Грег — заезжий торговец."),  # named in one message only: a cameo
                     face("Ann", "Ann — хозяйка."))  # a member: never a mini-card


def face_requests(fake_llm) -> list[dict]:
    return [r for r in fake_llm.requests if "its side characters" in r["messages"][0]["content"]]


def face_rows(ctx) -> list[dict]:
    return [dict(r) for r in ctx.db.fetchall(
        "SELECT id, name, aliases, card, favourite, made_by, locked, deleted, merged_into, character_id "
        "FROM p_memory_faces ORDER BY created_at, rowid")]


async def face_chat(client, ctx, fake_llm, *, settings=None, answers=(FIRST,), run=True):
    """20 messages, one chapter closed at the marker (№1-№12) where Борин is named twice and Грег once; Ann a member;
    «Лица» on; the job done once."""
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    ann = setup["ann"]
    assert (await client.post(f"/api/chats/{chat_id}/members", json={"character_id": ann["id"]})).status_code == 201
    for index, text in CONTENTS.items():
        ctx.chats.set_content(ids[index], text)
    await models_on(client, **{"faces": True, **(settings or {})})
    if run:
        fake_llm.queue(*answers)
        await switch(client, chat_id)
        await idle(ctx, chat_id)
    return setup, chat_id, ids


def more(ctx, chat_id, ann_id, start, marks) -> None:
    """Twelve more messages (user first) with a chapter marker; `marks`: number -> text."""
    for i in range(start, start + 12):
        text = marks.get(i, f"дальше {i}")
        extra = {} if i % 2 == 0 else {"character_id": ann_id, "name": "Ann"}
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", text, **extra)


def item(result, name) -> dict:
    return next(f for f in result["faces"] if f["name"] == name)


def faces_entry(saw) -> str | None:
    return entries(saw).get("plugin:memory:faces")


# -- off by default ----------------------------------------------------------------------------------


async def test_faces_are_off_by_default_and_migration_0006(client, ctx, fake_llm):
    _, chat_id, _ = await face_chat(client, ctx, fake_llm, settings={"faces": False}, answers=())
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert face_requests(fake_llm) == [] and face_rows(ctx) == []
    assert {"p_memory_faces", "p_memory_face_versions", "p_memory_face_chats", "p_memory_face_runs",
            "p_memory_faces_gone", "p_memory_face_chats_chat"} <= set(objects(ctx))
    assert ctx.db.fetchone("SELECT schema_version AS v FROM plugins WHERE id = 'memory'")["v"] >= 6
    plugins = (await client.get("/api/plugins")).json()["plugins"]
    assert next(p for p in plugins if p["id"] == "memory")["values"]["faces"] is False
    result = await view(client, chat_id, "faces")
    assert result["on"] is False and result["faces"] == []
    saw = await reply(client, ctx, chat_id, "Борин, налей")
    assert faces_entry(saw) is None


# -- made by the job ---------------------------------------------------------------------------------


async def test_a_recurring_side_character_gets_a_flat_mini_card_and_a_cameo_or_member_does_not(client, ctx, fake_llm):
    setup, chat_id, ids = await face_chat(client, ctx, fake_llm)
    sent = face_requests(fake_llm)
    assert len(sent) == 1
    system, user = sent[0]["messages"][0]["content"], sent[0]["messages"][-1]["content"]
    assert "Never a card for the main characters" in system and "Main characters: Ann, User" in user
    assert "Борин ворчит" in user and "№13" not in user  # only the closed chapter
    rows = face_rows(ctx)
    assert [r["name"] for r in rows] == ["Борин"]  # Грег: one message only; Ann: a member
    row = rows[0]
    assert row["card"] == CARD and json.loads(row["aliases"]) == ["трактирщик"] and row["made_by"] == "auto"
    assert not row["locked"] and not row["favourite"]
    link = ctx.db.fetchone("SELECT chat_id, first_message_id, in_use FROM p_memory_face_chats")
    assert (link["chat_id"], link["first_message_id"], link["in_use"]) == (chat_id, ids[1], 1)
    # flat: no character, no bubbles anywhere
    assert [c["name"] for c in ctx.characters.list()] == ["Ann", "Bob"]
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM character_blocks")["n"] == 0
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_face_versions")["n"] == 1
    # the card got a vector of this chat (found by meaning with the reply's one request)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_vectors WHERE item_id = ?",
                           (f"f:{row['id']}@{chat_id}",))["n"] == 1
    result = await view(client, chat_id, "faces")
    shown = item(result, "Борин")
    assert shown["linked"] and shown["in_use"] and shown["first_no"] == 2 and shown["chats"] == 1
    assert ctx.db.fetchone("SELECT state FROM p_memory_face_runs")["state"] == "done"


async def test_later_chapters_update_the_card_but_never_a_locked_one(client, ctx, fake_llm):
    setup, chat_id, _ = await face_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    more(ctx, chat_id, ann, 20, {21: "Борин чинит дверь.", 23: "Борин зовёт хозяина.", 24: "*** Глава"})
    fake_llm.queue(faces_answer(face("Борин", "Борин — хозяин таверны, чинит дверь сам.", "F1", ("хозяин",))))
    await rerun(client, ctx, chat_id)
    user = face_requests(fake_llm)[-1]["messages"][-1]["content"]
    assert f"F1: Борин (also: трактирщик)\nCard: {CARD}" in user
    row = face_rows(ctx)[0]
    assert row["card"] == "Борин — хозяин таверны, чинит дверь сам." and json.loads(row["aliases"]) == ["трактирщик", "хозяин"]
    assert len(face_rows(ctx)) == 1  # the same person, not a new face
    await view(client, chat_id, "face_lock", face_id=row["id"], locked=True)
    more(ctx, chat_id, ann, 32, {33: "Борин спит.", 35: "Борин храпит.", 36: "*** Глава"})
    fake_llm.queue(faces_answer(face("Борин", "Совсем другой текст.", "F1")))
    await rerun(client, ctx, chat_id)
    assert "[locked]" in face_requests(fake_llm)[-1]["messages"][-1]["content"]
    assert face_rows(ctx)[0]["card"] == "Борин — хозяин таверны, чинит дверь сам."


# -- in the prompt -------------------------------------------------------------------------------------


async def test_sent_only_when_named_or_found_by_meaning_compact_with_one_meaning_request(client, ctx, fake_llm):
    _, chat_id, _ = await face_chat(client, ctx, fake_llm)
    saw = await reply(client, ctx, chat_id, "погода хорошая")
    assert faces_entry(saw) is None
    fake_llm.embedding_requests.clear()
    saw = await reply(client, ctx, chat_id, "Борину налей ещё")  # another ending of the name
    assert len(fake_llm.embedding_requests) == 1  # still the reply's one meaning request
    text = faces_entry(saw)
    assert text.startswith("[Второстепенные лица") and f"- Борин (также: трактирщик): {CARD}" in text
    entry = next(e for e in saw["entries"] if e["source"] == "plugin:memory:faces")
    assert entry["label"] == "Лица: Борин" and len(text) // 4 <= plugin_module("faces").PROMPT_TOKENS
    saw = await reply(client, ctx, chat_id, "ворчливый седой хозяин с деревянной ногой")  # by meaning, no name
    assert CARD in (faces_entry(saw) or "")


async def test_reused_in_another_chat_only_as_a_favourite_and_only_when_named(client, ctx, fake_llm):
    setup, chat_id, _ = await face_chat(client, ctx, fake_llm)
    row = face_rows(ctx)[0]
    other = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    await client.post(f"/api/chats/{other}/members", json={"character_id": setup["bob"]["id"]})
    for i in range(4):
        say(ctx, other, "user" if i % 2 == 0 else "assistant", f"другой чат {i}")
    await switch(client, other)
    await idle(ctx, other)
    assert faces_entry(await reply(client, ctx, other, "Борин, привет")) is None  # not a favourite
    assert item(await view(client, other, "faces", all=True), "Борин")["linked"] is False
    assert (await view(client, other, "faces"))["faces"] == []
    await view(client, chat_id, "face_favourite", face_id=row["id"], favourite=True)
    assert CARD in faces_entry(await reply(client, ctx, other, "Борин, привет"))
    for i in range(3):  # the name leaves the last messages the reply looks at
        say(ctx, other, "user" if i % 2 else "assistant", f"ещё {i}")
    assert faces_entry(await reply(client, ctx, other, "ворчливый седой хозяин с деревянной ногой")) is None
    result = await view(client, other, "faces")
    assert item(result, "Борин")["favourite"] and item(result, "Борин")["in_use"]
    await view(client, other, "face_use", face_id=row["id"], in_use=False)  # not in this chat
    assert faces_entry(await reply(client, ctx, other, "Борин, привет")) is None
    assert CARD in faces_entry(await reply(client, ctx, chat_id, "Борин, привет"))  # the first chat still has it


# -- by hand ---------------------------------------------------------------------------------------------


async def test_hand_edit_rename_aliases_versions_delete_restore_and_undo(client, ctx, fake_llm):
    _, chat_id, _ = await face_chat(client, ctx, fake_llm)
    row = face_rows(ctx)[0]
    result = await view(client, chat_id, "face_edit", face_id=row["id"], name="Борис", aliases=["корчмарь", "Борис"],
                        card="Борис держит корчму у моста.")
    shown = item(result, "Борис")
    assert shown["locked"] and shown["aliases"] == ["корчмарь"] and result["undo"] == "Изменить лицо"
    saw = await reply(client, ctx, chat_id, "корчмарь, налей")  # found by its new alias
    assert "Борис держит корчму у моста." in faces_entry(saw)
    versions = (await view(client, chat_id, "face_versions", face_id=row["id"]))["versions"]
    assert [v["by"] for v in versions] == ["hand", "auto"]
    result = await view(client, chat_id, "face_restore", face_id=row["id"], version_id=versions[1]["id"])
    assert item(result, "Борин")["card"] == CARD and item(result, "Борин")["locked"]
    await view(client, chat_id, "face_delete", face_id=row["id"])
    assert faces_entry(await reply(client, ctx, chat_id, "Борин, налей")) is None
    assert item(await view(client, chat_id, "faces"), "Борин")["deleted"] is True
    await view(client, chat_id, "face_undelete", face_id=row["id"])
    for _ in range(4):  # undelete, delete, restore, edit
        await view(client, chat_id, "undo")
    back = face_rows(ctx)[0]
    assert (back["name"], back["card"], back["locked"], back["deleted"]) == ("Борин", CARD, 0, 0)
    assert len((await view(client, chat_id, "face_versions", face_id=row["id"]))["versions"]) == 1
    bad = await client.post("/api/plugins/memory/actions/face_edit", json={"chat_id": chat_id, "face_id": row["id"],
                                                                           "name": " "})
    assert bad.status_code == 400


async def test_merge_two_cards_of_the_same_person_moves_links_and_records_and_undo(client, ctx, fake_llm):
    setup, chat_id, ids = await face_chat(client, ctx, fake_llm)
    store = plugin_module("store")
    keep = face_rows(ctx)[0]
    twin = store.new_face("Трактирщик", "Хозяин таверны.", aliases=["старик"], by="hand")
    store.put_face(ctx.db, twin, version_by="hand")
    other = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    say(ctx, other, "user", "привет")
    store.link_face(ctx.db, twin["id"], other)
    record = store.new_thread(chat_id, "promise", "face:" + twin["id"], "налью бесплатно", ids[3],
                              to_whom=setup["ann"]["id"], message_id=ids[3])
    store.put_thread(ctx.db, record)
    result = await view(client, chat_id, "face_merge", face_id=twin["id"], into_id=keep["id"])
    merged = item(result, "Борин")
    assert set(merged["aliases"]) == {"трактирщик", "старик"} and merged["chats"] == 2
    assert all(f["name"] != "Трактирщик" for f in (await view(client, chat_id, "faces", all=True))["faces"])
    assert store.get_thread(ctx.db, record["id"])["who"] == "face:" + keep["id"]
    assert result["undo"].startswith("Объединить")
    await view(client, chat_id, "undo")
    assert store.get_face(ctx.db, twin["id"])["merged_into"] == ""
    assert store.get_face(ctx.db, keep["id"])["aliases"] == ["трактирщик"]
    assert store.get_thread(ctx.db, record["id"])["who"] == "face:" + twin["id"]
    assert [link["chat_id"] for link in store.face_links(ctx.db, face_id=twin["id"])] == [other]
    bad = await client.post("/api/plugins/memory/actions/face_merge",
                            json={"chat_id": chat_id, "face_id": keep["id"], "into_id": keep["id"]})
    assert bad.status_code == 409


async def test_make_real_creates_a_proper_character_and_keeps_the_face_linked(client, ctx, fake_llm):
    _, chat_id, _ = await face_chat(client, ctx, fake_llm)
    row = face_rows(ctx)[0]
    result = await view(client, chat_id, "face_make_real", face_id=row["id"])
    made = result["made"]
    character = ctx.characters.get(made["character_id"])
    assert character["name"] == "Борин" and CARD in character["data"]["description"]
    assert "трактирщик" in character["data"]["description"]
    assert ctx.blocks.for_owners([character["id"]]).get(character["id"], []) == []  # flat: no bubble tree
    assert face_rows(ctx)[0]["character_id"] == character["id"]
    assert item(result, "Борин")["character_name"] == "Борин"
    again = await client.post("/api/plugins/memory/actions/face_make_real", json={"chat_id": chat_id, "face_id": row["id"]})
    assert again.status_code == 409
    # where the real character is a member, its card goes and the mini-card does not
    assert (await client.post(f"/api/chats/{chat_id}/members", json={"character_id": character["id"]})).status_code == 201
    saw = await reply(client, ctx, chat_id, "Борин, налей", speaker=next(
        m["character_id"] for m in ctx.members.list(chat_id) if m["name"] == "Ann"))
    assert faces_entry(saw) is None


# -- development and «Слово и нити» of a side character (M4 / M5 paths) ----------------------------------


async def test_development_and_threads_of_a_side_character_reuse_the_m4_m5_paths(client, ctx, fake_llm):
    setup, chat_id, ids = await face_chat(client, ctx, fake_llm, run=False,
                                          settings={"development": True, "threads": True})
    ann = setup["ann"]["id"]
    ctx.chats.set_content(ids[3], "Трактирщик Борин ворчит: обещаю налить тебе бесплатно завтра.")
    promise = {"kind": "promise", "who": "Борин", "to": "Ann", "quote": "обещаю налить тебе бесплатно завтра",
               "message_no": 4, "importance": 2, "knows": []}
    fake_llm.queue(FIRST,  # faces first: Борин is then a person of the development and threads requests
                   json.dumps({"layers": [{"who": "Борин", "text": "Борин теперь злится на Ann."}], "now_lines": []},
                              ensure_ascii=False),
                   threads_answer([promise]))
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    asked = [r["messages"][-1]["content"] for r in fake_llm.requests]
    assert any("Борин\nCard: " + CARD in text for text in asked)  # the development request
    assert any(": Борин" in text and "People:" in text for text in asked)  # the threads request
    face_id = face_rows(ctx)[0]["id"]
    layer = ctx.db.fetchone("SELECT text FROM p_memory_texts WHERE kind = 'development' AND target = ?",
                            ("face:" + face_id,))
    assert layer["text"] == "Борин теперь злится на Ann."
    record = ctx.db.fetchone("SELECT who, to_whom FROM p_memory_threads")
    assert (record["who"], record["to_whom"]) == ("face:" + face_id, ann)
    layers = await view(client, chat_id, "layers")
    assert any(m["is_face"] and m["name"] == "Борин" for m in layers["members"])
    threads = await view(client, chat_id, "threads")
    assert any(g["name"] == "Борин" for g in threads["groups"])
    saw = await reply(client, ctx, chat_id, "Борин, ты обещал налить бесплатно")
    assert "Сейчас: Борин теперь злится на Ann." in faces_entry(saw)
    assert "обещание · Борин → Ann" in entries(saw)[f"plugin:memory:threads:{ann}"]  # given to Ann: Ann knows it


# -- a reply cancels the step ------------------------------------------------------------------------------


async def test_a_reply_cancels_the_faces_request_and_it_is_made_again(client, ctx, fake_llm, monkeypatch):
    _, chat_id, _ = await face_chat(client, ctx, fake_llm, run=False)
    module = plugin_module("faces")
    started: list[int] = []
    gate = asyncio.Event()
    real = module.ask

    async def slow(llm, request):
        started.append(1)
        if len(started) == 1:
            await gate.wait()
        return await real(llm, request)

    monkeypatch.setattr(module, "ask", slow)
    fake_llm.queue(FIRST)
    await switch(client, chat_id)
    for _ in range(300):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started == [1]
    ctx.foreground.enter()  # a reply starts: the step is cancelled at once
    await asyncio.sleep(0.1)
    assert face_rows(ctx) == [] and ctx.jobs.status("memory", f"chat:{chat_id}")["paused"] is True
    ctx.foreground.leave()
    await idle(ctx, chat_id)
    assert len(started) == 2 and [r["name"] for r in face_rows(ctx)] == ["Борин"]


# -- one model for every task (owner, 2026-11-11) -----------------------------------------------------------


async def test_one_memory_model_serves_every_task_and_empty_means_the_connections_own(client, ctx, fake_llm):
    small = (await client.post("/api/connections", json={"name": "small", "base_url": "http://llm/v1",
                                                          "model": "lite-1"})).json()
    _, chat_id, _ = await face_chat(client, ctx, fake_llm, run=False,
                                    settings={"summaries": True, "memory_connection": small["id"]})
    fake_llm.queue(answer(), FIRST)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    used = [r["model"] for r in fake_llm.requests]
    assert len(used) == 2 and used == ["lite-1", "lite-1"]  # summary and faces: the connection's own model
    memory_module().WAKE_DELAY = 0.01
    r = await client.put("/api/plugins/memory/settings", json={"memory_model_name": "lite-2"})
    assert r.status_code == 200
    ctx.chats.set_content(ctx.chats.active_path(chat_id)[1]["id"], "Борин наливает кружку и молчит, правда.")
    fake_llm.queue(answer())
    await client.post("/api/plugins/memory/actions/resummarize",
                      json={"chat_id": chat_id, "chapter_id": ctx.db.fetchone("SELECT id FROM p_memory_chapters")["id"]})
    await idle(ctx, chat_id)
    assert fake_llm.requests[-1]["model"] == "lite-2"
    bad = await client.put("/api/plugins/memory/settings", json={"summary_model_name": "big"})
    assert bad.status_code == 400  # the per-task model settings are gone
