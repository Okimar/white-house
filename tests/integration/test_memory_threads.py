"""Memory++ M5 through the HTTP API on the fake model: «Слово и нити». Records made by the small model after a closed
chapter (every kind, exact quotes only, quests only with their toggle), statuses changed by later chapters and kept per
branch, what goes to whom (the speaker's vows always and right after their card, other records only when relevant, the
persona's promises to the character they were given to, secrets only to those who know, «Помнит всё»), hand edit with a
lock, status, delete / restore, add by hand and undo, a reply cancelling the request and it being made again, off by
default, migration 0005, still exactly one meaning request per reply. Also the M4-noted gap of M3: the plot's hand edit
must not be anchored at the newest message (it went missing while that message was regenerated)."""

from __future__ import annotations

import asyncio
import json

from tests.integration.test_memory_chapters import chapter_chat, rerun, view
from tests.integration.test_memory_plugin import idle, memory_module, objects, say, switch
from tests.integration.test_memory_summaries import models_on, plugin_module

PROMISE = "Я обещаю найти твой караван до весны."
VOW = "Я никогда не предам друга!"
SECRET = "ключ от погреба спрятан под мельницей"
DEBT = "Ты должен мне три серебряные монеты."
QUEST = "Найди мне меч короля."
CONTENTS = {2: PROMISE + " Клянусь.", 3: VOW + " Это мой закон.", 5: f"По секрету: {SECRET}.", 7: DEBT, 9: QUEST}


def record(kind, who, quote, number, to="", importance=2, knows=()):
    return {"kind": kind, "who": who, "to": to, "quote": quote, "message_no": number, "importance": importance,
            "knows": list(knows)}


ALL = [record("promise", "User", PROMISE, 3, "Ann", 3), record("vow", "Ann", VOW, 4, importance=3),
       record("secret", "Ann", SECRET, 6, "User", knows=("Ann", "User")), record("debt", "User", DEBT, 8, "Ann"),
       record("quest", "Ann", QUEST, 10, "User"),
       record("claim", "Ann", "Я дочь короля", 4)]  # not in any message: an invented quote is dropped


def threads_answer(new=(), changes=()) -> str:
    return json.dumps({"new": list(new), "changes": list(changes)}, ensure_ascii=False)


def thread_requests(fake_llm) -> list[dict]:
    return [r for r in fake_llm.requests if "gave their word about" in r["messages"][0]["content"]]


def rows(ctx, chat_id) -> list[dict]:
    return [dict(r) for r in ctx.db.fetchall(
        "SELECT id, kind, who, to_whom, knows, quote, message_id, importance, made_by, locked, deleted "
        "FROM p_memory_threads WHERE chat_id = ? ORDER BY created_at, rowid", (chat_id,))]


async def thread_chat(client, ctx, fake_llm, *, settings=None, bob=False, answer=None, run=True):
    """20 messages, one chapter closed at the marker (messages №1-№12) with the promise, the vow, the secret, the debt
    and the quest in it; Ann (and Bob) members; «Слово и нити» on; the job done once."""
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    ann = setup["ann"]
    assert (await client.put(f"/api/characters/{ann['id']}", json={"name": "Ann", "description": "Ann is an innkeeper."})
            ).status_code == 200
    assert (await client.post(f"/api/chats/{chat_id}/members", json={"character_id": ann["id"]})).status_code == 201
    if bob:
        assert (await client.post(f"/api/chats/{chat_id}/members",
                                  json={"character_id": setup["bob"]["id"]})).status_code == 201
    for index, text in CONTENTS.items():
        ctx.chats.set_content(ids[index], text)
    await models_on(client, threads=True, **(settings or {}))
    if run:
        fake_llm.queue(threads_answer(ALL) if answer is None else answer)
        await switch(client, chat_id)
        await idle(ctx, chat_id)
    return setup, chat_id, ids


def item(result, quote) -> dict:
    return next(i for g in result["groups"] for i in g["items"] if i["quote"] == quote)


async def reply(client, ctx, chat_id, text, speaker=None) -> dict:
    """One reply (the job must not run beside it); the prompt record of the reply."""
    memory_module().WAKE_DELAY = 60
    body = {"text": text, **({"speaker": speaker} if speaker else {})}
    r = await client.post(f"/api/chats/{chat_id}/send", json=body)
    assert r.status_code == 202, r.text
    run = ctx.runs.get(r.json()["run_id"])
    await asyncio.wait([run.task], timeout=10)
    leaf = ctx.chats.get_chat(chat_id)["leaf_id"]
    return (await client.get(f"/api/messages/{leaf}/prompt")).json()


