"""Memory++ M4 through the HTTP API on the fake model: «Развитие персонажа» (made by the small model after a chapter's
summary, the request's contents, placed right after the card, per branch, self-compression, lock / undo / hand edit /
versions, «Не наслаивать» = no layer and no request, the user's persona, transfer to another chat, cancelled by a reply
and redone) and «Сейчас:» lines for lorebook bubbles (appended only when the bubble is sent, the lorebook row never
changes, «Записать в лорбук насовсем» after a copy of the data, one history step). Off by default; migration 0004;
still exactly one meaning request per reply."""

from __future__ import annotations

import asyncio
import hashlib
import json

from tests.integration.test_memory_chapters import chapter_chat, rerun, view
from tests.integration.test_memory_plugin import idle, memory_module, objects, say, send, switch
from tests.integration.test_memory_summaries import STORY, answer, models_on, plugin_module

LAYER = "Анна подружилась с хозяином таверны и теперь ищет караван на севере. Она устала, но полна решимости."
LAYER2 = "Анна дошла до леса, нашла следы каравана и больше не доверяет хозяину таверны."


def layers_answer(*layers: tuple[str, str], lines: tuple[tuple[str, str], ...] = ()) -> str:
    return json.dumps({"layers": [{"who": w, "text": t} for w, t in layers],
                       "now_lines": [{"entry": e, "line": line} for e, line in lines]}, ensure_ascii=False)


def layer_requests(fake_llm) -> list[dict]:
    return [r for r in fake_llm.requests if r["messages"][0]["content"].startswith("You keep the memory of a long")]


def dev_rows(ctx, chat_id) -> list[dict]:
    return [dict(r) for r in ctx.db.fetchall(
        "SELECT target, text, anchor_id, made_by, locked FROM p_memory_texts WHERE chat_id = ? AND kind = 'development' "
        "ORDER BY created_at, rowid", (chat_id,))]


async def layer_chat(client, ctx, fake_llm, *, settings=None, persona=False, lines=False):
    """20 messages, one chapter closed at the marker (messages 0-11), Ann a member with a card; summaries and
    development on (the user's own layer off unless `persona`); the job done once."""
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    ann = setup["ann"]
    assert (await client.put(f"/api/characters/{ann['id']}", json={"name": "Ann", "description": "Ann is a kind innkeeper."})
            ).status_code == 200
    assert (await client.post(f"/api/chats/{chat_id}/members", json={"character_id": ann["id"]})).status_code == 201
    if not persona:
        plugin_module("store").set_member(ctx.db, chat_id, "user", no_layer=True)
    await models_on(client, summaries=True, development=not lines, now_lines=lines, **(settings or {}))
    return setup, chat_id, ids


async def run_job(client, ctx, chat_id) -> None:
    await switch(client, chat_id)
    await idle(ctx, chat_id)


async def layers_view(client, chat_id) -> dict:
    return await view(client, chat_id, "layers")


def member(result, target) -> dict:
    return next(m for m in result["members"] if m["target"] == target)


def prompt_entries(saw) -> list[tuple[str, str]]:
    return [(e["source"], e["text"]) for e in saw["entries"]]


async def reply(client, ctx, chat_id, text) -> dict:
    memory_module().WAKE_DELAY = 60  # the job must not run beside the reply under test
    events = await send(client, ctx, chat_id, text)
    assert events[-1]["type"] == "done", events[-1]
    return (await client.get(f"/api/messages/{events[-1]['message']['id']}/prompt")).json()


# -- off by default --------------------------------------------------------------------------------


