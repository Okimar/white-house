"""Memory++ M2 through the HTTP API on the fake model: the job closes chapters (script and optional model judge),
branches, staleness, the deleted-message repair, hand edits with undo, a reply pausing the work, chapters found in
the prompt, and nothing happens when memory is off."""

from __future__ import annotations

import asyncio
import json

from tests.integration.test_memory_plugin import boot, idle, memory_module, objects, say, send, state_of, switch

SIZE = 8


async def chapter_chat(client, ctx, fake_llm, count, marks=None, *, settings=None, run=True, window=0, pad=""):
    """A chat of `count` messages (user first, then taking turns); `marks`: index -> text. Memory on, job done."""
    setup = await boot(client, ctx, window=window, settings={"chapter_size": SIZE, **(settings or {})})
    chat_id = setup["chat"]["id"]
    marks = marks or {}
    ids = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        plain = f"Я заказываю эль номер {i}" if role == "user" else f"Ann подаёт эль номер {i}"
        text = marks.get(i) or (unique(i) if pad == "unique" else plain + pad)
        extra = {"character_id": setup["ann"]["id"], "name": "Ann"} if role == "assistant" else {}
        ids.append(say(ctx, chat_id, role, text, **extra)["id"])
    if run:
        await switch(client, chat_id)
        await idle(ctx, chat_id)
    return setup, chat_id, ids


WORDS = ("лошадь пещера факел кузнец корабль мельница ручей монета ключ плащ свеча колокол мост рынок река "
         "якорь лампа пастух шлем гора лес ворота стена башня звезда мёд хлеб сыр перо бочка посох сеть").split()


def unique(i: int) -> str:
    """A message with words no other message has (the fake vectors and the stems then tell messages apart)."""
    return f"{WORDS[i % len(WORDS)]}{'ааа' * (i // len(WORDS))} и {WORDS[(i * 7 + 3) % len(WORDS)]}ов{i}"


async def rerun(client, ctx, chat_id) -> None:
    """Rows written straight into the repo fire no event: ask for another pass, as the page's button does."""
    await client.post("/api/plugins/memory/actions/vectorize", json={"chat_id": chat_id})
    await idle(ctx, chat_id)


async def view(client, chat_id, action="chapters", **payload) -> dict:
    r = await client.post(f"/api/plugins/memory/actions/{action}", json={"chat_id": chat_id, **payload})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def ends(result) -> list[tuple[str, str]]:
    return [(c["first_id"], c["last_id"]) for c in result["chapters"]]


async def test_the_job_closes_chapters_at_markers_and_leaves_the_short_rest_open(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава вторая. Я иду дальше.",
                                                                     24: "На следующий день я просыпаюсь."})
    result = await view(client, chat_id)
    assert ends(result) == [(ids[0], ids[11]), (ids[12], ids[23])]
    assert [c["number"] for c in result["chapters"]] == [1, 2]
    assert result["chapters"][0]["reason"] and result["chapters"][0]["by"] == "script"
    assert result["open"]["count"] == 6 and result["open"]["first_id"] == ids[24]
    assert [c["state"] for c in result["chapters"]] == ["raw", "raw"]
    assert (await state_of(client, chat_id))["chapters"] == 2
    assert fake_llm.requests == []  # no chat model at all


async def test_a_chat_not_longer_than_n_gets_no_chapter_and_a_long_plain_one_is_cut_at_three_n(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, SIZE, {4: "*** Глава"})
    assert (await view(client, chat_id))["chapters"] == []
    _, chat2, ids = await chapter_chat(client, ctx, fake_llm, 60)
    result = await view(client, chat2)
    assert result["chapters"] and all(c["count"] <= 3 * SIZE for c in result["chapters"])
    assert result["chapters"][0]["reason"].startswith("длинная глава")
    assert ctx.chats.get_message(result["chapters"][0]["last_id"])["role"] == "assistant"  # cut before a user message


async def test_model_judge_off_by_default_makes_no_chat_request_and_on_it_has_the_last_word(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {10: "*** Глава"})
    assert fake_llm.requests == []
    assert ends(await view(client, chat_id)) == [(ids[0], ids[9])]  # the script cut at the marker
    # judge on: the model says the scene ends after message number 14
    await client.put("/api/plugins/memory/access", json={"models": True})
    await client.put("/api/plugins/memory/settings", json={"chapter_size": SIZE, "border_judge": True})
    ctx.db.execute("DELETE FROM p_memory_chapters")
    fake_llm.queue(json.dumps({"finished": True, "ends_at": 14, "reason": "герои ушли"}))
    say(ctx, chat_id, "user", "ещё одно")
    await rerun(client, ctx, chat_id)
    result = await view(client, chat_id)
    assert ends(result)[0] == (ids[0], ids[13])  # message number 14 = index 13
    assert result["chapters"][0]["by"] == "model" and result["chapters"][0]["reason"] == "герои ушли"
    assert "Глава" in json.dumps(fake_llm.requests[0]["messages"], ensure_ascii=False)  # the open part was sent