def entries(saw) -> dict[str, str]:
    return {e["source"]: e["text"] for e in saw["entries"]}


def memory_text(saw) -> str:
    """What the memory plugin added to the prompt (the history itself holds the quoted messages too)."""
    return " | ".join(text for source, text in entries(saw).items() if source.startswith("plugin:memory:"))


# -- off by default --------------------------------------------------------------------------------


async def test_threads_are_off_by_default_and_migration_0005(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert thread_requests(fake_llm) == [] and rows(ctx, chat_id) == []
    assert {"p_memory_threads", "p_memory_thread_states", "p_memory_thread_runs", "p_memory_threads_gone",
            "p_memory_threads_chat", "p_memory_thread_states_chat", "p_memory_thread_runs_chat"} <= set(objects(ctx))
    assert ctx.db.fetchone("SELECT schema_version AS v FROM plugins WHERE id = 'memory'")["v"] >= 5
    plugins = (await client.get("/api/plugins")).json()["plugins"]
    values = next(p for p in plugins if p["id"] == "memory")["values"]
    assert values["threads"] is False and values["quests"] is False
    result = await view(client, chat_id, "threads")
    assert result["on"] is False and result["groups"] == []


# -- made by the job ---------------------------------------------------------------------------------


async def test_the_job_extracts_every_kind_with_exact_quotes_and_no_quests_by_default(client, ctx, fake_llm):
    setup, chat_id, ids = await thread_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    sent = thread_requests(fake_llm)
    assert len(sent) == 1
    system, user = sent[0]["messages"][0]["content"], sent[0]["messages"][-1]["content"]
    assert "EXACT words" in system and "promise:" in system and "vow:" in system and "secret:" in system
    assert "quest:" not in system  # quests have their own toggle, off
    assert "C1: Ann" in user and "C2: User" in user and f"№3 User: {PROMISE}" in user
    assert "№13" not in user  # only the closed chapter
    made = {r["kind"]: r for r in rows(ctx, chat_id)}
    assert set(made) == {"promise", "vow", "secret", "debt"}  # no quest, no invented claim
    assert (made["promise"]["who"], made["promise"]["to_whom"], made["promise"]["message_id"],
            made["promise"]["importance"]) == ("user", ann, ids[2], 3)
    assert (made["vow"]["who"], made["vow"]["to_whom"], made["vow"]["quote"]) == (ann, "", VOW)
    assert json.loads(made["secret"]["knows"]) == [ann, "user"] and made["secret"]["message_id"] == ids[5]
    assert all(r["made_by"] == "auto" and not r["locked"] for r in made.values())
    result = await view(client, chat_id, "threads")
    assert [g["name"] for g in result["groups"]] == ["Ann", "User"]  # the chat's characters first, then «Я»
    shown = item(result, PROMISE)
    assert shown["status"] == "active" and shown["status_label"] == "в силе" and shown["message_no"] == 3
    assert shown["kind_label"] == "обещание" and shown["to_name"] == "Ann"
    assert ctx.db.fetchone("SELECT state FROM p_memory_thread_runs")["state"] == "done"
    # the quotes got vectors in the job (found by meaning with the reply's one request)
    have = {r["item_id"] for r in ctx.db.fetchall("SELECT item_id FROM p_memory_vectors WHERE item_id LIKE 't:%'")}
    assert have == {"t:" + r["id"] for r in made.values()}


async def test_quests_only_with_their_toggle(client, ctx, fake_llm):
    _, chat_id, _ = await thread_chat(client, ctx, fake_llm, settings={"quests": True})
    assert "quest:" in thread_requests(fake_llm)[0]["messages"][0]["content"]
    assert "quest" in {r["kind"] for r in rows(ctx, chat_id)}


async def test_a_later_chapter_changes_statuses_per_branch_and_never_a_locked_record(client, ctx, fake_llm):
    setup, chat_id, ids = await thread_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    vow = next(r for r in rows(ctx, chat_id) if r["kind"] == "vow")
    await view(client, chat_id, "thread_lock", thread_id=vow["id"], locked=True)
    for i in range(20, 32):
        text = {22: "Вот твой караван, я нашёл его.", 24: "*** Глава"}.get(i, f"дальше {i}")
        extra = {} if i % 2 == 0 else {"character_id": ann, "name": "Ann"}
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", text, **extra)
    fake_llm.queue(threads_answer(changes=[{"id": "T2", "status": "kept", "message_no": 23, "note": "караван найден"},
                                           {"id": "T1", "status": "broken", "message_no": 23}]))
    await rerun(client, ctx, chat_id)
    user = thread_requests(fake_llm)[-1]["messages"][-1]["content"]
    assert f"T1: vow of Ann (active): «{VOW}»" in user and f"T2: promise of User to Ann (active): «{PROMISE}»" in user
    result = await view(client, chat_id, "threads")
    promise = item(result, PROMISE)
    assert promise["status"] == "kept" and promise["note"] == "караван найден"
    assert [h["status"] for h in promise["history"]] == ["kept"] and promise["history"][0]["message_no"] == 23
    assert item(result, VOW)["status"] == "active"  # locked: the model's «broken» was not taken
    # another branch forking before the message that kept it: there it is still in force
    ctx.chats.set_leaf(chat_id, ids[15])
    say(ctx, chat_id, "user", "другая ветка")
    assert item(await view(client, chat_id, "threads"), PROMISE)["status"] == "active"


# -- what goes to whom ---------------------------------------------------------------------------------


async def test_vows_always_after_the_card_others_only_when_relevant_and_the_persona_promise_reaches_ann(
        client, ctx, fake_llm):
    setup, chat_id, _ = await thread_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    saw = await reply(client, ctx, chat_id, "погода хорошая")
    sources = [e["source"] for e in saw["entries"]]
    assert sources[sources.index(f"character:{ann}:card") + 1] == f"plugin:memory:vows:{ann}"
    assert VOW in entries(saw)[f"plugin:memory:vows:{ann}"]
    assert f"plugin:memory:threads:{ann}" not in sources  # nothing else is relevant to the weather
    saw = await reply(client, ctx, chat_id, "ну где мой караван")
    text = entries(saw)[f"plugin:memory:threads:{ann}"]
    assert f"обещание · User → Ann (в силе, №3): «{PROMISE}»" in text  # «ты же обещал»
    assert SECRET not in text and DEBT not in text  # not relevant to this reply
    assert any(e["label"] == "Слово и нити: Ann" for e in saw["entries"])


async def test_secrets_only_to_those_who_know_and_remembers_all(client, ctx, fake_llm):
    setup, chat_id, _ = await thread_chat(client, ctx, fake_llm, bob=True)
    ann, bob = setup["ann"]["id"], setup["bob"]["id"]
    saw = await reply(client, ctx, chat_id, "что там с погребом и мельницей", speaker=ann)
    assert SECRET in entries(saw)[f"plugin:memory:threads:{ann}"]
    saw = await reply(client, ctx, chat_id, "что там с погребом и мельницей", speaker=bob)
    text = memory_text(saw)
    assert SECRET not in text and VOW not in text  # Bob does not know it, and Ann's vow is Ann's
    # Ann tells Bob (a hand edit of who knows): now Bob gets it too
    secret = next(r for r in rows(ctx, chat_id) if r["kind"] == "secret")
    await view(client, chat_id, "thread_edit", thread_id=secret["id"], knows=[ann, "user", bob])
    saw = await reply(client, ctx, chat_id, "что там с погребом и мельницей", speaker=bob)
    assert SECRET in entries(saw)[f"plugin:memory:threads:{bob}"]
    # «Помнит всё»: every record that is relevant
    await view(client, chat_id, "thread_edit", thread_id=secret["id"], knows=[ann, "user"])
    await view(client, chat_id, "member_set", character_id=bob, remembers_all=True)
    saw = await reply(client, ctx, chat_id, "что там с погребом и мельницей", speaker=bob)
    assert SECRET in entries(saw)[f"plugin:memory:threads:{bob}"]


async def test_nothing_goes_with_threads_switched_off_and_dropped_records_never(client, ctx, fake_llm):
    setup, chat_id, _ = await thread_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    promise = next(r for r in rows(ctx, chat_id) if r["kind"] == "promise")
    await view(client, chat_id, "thread_status", thread_id=promise["id"], status="dropped")
    saw = await reply(client, ctx, chat_id, "ну где мой караван")
    assert PROMISE not in memory_text(saw)
    await client.put("/api/plugins/memory/settings", json={"threads": False})
    saw = await reply(client, ctx, chat_id, "погода")
    assert f"plugin:memory:vows:{ann}" not in entries(saw)


async def test_the_reply_still_makes_exactly_one_meaning_request(client, ctx, fake_llm):
    _, chat_id, _ = await thread_chat(client, ctx, fake_llm)
    fake_llm.embedding_requests.clear()
    saw = await reply(client, ctx, chat_id, "ну где мой караван")
    assert len(fake_llm.embedding_requests) == 1
    assert PROMISE in memory_text(saw)


# -- by hand -------------------------------------------------------------------------------------------


async def test_hand_edit_status_delete_restore_add_and_undo(client, ctx, fake_llm):
    setup, chat_id, ids = await thread_chat(client, ctx, fake_llm)
    ann = setup["ann"]["id"]
    debt = next(r for r in rows(ctx, chat_id) if r["kind"] == "debt")
    edited = await view(client, chat_id, "thread_edit", thread_id=debt["id"], quote="Ты должен мне пять монет.",
                        importance=3)
    shown = item(edited, "Ты должен мне пять монет.")
    assert shown["locked"] is True and shown["importance"] == 3 and edited["undo"] == "Изменить запись"
    result = await view(client, chat_id, "thread_status", thread_id=debt["id"], status="kept", note="отдал")
    assert item(result, "Ты должен мне пять монет.")["status_label"] == "выполнено"
    path = ctx.chats.active_path(chat_id)
    state = ctx.db.fetchone("SELECT anchor_id FROM p_memory_thread_states")
    assert state["anchor_id"] == path[-2]["id"]  # not the newest message: it may be regenerated
    result = await view(client, chat_id, "thread_delete", thread_id=debt["id"])
    assert item(result, "Ты должен мне пять монет.")["deleted"] is True
    assert "пять монет" not in memory_text(await reply(client, ctx, chat_id, "монеты долг пять"))
    await view(client, chat_id, "thread_restore", thread_id=debt["id"])
    await view(client, chat_id, "undo")  # the restore
    await view(client, chat_id, "undo")  # the delete
    await view(client, chat_id, "undo")  # the status
    result = await view(client, chat_id, "undo")  # the edit
    assert result["chapters"]  # undo answers with the chapters' view; the tab loads its own again
    back = item(await view(client, chat_id, "threads"), DEBT)
    assert back["status"] == "active" and back["locked"] is False and back["deleted"] is False
    added = await view(client, chat_id, "thread_add", kind="claim", who=ann, quote="Я дочь мельника.", number=4)
    made = item(added, "Я дочь мельника.")
    assert made["by"] == "hand" and made["locked"] is True and made["message_no"] == 4 and made["message_id"] == ids[3]
    bad = await client.post("/api/plugins/memory/actions/thread_add",
                            json={"chat_id": chat_id, "kind": "spell", "who": ann, "quote": "абв"})
    assert bad.status_code == 409
    await view(client, chat_id, "undo")
    assert all(i["quote"] != "Я дочь мельника." for g in (await view(client, chat_id, "threads"))["groups"]
               for i in g["items"])


async def test_a_reply_cancels_the_threads_request_and_it_is_made_again(client, ctx, fake_llm, monkeypatch):
    _, chat_id, _ = await thread_chat(client, ctx, fake_llm, run=False)
    module = plugin_module("threads")
    started: list[int] = []
    gate = asyncio.Event()
    real = module.ask

    async def slow(llm, request):
        started.append(1)
        if len(started) == 1:
            await gate.wait()
        return await real(llm, request)

    monkeypatch.setattr(module, "ask", slow)
    fake_llm.queue(threads_answer(ALL))
    await switch(client, chat_id)
    for _ in range(300):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started == [1]
    ctx.foreground.enter()  # a reply starts: the step is cancelled at once
    await asyncio.sleep(0.1)
    assert rows(ctx, chat_id) == [] and ctx.jobs.status("memory", f"chat:{chat_id}")["paused"] is True
    ctx.foreground.leave()
    await idle(ctx, chat_id)
    assert len(started) == 2 and len(rows(ctx, chat_id)) == 4


# -- the plot's hand edit anchor (M4's known gap of M3) ------------------------------------------------


async def test_regression_a_hand_written_plot_stays_while_the_newest_reply_is_regenerated(client, ctx, fake_llm):
    """The plot written by hand in a chat with no plot was anchored at the newest message; a regenerate leaves that
    message out of its own history, so the plot was off the branch exactly during that reply."""
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {}, window=200, pad="unique",
                                       settings={"tail_percent": 20, "tail_min": 4, "memory_tokens": 600})
    plot = "Анна ищет пропавший караван на севере и не верит хозяину таверны."
    await view(client, chat_id, "plot_edit", text=plot)
    path = ctx.chats.active_path(chat_id)
    anchor = ctx.db.fetchone("SELECT anchor_id FROM p_memory_texts WHERE kind = 'plot'")["anchor_id"]
    assert anchor == path[-2]["id"]
    memory_module().WAKE_DELAY = 60
    r = await client.post(f"/api/chats/{chat_id}/regenerate")
    assert r.status_code == 202, r.text
    await asyncio.wait([ctx.runs.get(r.json()["run_id"]).task], timeout=10)
    assert plot in json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)