async def test_development_and_now_lines_are_off_by_default_and_migration_0004(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    fake_llm.queue(answer())
    await run_job(client, ctx, chat_id)
    assert layer_requests(fake_llm) == [] and dev_rows(ctx, chat_id) == []
    result = await layers_view(client, chat_id)
    assert result["development_on"] is False and result["now_on"] is False and result["now_lines"] == []
    assert {"p_memory_layer_runs", "p_memory_layer_runs_chat", "p_memory_texts_anchor"} <= set(objects(ctx))
    assert ctx.db.fetchone("SELECT schema_version AS v FROM plugins WHERE id = 'memory'")["v"] >= 4
    settings = (await client.get("/api/plugins")).json()
    memory = next(p for p in settings["plugins"] if p["id"] == "memory")
    assert memory["values"]["development"] is False and memory["values"]["now_lines"] is False
    assert memory["values"]["memory_percent"] == 15 and memory["values"]["dev_tokens"] == 450


# -- made by the job ---------------------------------------------------------------------------------


async def test_the_layer_is_made_after_the_summary_with_card_old_layer_and_rules(client, ctx, fake_llm):
    setup, chat_id, ids = await layer_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER)))
    await run_job(client, ctx, chat_id)
    sent = layer_requests(fake_llm)
    assert len(sent) == 1
    order = [r["messages"][0]["content"][:30] for r in fake_llm.requests]
    assert order.index(sent[0]["messages"][0]["content"][:30]) > 0  # the summary came first
    system, user = sent[0]["messages"][0]["content"], sent[0]["messages"][-1]["content"]
    assert "how this character is NOW" in system and "at most 450 tokens" in system
    assert "the layer is the truth now" in system and "(here: Russian)" in system and "Present tense" in system
    assert "C1: Ann" in user and "Card: Ann is a kind innkeeper." in user and "Old layer: (none yet)" in user
    assert STORY in user  # the chapter's summary is the scene
    assert "User" not in user.split("The scene")[0]  # the persona has «Не наслаивать» here
    rows = dev_rows(ctx, chat_id)
    assert [(r["target"], r["text"], r["made_by"], r["locked"]) for r in rows] == [(ann, LAYER, "auto", 0)]
    assert rows[0]["anchor_id"] == ids[11]  # anchored at the chapter's last message
    shown = member(await layers_view(client, chat_id), ann)
    assert shown["text"] == LAYER and shown["by"] == "auto" and shown["locked"] is False
    assert ctx.db.fetchone("SELECT state FROM p_memory_layer_runs")["state"] == "done"
    # the next chapter: the old layer goes into the request and the new one replaces it
    for i in range(20, 32):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", "*** Глава" if i == 24 else f"дальше {i}",
            **({} if i % 2 == 0 else {"character_id": ann, "name": "Ann"}))
    fake_llm.queue(answer("Лес", STORY.replace("таверну", "лес")), layers_answer(("C1", LAYER2)))
    await rerun(client, ctx, chat_id)
    assert f"Old layer: {LAYER}" in layer_requests(fake_llm)[-1]["messages"][-1]["content"]
    assert member(await layers_view(client, chat_id), ann)["text"] == LAYER2


async def test_the_layer_goes_right_after_the_card_and_wins_as_now_even_with_no_window(client, ctx, fake_llm):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER)))
    await run_job(client, ctx, chat_id)
    saw = await reply(client, ctx, chat_id, "привет")
    sources = [s for s, _ in prompt_entries(saw)]
    card = sources.index(f"character:{ann}:card")
    assert sources[card + 1] == f"plugin:memory:dev:{ann}"
    text = dict(prompt_entries(saw))[f"plugin:memory:dev:{ann}"]
    assert "где расходится с карточкой, верно это" in text and LAYER in text
    assert any(e["label"] == "Развитие: Ann" for e in saw["entries"])


async def test_another_branch_keeps_its_own_development(client, ctx, fake_llm):
    setup, chat_id, ids = await layer_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER)))
    await run_job(client, ctx, chat_id)
    leaf = ctx.chats.get_chat(chat_id)["leaf_id"]
    ctx.chats.set_leaf(chat_id, ids[5])  # a fork inside the chapter: its layer is not on this branch
    say(ctx, chat_id, "user", "другая ветка")
    assert member(await layers_view(client, chat_id), ann)["text"] == ""
    saw = await reply(client, ctx, chat_id, "ещё")
    assert LAYER not in json.dumps(saw, ensure_ascii=False)
    # a hand edit here is this branch's own; the first branch keeps the model's
    await view(client, chat_id, "layer_edit", target=ann, text="Своя ветка: Анна осталась дома.")
    ctx.chats.set_leaf(chat_id, leaf)
    assert member(await layers_view(client, chat_id), ann)["text"] == LAYER