async def test_model_judge_not_finished_means_no_chapter_and_a_refusal_falls_back_to_the_script(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {10: "*** Глава"}, run=False,
                                         settings={"border_judge": True})
    fake_llm.queue(json.dumps({"finished": False}))
    await client.put("/api/plugins/memory/access", json={"models": True})
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert (await view(client, chat_id))["chapters"] == []  # the model said «ещё идёт»
    await client.put("/api/plugins/memory/access", json={"models": False})
    ctx.plugin_repo.data_delete("memory", chat_id, "judge")
    say(ctx, chat_id, "user", "ещё")
    await rerun(client, ctx, chat_id)
    result = await view(client, chat_id)  # no access to models: the script cuts at the marker
    assert ends(result)[0] == (ids[0], ids[9]) and result["chapters"][0]["by"] == "script"


async def test_a_chapter_of_another_branch_is_silent_and_a_shared_one_is_not(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава"})
    assert ends(await view(client, chat_id)) == [(ids[0], ids[11])]
    # a branch from message 15: chapter 1 (ends at 11) is shared
    ctx.chats.set_leaf(chat_id, ids[15])
    for i in range(3):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"другая ветка {i}")
    assert ends(await view(client, chat_id)) == [(ids[0], ids[11])]
    # a branch from message 5: chapter 1 contains the fork and its last message is not on this branch: silent
    ctx.chats.set_leaf(chat_id, ids[5])
    say(ctx, chat_id, "assistant", "ответвление")
    assert (await view(client, chat_id))["chapters"] == []
    r = await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": ids[29]})
    assert r.status_code == 200
    assert ends(await view(client, chat_id)) == [(ids[0], ids[11])]  # back on the first branch


