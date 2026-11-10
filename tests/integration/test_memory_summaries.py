"""Memory++ M3 through the HTTP API on the fake model: summaries of closed chapters as background job steps (ok,
failure → raw with a mark and a retry, cancelled by a reply and redone), the request's form, «Сюжет до сих пор»
built and versioned per branch, the two-level search (summary vs piece, whole chapter, story order, sticky, budget),
the group filter with «Помнит всё», stale → button / automatic (off by default), the lock, undo, versions, the
archive labels, and the step-1 review regressions (offers ranked, not cut by the plugin's budget before the window is
known; the job's first cut off the event loop)."""

from __future__ import annotations

import asyncio
import json
import sys

from tests.integration.test_memory_chapters import chapter_chat, rerun, unique, view
from tests.integration.test_memory_plugin import boot, idle, memory_module, objects, say, send, switch

SIZE = 8
STORY = ("Анна вошла в таверну, заказала эль и долго говорила с хозяином о пропавшем караване. "
         "Хозяин рассказал, что караван ушёл на север три дня назад.")


def plugin_module(name: str):
    """A module of the plugin as the manager loaded it (its package is `tavern_plugin_memory`)."""
    return sys.modules[f"tavern_plugin_memory.{name}"]


def answer(title: str = "В таверне", summary: str = STORY) -> str:
    return json.dumps({"title": title, "summary": summary}, ensure_ascii=False)


async def models_on(client, **settings) -> None:
    assert (await client.put("/api/plugins/memory/access", json={"models": True})).status_code == 200
    r = await client.put("/api/plugins/memory/settings", json={"chapter_size": SIZE, **settings})
    assert r.status_code == 200, r.text


def chat_requests(fake_llm) -> list[dict]:
    """The summary / plot requests (system prompt of the memory), not the chat's replies."""
    return [r for r in fake_llm.requests if "memory of a long roleplay chat" in json.dumps(r["messages"])
            or "running plot" in json.dumps(r["messages"])]


def summaries(ctx, chat_id) -> dict[str, dict]:
    rows = ctx.db.fetchall("SELECT target, text, made_by, locked FROM p_memory_texts WHERE chat_id = ? "
                           "AND kind = 'summary' AND is_current = 1", (chat_id,))
    return {r["target"]: dict(r) for r in rows}


async def two_chapters(client, ctx, fake_llm, **kw):
    return await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава вторая. Я иду дальше.",
                                                          24: "На следующий день я просыпаюсь."}, **kw)


# -- off by default ----------------------------------------------------------------------------------


async def test_summaries_are_off_by_default_and_make_no_request(client, ctx, fake_llm):
    _, chat_id, _ = await two_chapters(client, ctx, fake_llm)
    result = await view(client, chat_id)
    assert len(result["chapters"]) == 2 and all(c["state"] == "raw" for c in result["chapters"])
    assert fake_llm.requests == [] and summaries(ctx, chat_id) == {}
    assert result["summaries_on"] is False and result["plot"] is None
    # migration 0003: its tables and the new chapter columns exist, the plugin's schema is at version 3
    assert {"p_memory_texts", "p_memory_hits", "p_memory_members", "p_memory_chapters_gone"} <= set(objects(ctx))
    columns = {r["name"] for r in ctx.db.fetchall("PRAGMA table_info(p_memory_chapters)")}
    assert {"error", "failed_at"} <= columns
    assert ctx.db.fetchone("SELECT schema_version AS v FROM plugins WHERE id = 'memory'")["v"] >= 3


# -- the job ---------------------------------------------------------------------------------------


async def test_the_job_summarizes_closed_chapters_with_the_prompt_rules(client, ctx, fake_llm):
    _, chat_id, _ = await two_chapters(client, ctx, fake_llm, run=False)
    await models_on(client, summaries=True)
    fake_llm.queue(answer("В таверне"), answer("Дорога", STORY.replace("таверну", "лес")))
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    result = await view(client, chat_id)
    assert [c["state"] for c in result["chapters"]] == ["summarized", "summarized"]
    assert [c["title"] for c in result["chapters"]] == ["В таверне", "Дорога"]
    assert result["chapters"][0]["summary"] == STORY and result["chapters"][0]["summary_by"] == "auto"
    sent = chat_requests(fake_llm)
    assert len(sent) == 2
    system = sent[0]["messages"][0]["content"]
    assert "language most of the text below is written in (here: Russian)" in system
    assert "Past tense" in system and "No meta talk" in system and "about 1000 tokens" in system
    first_user = sent[0]["messages"][-1]["content"]
    assert "Я заказываю эль номер 0" in first_user and "Ann: Ann подаёт эль номер 1" in first_user
    assert "story just before" not in first_user
    assert STORY in sent[1]["messages"][-1]["content"]  # the previous summary is the next one's context
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM spending WHERE purpose = 'plugin:memory'")["n"] >= 2