async def test_a_too_long_layer_compresses_itself_with_one_more_request(client, ctx, fake_llm):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm, settings={"dev_tokens": 150})
    ann = setup["ann"]["id"]
    long = " ".join([LAYER] * 12)  # about 300 tokens, more than 1.3 x 150
    short = "Анна сдружилась с хозяином таверны и ищет караван на севере."
    fake_llm.queue(answer(), layers_answer(("Ann", long)), json.dumps({"text": short}, ensure_ascii=False))
    await run_job(client, ctx, chat_id)
    compress = [r for r in fake_llm.requests if r["messages"][0]["content"].startswith("Shorten the text")]
    assert len(compress) == 1 and "at most 150 tokens" in compress[0]["messages"][0]["content"]
    assert long in compress[0]["messages"][-1]["content"]
    assert member(await layers_view(client, chat_id), ann)["text"] == short


async def test_hand_edit_locks_versions_restore_and_undo_and_a_lock_means_no_request(client, ctx, fake_llm):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER)))
    await run_job(client, ctx, chat_id)
    edited = await view(client, chat_id, "layer_edit", target=ann, text="Анна ранена в плечо.")
    mine = member(edited, ann)
    assert mine["text"] == "Анна ранена в плечо." and mine["locked"] is True and mine["by"] == "hand"
    assert edited["undo"] == "Изменить развитие"
    versions = (await view(client, chat_id, "layer_versions", target=ann))["versions"]
    assert [v["by"] for v in versions] == ["hand", "auto"]
    restored = await view(client, chat_id, "layer_restore", target=ann, text_id=versions[1]["id"])
    assert member(restored, ann)["text"] == LAYER and member(restored, ann)["locked"] is True
    await view(client, chat_id, "undo")  # the restore
    assert member(await layers_view(client, chat_id), ann)["text"] == "Анна ранена в плечо."
    await view(client, chat_id, "undo")  # the hand edit: the model's version again
    assert member(await layers_view(client, chat_id), ann)["text"] == LAYER
    # a lock: the next chapter makes no request about Ann (and the persona is off here): no request at all
    await view(client, chat_id, "layer_lock", target=ann, locked=True)
    for i in range(20, 32):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", "*** Глава" if i == 24 else f"дальше {i}")
    fake_llm.queue(answer("Лес"))
    await rerun(client, ctx, chat_id)
    assert len(layer_requests(fake_llm)) == 1
    assert member(await layers_view(client, chat_id), ann)["text"] == LAYER
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_layer_runs WHERE state = 'done'")["n"] == 2


async def test_no_layer_member_gets_no_layer_and_no_request(client, ctx, fake_llm):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    plugin_module("store").set_member(ctx.db, chat_id, ann, no_layer=True)
    fake_llm.queue(answer())
    await run_job(client, ctx, chat_id)
    assert layer_requests(fake_llm) == [] and dev_rows(ctx, chat_id) == []
    # a layer written by hand is still not sent while «Не наслаивать» is on
    await view(client, chat_id, "layer_edit", target=ann, text="Анна ранена.")
    saw = await reply(client, ctx, chat_id, "привет")
    assert f"plugin:memory:dev:{ann}" not in [s for s, _ in prompt_entries(saw)]
    await view(client, chat_id, "member_set", character_id=ann, no_layer=False)
    saw = await reply(client, ctx, chat_id, "ещё")
    assert f"plugin:memory:dev:{ann}" in [s for s, _ in prompt_entries(saw)]


async def test_the_users_persona_gets_a_layer_too(client, ctx, fake_llm):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm, persona=True)
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER), ("User", "Ты дал Анне слово найти караван.")))
    await run_job(client, ctx, chat_id)
    user = layer_requests(fake_llm)[0]["messages"][-1]["content"]
    assert "C1: User" in user and "C2: Ann" in user
    me = member(await layers_view(client, chat_id), "user")
    assert me["is_me"] is True and me["text"] == "Ты дал Анне слово найти караван."
    saw = await reply(client, ctx, chat_id, "привет")  # no «me» member: it goes in the memory spot
    assert dict(prompt_entries(saw)).get("plugin:memory:dev:user", "").endswith("Ты дал Анне слово найти караван.")
    # with a «me» member it follows the persona's card
    me_card = (await client.post("/api/characters", json={"name": "Игорь", "description": "Игорь — путник."})).json()
    assert (await client.post(f"/api/chats/{chat_id}/members",
                              json={"character_id": me_card["id"], "is_me": True})).status_code == 201
    await view(client, chat_id, "layer_edit", target=me_card["id"], text="Игорь обещал Анне помочь.")
    sources = [s for s, _ in prompt_entries(await reply(client, ctx, chat_id, "ещё"))]
    assert sources[sources.index(f"character:{me_card['id']}:card") + 1] == f"plugin:memory:dev:{me_card['id']}"