async def test_edit_swipe_hide_and_delete_inside_a_chapter_mark_it_stale(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава"})
    assert (await view(client, chat_id))["chapters"][0]["state"] == "raw"
    await client.put(f"/api/messages/{ids[4]}", json={"content": "совсем другой текст"})
    assert (await view(client, chat_id))["chapters"][0]["state"] == "stale"
    await client.put(f"/api/messages/{ids[4]}", json={"content": "Я заказываю эль номер 4"})
    assert (await view(client, chat_id))["chapters"][0]["state"] == "raw"  # the same text again
    index = ctx.chats.add_swipe(ids[3], "новый вариант")
    await client.put(f"/api/messages/{ids[3]}/swipe", json={"index": index})
    assert (await view(client, chat_id))["chapters"][0]["state"] == "stale"
    await client.put(f"/api/messages/{ids[3]}/swipe", json={"index": 0})
    await client.put(f"/api/messages/{ids[6]}", json={"exclude_from_prompt": True})
    assert (await view(client, chat_id))["chapters"][0]["state"] == "stale"
    await client.put(f"/api/messages/{ids[6]}", json={"exclude_from_prompt": False})
    await client.delete(f"/api/messages/{ids[7]}")
    now = await view(client, chat_id)
    assert now["chapters"][0]["state"] == "stale"
    accepted = await view(client, chat_id, "accept", chapter_id=now["chapters"][0]["id"])
    assert accepted["chapters"][0]["state"] == "raw"


async def test_deleting_the_first_or_last_message_of_a_chapter_keeps_the_chapter(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава", 24: "*** Ещё"})
    r = await client.delete(f"/api/messages/{ids[11]}")  # the last of chapter 1
    assert r.status_code == 200
    await asyncio.sleep(0.05)
    result = await view(client, chat_id)
    assert result["chapters"][0]["last_id"] == ids[10] and result["chapters"][0]["state"] == "stale"
    await client.delete(f"/api/messages/{ids[12]}")  # the first of chapter 2
    await asyncio.sleep(0.05)
    result = await view(client, chat_id)
    assert result["chapters"][1]["first_id"] == ids[13]
    assert len(result["chapters"]) == 2  # nothing cascaded away


async def test_hand_edits_split_merge_move_remove_restore_and_undo(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава", 24: "*** Ещё"})
    first = await view(client, chat_id)
    assert len(first["chapters"]) == 2 and first["undo"] == ""
    r = await view(client, chat_id, "split", message_id=ids[6])  # «Новая глава отсюда» inside chapter 1
    assert ends(r)[:3] == [(ids[0], ids[5]), (ids[6], ids[11]), (ids[12], ids[23])]
    assert r["chapters"][1]["by"] == "hand" and r["undo"] == "Разрезать главу"
    r = await view(client, chat_id, "split", message_id=ids[28])  # in the part without chapters
    assert ends(r)[-1] == (ids[24], ids[27])
    r = await view(client, chat_id, "merge", chapter_id=r["chapters"][1]["id"])  # 6..11 + 12..23
    assert (ids[6], ids[23]) in ends(r) and len(r["chapters"]) == 3
    last = r["chapters"][-1]
    moved = await view(client, chat_id, "move_border", chapter_id=last["id"], message_id=ids[20])
    assert (ids[6], ids[19]) in ends(moved) and (ids[20], ids[27]) in ends(moved)
    undone = await view(client, chat_id, "undo")
    assert (ids[6], ids[23]) in ends(undone) and (ids[24], ids[27]) in ends(undone)
    target = undone["chapters"][0]["id"]
    removed = await view(client, chat_id, "remove", chapter_id=target)
    assert removed["chapters"][0]["state"] == "deleted" and removed["chapters"][0]["number"] is None
    assert removed["chapters"][1]["number"] == 1
    restored = await view(client, chat_id, "restore", chapter_id=target)
    assert restored["chapters"][0]["state"] == "raw" and restored["chapters"][0]["number"] == 1
    edited = await view(client, chat_id, "edit", chapter_id=target, title="В таверне", whole=True)
    assert edited["chapters"][0]["title"] == "В таверне" and edited["chapters"][0]["whole"] is True
    bad = await client.post("/api/plugins/memory/actions/split", json={"chat_id": chat_id, "message_id": ids[0]})
    assert bad.status_code == 409  # a border already stands there: a plain message, not a crash
    for _ in range(20):  # every step is undone, one by one, down to the start
        r = await client.post("/api/plugins/memory/actions/undo", json={"chat_id": chat_id})
        if r.status_code != 200:
            break
    assert ends(await view(client, chat_id)) == [(ids[0], ids[11]), (ids[12], ids[23])]


async def test_a_reply_pauses_the_chapter_work_and_it_resumes(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава"}, run=False)
    ctx.foreground.enter()  # a reply starts
    await switch(client, chat_id)
    await asyncio.sleep(0.5)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_chapters")["n"] == 0
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["paused"] is True
    ctx.foreground.leave()
    await idle(ctx, chat_id)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_chapters")["n"] == 1


async def test_nothing_is_cut_with_the_chat_switch_off_and_the_table_follows_the_chat(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава"}, run=False)
    await asyncio.sleep(0.1)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_chapters")["n"] == 0
    bad = await client.post("/api/plugins/memory/actions/split", json={"chat_id": chat_id, "message_id": ids[6]})
    assert bad.status_code == 400  # «Память в этом чате выключена»
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert "p_memory_chapters" in objects(ctx)
    assert ctx.db.fetchone("SELECT schema_version AS v FROM plugins WHERE id = 'memory'")["v"] == 3
    await client.delete(f"/api/chats/{chat_id}")
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_chapters")["n"] == 0


async def test_a_found_chapter_brings_its_best_piece_with_neighbours_and_loose_old_messages_are_still_found(
        client, ctx, fake_llm):
    marks = {6: "Королева Изольда ждёт у костра.", 12: "*** Глава"}
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, marks, window=120,
                                         settings={"tail_percent": 20, "tail_min": 4, "memory_tokens": 400},
                                         pad="unique")
    ctx.chats.set_content(ids[18], "Загадочная Тарабарская башня стоит на холме")  # in the open part: no chapter
    await idle(ctx, chat_id)
    memory_module().WAKE_DELAY = 60
    events = await send(client, ctx, chat_id, "Изольда и Тарабарская башня")
    assert events[-1]["type"] == "done"
    prompt = json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)
    assert "Ранее в истории: Глава 1" in prompt and "Королева Изольда" in prompt
    assert "Тарабарская башня" in prompt  # a message in no chapter yet is found by its own piece
    assert unique(5) in prompt and unique(7) in prompt  # a neighbour of the best piece, inside the chapter
    saw = (await client.get(f"/api/messages/{events[-1]['message']['id']}/prompt")).json()
    sources = [e["source"] for e in saw["entries"]]
    chapter_id = ctx.db.fetchone("SELECT id FROM p_memory_chapters ORDER BY first_id")["id"]
    assert f"plugin:memory:chapter:{chapter_id}" in sources