async def test_a_summary_in_the_wrong_language_is_asked_again_then_the_chapter_fails_and_is_retried(
        client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    english = answer("Tavern", "Anna came into the tavern, ordered ale and talked with the keeper for a long time.")
    fake_llm.queue(english, english)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    chapter = (await view(client, chat_id))["chapters"][0]
    assert chapter["state"] == "failed" and "Russian" in chapter["error"] and chapter["tries"] == 1
    assert "refused" in chat_requests(fake_llm)[1]["messages"][-1]["content"]  # the second try says why
    assert summaries(ctx, chat_id) == {}
    # not before the backoff
    fake_llm.queue(answer())
    await rerun(client, ctx, chat_id)
    assert (await view(client, chat_id))["chapters"][0]["state"] == "failed"
    # after it: tried again, and this time it works
    ctx.db.execute("UPDATE p_memory_chapters SET failed_at = '2000-01-01T00:00:00+00:00'")
    await rerun(client, ctx, chat_id)
    chapter = (await view(client, chat_id))["chapters"][0]
    assert chapter["state"] == "summarized" and chapter["error"] == "" and chapter["tries"] == 0


async def test_a_failing_service_marks_the_chapter_and_the_job_wakes_itself_after_the_backoff(
        client, ctx, fake_llm, monkeypatch):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"})  # vectors and chapters first
    monkeypatch.setattr(plugin_module("summaries"), "BACKOFF_BASE", 0.5)
    await models_on(client, summaries=True)
    # the fake fails its next requests of any kind: now those are the summary's (JSON mode, then words only)
    fake_llm.fail_next(400, "bad request")
    fake_llm.fail_next(400, "bad request")
    fake_llm.queue(answer())
    await rerun(client, ctx, chat_id)
    assert (await view(client, chat_id))["chapters"][0]["state"] == "failed"
    for _ in range(300):  # the timer (backoff 0.5 s, at least 1 s) runs the job again by itself
        await asyncio.sleep(0.02)
        if summaries(ctx, chat_id):
            break
    await idle(ctx, chat_id)
    assert (await view(client, chat_id))["chapters"][0]["state"] == "summarized"


async def test_no_model_access_leaves_chapters_raw_and_says_why(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await client.put("/api/plugins/memory/settings", json={"chapter_size": SIZE, "summaries": True})
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    result = await view(client, chat_id)
    assert result["chapters"][0]["state"] == "raw" and result["chapters"][0]["tries"] == 0
    assert "не разрешено" in ctx.jobs.status("memory", f"chat:{chat_id}")["note"]
    assert chat_requests(fake_llm) == []


async def test_a_reply_cancels_the_summary_request_in_flight_and_it_is_made_again(client, ctx, fake_llm, monkeypatch):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    module = plugin_module("summaries")

    started: list[int] = []
    gate = asyncio.Event()
    real = module.summarize

    async def slow(llm, path, p, previous, target):
        started.append(1)
        if len(started) == 1:
            await gate.wait()  # the first try hangs until it is cancelled
        return await real(llm, path, p, previous, target)

    monkeypatch.setattr(module, "summarize", slow)
    fake_llm.queue(answer())
    await switch(client, chat_id)
    for _ in range(300):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started == [1]
    ctx.foreground.enter()  # a reply starts: the step is cancelled at once
    await asyncio.sleep(0.1)
    assert summaries(ctx, chat_id) == {}
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["paused"] is True
    ctx.foreground.leave()
    await idle(ctx, chat_id)
    assert len(started) == 2 and len(summaries(ctx, chat_id)) == 1


async def test_ask_each_time_asks_once_per_job_and_waits_for_a_page(client, ctx, fake_llm, monkeypatch):
    _, chat_id, _ = await two_chapters(client, ctx, fake_llm, run=False)
    await models_on(client, summaries=True)
    await client.put("/api/plugins/memory/access", json={"models": True, "ask": True})
    asked: list[str] = []

    async def ask(plugin_id, name, chat):
        asked.append(plugin_id)
        return True

    monkeypatch.setattr(ctx.plugins, "ask", ask)
    from tavern.plugins import manager

    monkeypatch.setattr(manager, "WAIT_POLL", 0.02)
    listeners = {"n": 0}
    monkeypatch.setattr(type(ctx.notices), "listeners", property(lambda self: listeners["n"]))
    fake_llm.queue(answer(), answer("Дорога"))
    await switch(client, chat_id)
    await asyncio.sleep(0.3)
    assert asked == [] and "ждёт разрешения" in ctx.jobs.status("memory", f"chat:{chat_id}")["note"]
    listeners["n"] = 1  # a page opens
    await idle(ctx, chat_id)
    assert asked == ["memory"]  # two summaries, one question
    assert len(summaries(ctx, chat_id)) == 2


# -- stale, lock, versions, undo ------------------------------------------------------------------


async def test_an_edit_makes_a_summary_stale_redone_by_the_button_and_automatically_only_when_asked(
        client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    fake_llm.queue(answer())
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    await client.put(f"/api/messages/{ids[4]}", json={"content": "совсем другой текст"})
    await idle(ctx, chat_id)
    chapter = (await view(client, chat_id))["chapters"][0]
    assert chapter["state"] == "stale" and len(chat_requests(fake_llm)) == 1  # automatic is off by default
    fake_llm.queue(answer(summary=STORY + " Потом всё изменилось."))
    await view(client, chat_id, "resummarize", chapter_id=chapter["id"])
    await idle(ctx, chat_id)
    chapter = (await view(client, chat_id))["chapters"][0]
    assert chapter["state"] == "summarized" and chapter["summary"].endswith("Потом всё изменилось.")
    # automatic re-summarizing on
    await client.put("/api/plugins/memory/settings", json={"chapter_size": SIZE, "summaries": True,
                                                           "auto_resummarize": True})
    fake_llm.queue(answer(summary=STORY + " Третий раз."))
    await client.put(f"/api/messages/{ids[5]}", json={"content": "и это тоже другое"})
    await idle(ctx, chat_id)
    assert (await view(client, chat_id))["chapters"][0]["summary"].endswith("Третий раз.")


async def test_a_hand_edit_locks_the_summary_versions_and_undo(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True, auto_resummarize=True)
    fake_llm.queue(answer())
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    chapter_id = (await view(client, chat_id))["chapters"][0]["id"]
    edited = await view(client, chat_id, "summary_edit", chapter_id=chapter_id, text="Анна ушла из таверны ночью.")
    chapter = edited["chapters"][0]
    assert chapter["locked"] is True and chapter["summary_by"] == "hand" and edited["undo"] == "Изменить пересказ"
    # a lock is never replaced: an edit makes it stale, the automatic redo and the button leave it
    await client.put(f"/api/messages/{ids[4]}", json={"content": "другое"})
    await idle(ctx, chat_id)
    assert (await view(client, chat_id))["chapters"][0]["summary"] == "Анна ушла из таверны ночью."
    bad = await client.post("/api/plugins/memory/actions/resummarize", json={"chat_id": chat_id, "chapter_id": chapter_id})
    assert bad.status_code == 409
    assert len(chat_requests(fake_llm)) == 1
    versions = (await view(client, chat_id, "summary_versions", chapter_id=chapter_id))["versions"]
    assert [v["by"] for v in versions] == ["hand", "auto"] and versions[0]["current"] is True
    restored = await view(client, chat_id, "summary_restore", text_id=versions[1]["id"])
    assert restored["chapters"][0]["summary"] == STORY and restored["chapters"][0]["locked"] is False
    undone = await view(client, chat_id, "undo")  # back to the hand version
    assert undone["chapters"][0]["summary"] == "Анна ушла из таверны ночью."
    undone = await view(client, chat_id, "undo")  # the hand edit itself is undone: the model's version again
    assert undone["chapters"][0]["summary"] == STORY


async def test_a_deleted_and_restored_chapter_keeps_its_summary(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    fake_llm.queue(answer())
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    chapter_id = (await view(client, chat_id))["chapters"][0]["id"]
    await view(client, chat_id, "remove", chapter_id=chapter_id)
    restored = await view(client, chat_id, "restore", chapter_id=chapter_id)
    await idle(ctx, chat_id)
    chapter = (await view(client, chat_id))["chapters"][0]
    assert chapter["state"] == "summarized" and chapter["summary"] == STORY
    assert len(chat_requests(fake_llm)) == 1 and restored


# -- the plot ----------------------------------------------------------------------------------------


async def test_the_plot_is_built_from_summaries_grows_with_them_and_is_versioned_per_branch(client, ctx, fake_llm):
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True, plot=True)
    plot1 = "Анна ищет пропавший караван. Хозяин таверны сказал, что караван ушёл на север три дня назад."
    fake_llm.queue(answer(), json.dumps({"plot": plot1}, ensure_ascii=False))
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    result = await view(client, chat_id)
    assert result["plot"]["text"] == plot1 and result["plot"]["by"] == "auto"
    assert "300 to 500 tokens" in chat_requests(fake_llm)[-1]["messages"][0]["content"]
    # a second chapter closes on this branch: the plot grows from the old one plus the new summary
    for i in range(20, 32):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", "*** Глава" if i == 24 else f"дальше {i}")
    plot2 = plot1 + " Потом Анна ушла в лес и нашла следы."
    fake_llm.queue(answer("Лес", STORY.replace("таверну", "лес")), json.dumps({"plot": plot2}, ensure_ascii=False))
    await rerun(client, ctx, chat_id)
    assert (await view(client, chat_id))["plot"]["text"] == plot2
    assert plot1 in chat_requests(fake_llm)[-1]["messages"][-1]["content"]  # incremental: the old plot is sent
    # another branch forking inside the second chapter: there the plot is the first version
    ctx.chats.set_leaf(chat_id, ids[15])
    say(ctx, chat_id, "user", "другая ветка")
    result = await view(client, chat_id)
    assert result["plot"]["text"] == plot1
    versions = (await view(client, chat_id, "plot_versions"))["versions"]
    assert [v["text"] for v in versions] == [plot1]
    # a hand edit on this branch is locked and undone by «↶»
    await view(client, chat_id, "plot_edit", text="Своя версия сюжета ветки, где Анна осталась в таверне.")
    assert (await view(client, chat_id))["plot"]["locked"] is True
    assert (await view(client, chat_id, "undo"))["plot"]["text"] == plot1


async def test_plot_off_by_default_and_the_rebuild_button(client, ctx, fake_llm):
    _, chat_id, _ = await chapter_chat(client, ctx, fake_llm, 20, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    fake_llm.queue(answer())
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert (await view(client, chat_id))["plot"] is None and len(chat_requests(fake_llm)) == 1
    fake_llm.queue(json.dumps({"plot": "Анна ищет караван, который ушёл на север три дня назад, и не сдаётся."},
                              ensure_ascii=False))
    await view(client, chat_id, "plot_rebuild")
    await idle(ctx, chat_id)
    assert (await view(client, chat_id))["plot"]["text"].startswith("Анна ищет караван")


# -- the prompt --------------------------------------------------------------------------------------


async def summarized_chat(client, ctx, fake_llm, *, window=200, settings=None, summary=STORY):
    """30 messages in two closed chapters with summaries; the window keeps only the newest few."""
    _, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава", 24: "*** Ещё"}, run=False,
                                         window=window, pad="unique",
                                         settings=settings)
    await models_on(client, summaries=True, **{"tail_percent": 20, "tail_min": 4, "memory_tokens": 600,
                                               **(settings or {})})
    fake_llm.queue(answer("Таверна", summary), answer("Лес", "Анна шла через лес до самой ночи и встретила там "
                                                            "старого кузнеца Тимофея у костра."))
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    memory_module().WAKE_DELAY = 60  # the job must not run beside the reply under test
    return chat_id, ids


def prompt_text(fake_llm) -> str:
    return json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)


async def test_a_found_chapter_comes_as_its_summary_in_story_order_with_labels(client, ctx, fake_llm):
    chat_id, ids = await summarized_chat(client, ctx, fake_llm)
    events = await send(client, ctx, chat_id, "что с караваном и кузнецом Тимофеем")
    assert events[-1]["type"] == "done"
    text = prompt_text(fake_llm)
    assert "[Ранее в истории: Глава 1. Таверна]" in text and "караван ушёл на север" in text
    assert "[Ранее в истории: Глава 2. Лес]" in text
    assert text.index("Глава 1. Таверна") < text.index("Глава 2. Лес")  # story order
    saw = (await client.get(f"/api/messages/{events[-1]['message']['id']}/prompt")).json()
    labels = [e["label"] for e in saw["entries"] if e["kind"] == "memory"]
    assert "Ранее в истории: Глава 1. Таверна (пересказ)" in labels
    assert any(e["source"].startswith("plugin:memory:summary:") for e in saw["entries"])
    assert saw["meta"]["dropped"]["memory"] and saw["meta"]["dropped"]["messages"] > 0


async def test_nothing_from_memory_when_the_whole_history_fits(client, ctx, fake_llm):
    chat_id, _ = await summarized_chat(client, ctx, fake_llm, window=100_000)
    await send(client, ctx, chat_id, "что с караваном")
    assert "Ранее в истории" not in prompt_text(fake_llm) and "Сюжет до сих пор" not in prompt_text(fake_llm)


async def test_a_piece_beats_the_summary_only_by_the_margin_and_whole_sends_the_full_text(client, ctx, fake_llm):
    chat_id, ids = await summarized_chat(client, ctx, fake_llm, summary=STORY)
    # a word only one message of chapter 1 has: the piece wins over the summary that does not have it
    await send(client, ctx, chat_id, unique(5))
    text = prompt_text(fake_llm)
    assert unique(5) in text and "караван ушёл на север" not in text
    # «Вся глава»: the full text of chapter 1 when it is found
    chapter_id = (await view(client, chat_id))["chapters"][0]["id"]
    await view(client, chat_id, "edit", chapter_id=chapter_id, whole=True)
    await send(client, ctx, chat_id, "караван")
    text = prompt_text(fake_llm)
    assert unique(1) in text and unique(9) in text  # every message of the chapter


async def test_sticky_keeps_a_found_chapter_for_n_replies(client, ctx, fake_llm):
    """The query is the last three messages: «караван» leaves it after two replies; then the sticky rule holds the
    chapter for `sticky` more replies, and after that it goes."""
    chat_id, _ = await summarized_chat(client, ctx, fake_llm, settings={"sticky": 2})
    seen = []
    for text in ("караван", "погода", "погода", "погода", "погода"):
        await send(client, ctx, chat_id, text)
        seen.append("Глава 1. Таверна" in prompt_text(fake_llm))
    assert seen == [True, True, True, True, False]
    hits = ctx.db.fetchall("SELECT what FROM p_memory_hits WHERE chat_id = ?", (chat_id,))
    assert "summary" in [h["what"] for h in hits]


async def test_the_memory_budget_the_plot_goes_first_and_a_summary_too_long_gives_way_to_pieces(
        client, ctx, fake_llm):
    plot = "Анна ищет караван; в лесу она встретила кузнеца Тимофея. Это сюжет до сих пор, кратко и по делу."
    long_story = " ".join([STORY] * 6)  # about 250 tokens: more than the budget left after the plot
    chat_id, _ = await summarized_chat(client, ctx, fake_llm, summary=long_story,
                                       settings={"memory_tokens": 150, "plot": True})
    ctx.db.execute("DELETE FROM p_memory_texts WHERE kind = 'plot'")
    chapter = ctx.db.fetchone("SELECT last_id FROM p_memory_chapters ORDER BY created_at DESC LIMIT 1")
    plugin_module("store").put_plot(ctx.db, chat_id, plot, chapter["last_id"], "[]", by="hand", locked=True)
    events = await send(client, ctx, chat_id, "караван на север")
    text = prompt_text(fake_llm)
    assert "[Сюжет до сих пор]" in text and "караван ушёл на север" not in text  # the summary did not fit
    saw = (await client.get(f"/api/messages/{events[-1]['message']['id']}/prompt")).json()
    assert saw["meta"]["dropped"]["memory"][0] == "Сюжет до сих пор"
    labels = saw["meta"]["dropped"]["memory"]
    assert "Ранее в истории: Глава 1. Таверна (отрывки)" in labels  # its pieces, the alternative, did
    assert sum(len(e["text"]) // 4 for e in saw["entries"] if e["kind"] == "memory") <= 150
    plot_entry = next(i for i, e in enumerate(saw["entries"]) if e["source"] == "plugin:memory:plot")
    first_message = next(i for i, e in enumerate(saw["entries"]) if e["source"].startswith("message:"))
    assert plot_entry < first_message


async def test_the_reply_still_makes_exactly_one_meaning_request(client, ctx, fake_llm):
    chat_id, _ = await summarized_chat(client, ctx, fake_llm)
    fake_llm.embedding_requests.clear()
    await send(client, ctx, chat_id, "караван")
    assert len(fake_llm.embedding_requests) == 1


async def test_group_chat_a_member_gets_only_chapters_they_were_in_unless_they_remember_all(client, ctx, fake_llm):
    setup = await boot(client, ctx, window=200, settings={"chapter_size": SIZE, "tail_percent": 20, "tail_min": 4,
                                                          "memory_tokens": 600})
    chat_id = setup["chat"]["id"]
    ann, bob = setup["ann"], setup["bob"]
    for character in (ann, bob):
        await client.post(f"/api/chats/{chat_id}/members", json={"character_id": character["id"]})
    for i in range(30):
        role = "user" if i % 2 == 0 else "assistant"
        who = ann if i < 12 else bob  # chapter 1: only Ann answers; later only Bob
        extra = {"character_id": who["id"], "name": who["name"]} if role == "assistant" else {}
        text = "*** Глава" if i in (12, 24) else unique(i)
        say(ctx, chat_id, role, text, **extra)
    await models_on(client, summaries=True, tail_percent=20, tail_min=4, memory_tokens=600)
    fake_llm.queue(answer("Таверна"), answer("Лес", "Боб шёл через лес до самой ночи и встретил кузнеца у костра."))
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    memory_module().WAKE_DELAY = 60
    members = (await view(client, chat_id))["members"]
    assert {m["name"] for m in members} >= {"Ann", "Bob"}

    async def bob_reply() -> str:
        r = await client.post(f"/api/chats/{chat_id}/send", json={"text": "караван", "speaker": bob["id"]})
        assert r.status_code == 202, r.text
        await asyncio.wait([ctx.runs.get(r.json()["run_id"]).task], timeout=10)
        return prompt_text(fake_llm)

    assert "Глава 1. Таверна" not in await bob_reply()  # Bob was not in chapter 1
    r = await client.post("/api/plugins/memory/actions/member_set",
                          json={"chat_id": chat_id, "character_id": bob["id"], "remembers_all": True})
    assert r.status_code == 200 and any(m["remembers_all"] for m in r.json()["result"]["members"])
    assert "Глава 1. Таверна" in await bob_reply()


async def test_review_regression_in_window_hits_do_not_take_the_budget_from_left_out_messages(
        client, ctx, fake_llm):
    """M3 review: the plugin cut its offers to its own budget BEFORE the window was known, by score. Recent messages
    (still in the window) share the query's words, so they took the whole budget and the old message that was really
    left out was never offered. Now every offer goes to the assembler ranked; it skips those still in the window."""
    setup = await boot(client, ctx, window=900, settings={"tail_percent": 10, "tail_min": 2, "memory_tokens": 150})
    chat_id = setup["chat"]["id"]
    for i in range(40):
        role = "user" if i % 2 == 0 else "assistant"
        text = (f"Тристан грустит одиноко {i}. " if i < 20 else f"Тристан ест Мария яблоко груша {i}. ") + "слово " * 30
        say(ctx, chat_id, role, text)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    memory_module().WAKE_DELAY = 60
    events = await send(client, ctx, chat_id, "Мария яблоко груша грустит одиноко")
    saw = (await client.get(f"/api/messages/{events[-1]['message']['id']}/prompt")).json()
    assert saw["meta"]["dropped"]["messages"] > 0
    assert "Ранее в истории (отрывок)" in prompt_text(fake_llm) and "грустит одиноко" in prompt_text(fake_llm)