async def test_transfer_the_layer_to_another_chat(client, ctx, fake_llm):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER)))
    await run_job(client, ctx, chat_id)
    other = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    assert (await client.post(f"/api/chats/{other}/members", json={"character_id": ann})).status_code == 201
    empty = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    assert (await client.post(f"/api/chats/{empty}/members", json={"character_id": ann})).status_code == 201
    say(ctx, other, "user", "новый чат")
    chats = (await view(client, chat_id, "layer_chats", target=ann))["chats"]
    assert {c["chat_id"]: c["has_messages"] for c in chats} == {other: True, empty: False}
    bad = await client.post("/api/plugins/memory/actions/layer_transfer",
                            json={"chat_id": chat_id, "target": ann, "to_chat_id": empty})
    assert bad.status_code == 409  # «В том чате ещё нет сообщений»
    done = await view(client, chat_id, "layer_transfer", target=ann, to_chat_id=other)
    assert done["done"]["to_chat_id"] == other and done["done"]["memory"] is False
    await switch(client, other)
    moved = member(await layers_view(client, other), ann)
    assert moved["text"] == LAYER and moved["by"] == "transfer"


async def test_a_reply_cancels_the_layer_request_and_it_is_made_again(client, ctx, fake_llm, monkeypatch):
    setup, chat_id, _ = await layer_chat(client, ctx, fake_llm)
    module = plugin_module("layers")
    started: list[int] = []
    gate = asyncio.Event()
    real = module.make

    async def slow(llm, scene, people, entries, target):
        started.append(1)
        if len(started) == 1:
            await gate.wait()
        return await real(llm, scene, people, entries, target)

    monkeypatch.setattr(module, "make", slow)
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER)))
    await switch(client, chat_id)
    for _ in range(300):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started == [1]
    ctx.foreground.enter()  # a reply starts: the step is cancelled at once
    await asyncio.sleep(0.1)
    assert dev_rows(ctx, chat_id) == [] and ctx.jobs.status("memory", f"chat:{chat_id}")["paused"] is True
    ctx.foreground.leave()
    await idle(ctx, chat_id)
    assert len(started) == 2 and [r["text"] for r in dev_rows(ctx, chat_id)] == [LAYER]


# -- «Сейчас:» lines --------------------------------------------------------------------------------


def block_hash(ctx, block_id) -> str:
    row = ctx.db.fetchone("SELECT * FROM character_blocks WHERE id = ?", (block_id,))
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def lore_chat(client, ctx, fake_llm):
    setup, chat_id, ids = await layer_chat(client, ctx, fake_llm, lines=True)
    book = (await client.post("/api/lorebooks", json={"name": "Мир"})).json()["id"]
    mill = (await client.post(f"/api/lorebooks/{book}/blocks", json={
        "title": "Мельница", "content": "Старая мельница у реки.", "mode": "search", "keywords": ["мельница"]})).json()
    other = (await client.post(f"/api/lorebooks/{book}/blocks", json={
        "title": "Замок", "content": "Замок на холме.", "mode": "search", "keywords": ["замок"]})).json()
    await client.put("/api/lorebook-uses", json={"scope": "chat", "target": chat_id, "lorebook_ids": [book]})
    ctx.chats.set_content(ids[4], "Мельница у реки сгорела этой ночью.")
    return setup, chat_id, ids, book, mill, other


async def test_now_lines_come_from_the_chapter_and_go_only_with_their_bubble_the_lorebook_unchanged(
        client, ctx, fake_llm):
    _, chat_id, _, book, mill, other = await lore_chat(client, ctx, fake_llm)
    before = {b["id"]: block_hash(ctx, b["id"]) for b in (mill, other)}
    fake_llm.queue(answer(), layers_answer(lines=(("E1", "Сейчас: мельница сгорела, остались угли."),)))
    await run_job(client, ctx, chat_id)
    user = layer_requests(fake_llm)[0]["messages"][-1]["content"]
    assert "E1: Мельница" in user and "Замок" not in user  # only the bubble the chapter mentions
    assert "C1: Ann" not in user  # development is off here
    shown = (await layers_view(client, chat_id))["now_lines"]
    assert [(x["title"], x["book"], x["line"]) for x in shown] == [("Мельница", "Мир", "мельница сгорела, остались угли.")]
    # a reply that does not choose the bubble: no line
    saw = await reply(client, ctx, chat_id, "погода хорошая")
    assert "Сейчас:" not in json.dumps(saw, ensure_ascii=False)
    # a reply that chooses it: the line goes after its text, in this reply only
    saw = await reply(client, ctx, chat_id, "что с мельница")
    text = dict(prompt_entries(saw))[f"block:{mill['id']}"]
    assert text.endswith("Старая мельница у реки.\nСейчас: мельница сгорела, остались угли.")
    assert {b: block_hash(ctx, b) for b in before} == before  # the lorebook rows never changed
    # another chat with the same book has no line
    chat2 = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    await client.put("/api/lorebook-uses", json={"scope": "chat", "target": chat2, "lorebook_ids": [book]})
    say(ctx, chat2, "user", "мельница")
    await switch(client, chat2)
    assert (await layers_view(client, chat2))["now_lines"] == []


async def test_now_line_edit_delete_undo_and_write_forever_after_a_copy(client, ctx, fake_llm):
    _, chat_id, _, book, mill, _ = await lore_chat(client, ctx, fake_llm)
    fake_llm.queue(answer(), layers_answer(lines=(("Мельница", "мельница сгорела."),)))
    await run_job(client, ctx, chat_id)
    edited = await view(client, chat_id, "now_edit", block_id=mill["id"], text="Сейчас: мельницу отстроили.")
    assert [(x["line"], x["locked"]) for x in edited["now_lines"]] == [("мельницу отстроили.", True)]
    assert (await view(client, chat_id, "now_delete", block_id=mill["id"]))["now_lines"] == []
    await view(client, chat_id, "undo")
    assert [x["line"] for x in (await layers_view(client, chat_id))["now_lines"]] == ["мельницу отстроили."]
    copies = len([a for a in ctx.backups.archives() if a.kind == "risky"])
    written = await view(client, chat_id, "now_write", block_id=mill["id"])
    assert written["written"]["content"] == "Старая мельница у реки.\nСейчас: мельницу отстроили."
    assert ctx.blocks.get(mill["id"])["content"] == "Старая мельница у реки.\nСейчас: мельницу отстроили."
    assert len([a for a in ctx.backups.archives() if a.kind == "risky"]) == copies + 1  # a copy first
    assert written["now_lines"] == []  # it left the chat's lines: never appended twice
    saw = await reply(client, ctx, chat_id, "что с мельница")
    assert dict(prompt_entries(saw))[f"block:{mill['id']}"].count("Сейчас:") == 1
    # one normal history step of the lorebook: «Отменить» there puts the bubble back
    assert (await client.post(f"/api/lorebooks/{book}/undo")).status_code == 200
    assert ctx.blocks.get(mill["id"])["content"] == "Старая мельница у реки."


async def test_the_reply_still_makes_exactly_one_meaning_request_with_layers_and_lines(client, ctx, fake_llm):
    _, chat_id, _, _, _, _ = await lore_chat(client, ctx, fake_llm)
    await client.put("/api/plugins/memory/settings", json={"development": True})
    fake_llm.queue(answer(), layers_answer(("Ann", LAYER), lines=(("E1", "мельница сгорела."),)))
    await run_job(client, ctx, chat_id)
    fake_llm.embedding_requests.clear()
    saw = await reply(client, ctx, chat_id, "что с мельница")
    assert len(fake_llm.embedding_requests) == 1
    assert "Сейчас: мельница сгорела." in json.dumps(saw, ensure_ascii=False)
