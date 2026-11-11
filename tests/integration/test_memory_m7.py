"""Memory++ M7 through the HTTP API: the memory goes into the chat file and comes back (ids remapped, no vectors, old
files still import, a plugin that was off gets its data when switched on), the assistant finds and uses every action
(descriptions, reads skip the journal, a mini-card and a paid run need a yes, undo), «Разобрать весь чат» shows the price
first and goes on after a rate limit, the quiet window after three cancels, and the model reset on a connection change."""

from __future__ import annotations

import json
import time

from tavern.services import assistant_app
from tavern.storage import snapshot
from tests.integration.test_memory_chapters import chapter_chat, rerun, view
from tests.integration.test_memory_plugin import idle, memory_module, say, switch, vectorized
from tests.integration.test_memory_summaries import models_on, plugin_module


async def made_memory(client, ctx, fake_llm):
    """A 30-message chat cut into chapters, then given by hand every kind of memory the file must carry."""
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава вторая.", 24: "На следующий день."})
    store = plugin_module("store")
    db = ctx.db
    chapters = store.chapter_rows(db, chat_id)
    assert len(chapters) >= 2
    ann = setup["ann"]["id"]
    first = chapters[0]
    store.put_summary(db, chat_id, first["id"], "Старый пересказ первой главы про эль.", first["content_hash"],
                      by="hand", locked=True)
    second = store.put_summary(db, chat_id, chapters[1]["id"], "Пересказ второй главы про дорогу.",
                               chapters[1]["content_hash"], by="auto")
    first_text = store.current_summaries(db, chat_id)[first["id"]]["id"]
    store.put_plot(db, chat_id, "Сюжет: эль и дорога.", chapters[1]["last_id"], json.dumps([first_text, second]), by="auto")
    store.put_anchored(db, chat_id, "development", ann, "Ann стала теплее.", ids[20], by="auto")
    store.put_anchored(db, chat_id, "now_line", "block-that-is-not-here", "Таверна закрыта.", ids[20], by="hand",
                       locked=True)
    store.set_member(db, chat_id, ann, remembers_all=True, no_layer=True)
    thread = store.new_thread(chat_id, "promise", ann, "Я принесу эль завтра", ids[3], message_id=ids[3],
                              chapter_id=first["id"], keywords=["эль"])
    store.put_thread(db, thread)
    store.put_state(db, chat_id, thread["id"], ids[10], "kept", [ann], by="auto", note="принесла")
    face = store.new_face("Борин", "Борин — хозяин таверны.", aliases=["трактирщик"], by="auto", origin=chat_id)
    store.put_face(db, face, version_by="auto")
    store.link_face(db, face["id"], chat_id, ids[1])
    store.put_run(db, store.LAYER_RUNS, chat_id, first["id"], "done")
    return setup, chat_id, ids, {"face": face, "thread": thread, "chapters": chapters, "ann": ann}


async def export_text(client, chat_id: str) -> str:
    r = await client.get(f"/api/chats/{chat_id}/export.jsonl")
    assert r.status_code == 200
    return r.text


async def import_text(client, text: str) -> str:
    r = await client.post("/api/chats/import.jsonl", content=text.encode())
    assert r.status_code == 201, r.text
    return r.json()["chat"]["id"]


async def test_the_memory_goes_into_the_file_and_comes_back_with_new_ids(client, ctx, fake_llm):
    _, chat_id, ids, made = await made_memory(client, ctx, fake_llm)
    text = await export_text(client, chat_id)
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    header = next(r["value"] for r in records if r["type"] == "header")
    assert header["version"] == 2
    record = next(r["value"] for r in records if r["type"] == "plugin")
    assert record["plugin"] == "memory" and record["data"]["version"] == 1
    # never vectors, never pieces
    assert "vector" not in text.lower() and "p_memory" not in text
    assert not any(row["id"] in text for row in ctx.db.fetchall("SELECT id FROM p_memory_pieces"))
    data = record["data"]
    assert {len(data["chapters"]) > 1, len(data["faces"]) == 1, len(data["threads"]) == 1} == {True}

    copy_id = await import_text(client, text)
    assert copy_id != chat_id
    store = plugin_module("store")
    db = ctx.db
    new_path = {m["id"] for m in ctx.chats.all_messages(copy_id)}
    old_path = {m["id"] for m in ctx.chats.all_messages(chat_id)}
    assert new_path.isdisjoint(old_path)
    chapters = store.chapter_rows(db, copy_id)
    assert len(chapters) == len(made["chapters"])
    assert all(c["first_id"] in new_path and c["last_id"] in new_path for c in chapters)
    assert {c["id"] for c in chapters}.isdisjoint({c["id"] for c in made["chapters"]})
    # the summary with its lock, and the chapter is not stale (its hash was recomputed for the new ids)
    sums = store.current_summaries(db, copy_id)
    by_title = {s["text"]: s for s in sums.values()}
    assert by_title["Старый пересказ первой главы про эль."]["locked"] is True
    assert (await view(client, copy_id))["chapters"][0]["state"] == "summarized"
    # the plot remembers the NEW summary ids
    plot = store.plot_rows(db, copy_id)[0]
    assert plot["text"] == "Сюжет: эль и дорога." and plot["anchor_id"] in new_path
    assert set(json.loads(plot["made_from"])) == {s["id"] for s in sums.values()}
    # development, the «Сейчас:» line, the member flags
    dev = store.anchored_rows(db, copy_id, "development")[0]
    assert dev["text"] == "Ann стала теплее." and dev["anchor_id"] in new_path
    now = store.anchored_rows(db, copy_id, "now_line")[0]
    assert now["locked"] is True and now["target"] == "block-that-is-not-here"
    assert store.member_flags(db, copy_id)[made["ann"]] == {"remembers_all": True, "no_layer": True}
    # threads with their status
    thread = store.thread_rows(db, copy_id)[0]
    assert thread["id"] != made["thread"]["id"] and thread["quote"] == "Я принесу эль завтра"
    assert thread["message_id"] in new_path and thread["chapter_id"] in {c["id"] for c in chapters}
    states = store.thread_states(db, copy_id)
    assert [s["status"] for s in states] == ["kept"] and states[0]["anchor_id"] in new_path
    # the face is the same card (same app), linked to the new chat; the done mark moved too
    assert [f["face_id"] for f in store.face_links(db, chat_id=copy_id)] == [made["face"]["id"]]
    assert len(store.face_rows(db)) == 1
    assert set(store.runs(db, store.LAYER_RUNS, copy_id)) <= {c["id"] for c in chapters}
    assert len(store.runs(db, store.LAYER_RUNS, copy_id)) == 1
    assert store.is_enabled(db, copy_id)
    # the vectors are not in the file: the new chat's job makes them
    await idle(ctx, copy_id)
    assert vectorized(ctx, copy_id)


async def test_a_face_of_another_app_is_made_new_with_its_versions(client, ctx, fake_llm):
    _, chat_id, ids, made = await made_memory(client, ctx, fake_llm)
    text = await export_text(client, chat_id)
    store = plugin_module("store")
    store.delete_face(ctx.db, made["face"]["id"])  # as if the file came from another installation
    copy_id = await import_text(client, text)
    faces = store.face_rows(ctx.db)
    assert [f["name"] for f in faces] == ["Борин"] and faces[0]["id"] != made["face"]["id"]
    assert faces[0]["aliases"] == ["трактирщик"] and faces[0]["origin_chat_id"] == copy_id
    assert len(store.face_versions(ctx.db, faces[0]["id"])) == 1
    assert [f["face_id"] for f in store.face_links(ctx.db, chat_id=copy_id)] == [faces[0]["id"]]


async def test_old_files_without_a_plugin_record_still_import(client, ctx, fake_llm):
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 6, run=False)
    text = await export_text(client, chat_id)
    assert not any(json.loads(line)["type"] == "plugin" for line in text.splitlines() if line.strip())
    header = json.loads(text.splitlines()[0])
    assert header["value"]["version"] == 1  # nothing plugin-made: the older format as before
    copy_id = await import_text(client, text)
    assert len(ctx.chats.all_messages(copy_id)) == 6
    assert not plugin_module("store").is_enabled(ctx.db, copy_id)


async def test_a_file_of_a_newer_memory_format_is_skipped_but_the_chat_imports(client, ctx, fake_llm):
    _, chat_id, ids, _ = await made_memory(client, ctx, fake_llm)
    lines = []
    for line in (await export_text(client, chat_id)).splitlines():
        row = json.loads(line)
        if row["type"] == "plugin":
            row["value"]["data"]["version"] = 99
        lines.append(json.dumps(row, ensure_ascii=False))
    copy_id = await import_text(client, "\n".join(lines) + "\n")
    assert len(ctx.chats.all_messages(copy_id)) == 30
    assert plugin_module("store").chapter_rows(ctx.db, copy_id) == []


async def test_data_that_came_while_the_plugin_was_off_is_given_when_it_is_switched_on(client, ctx, fake_llm):
    _, chat_id, ids, made = await made_memory(client, ctx, fake_llm)
    text = await export_text(client, chat_id)
    assert (await client.post("/api/plugins/memory/disable")).status_code == 200
    copy_id = await import_text(client, text)
    assert ctx.plugin_repo.data_get("memory", copy_id, "import:memory") is not None
    assert (await client.post("/api/plugins/memory/enable", json={})).status_code == 200
    memory_module().WAKE_DELAY = 0.01
    store = plugin_module("store")
    assert len(store.chapter_rows(ctx.db, copy_id)) == len(made["chapters"])
    assert ctx.plugin_repo.data_get("memory", copy_id, "import:memory") is None


# -- the assistant -------------------------------------------------------------------------------------------


def env(ctx, chat_id=None):
    return assistant_app.Env(ctx, chat_id)


async def test_the_assistant_finds_the_actions_and_reads_without_a_journal(client, ctx, fake_llm):
    _, chat_id, ids, made = await made_memory(client, ctx, fake_llm)
    routes = (await assistant_app.app_routes(env(ctx, chat_id), {"filter": "memory"})).result
    assert "POST /plugins/memory/actions/summary_edit" in routes
    assert "POST /plugins/memory/actions/chapters" in routes and "[reads only]" in routes.split("actions/chapters")[1].splitlines()[0]
    assert "[asks the user first]" in routes.split("actions/face_add")[1].splitlines()[0]
    assert "actions/undo" not in routes  # an action with no description stays hidden
    outcome = await assistant_app.app_call(env(ctx, chat_id), {
        "method": "POST", "path": "/plugins/memory/actions/chapters", "body": {"chat_id": chat_id}})
    assert outcome.result["status"] == 200 and outcome.what == ""  # nothing to journal
    assert len(outcome.result["data"]["result"]["chapters"]) >= 2


async def test_the_assistant_edits_a_summary_and_undo_puts_it_back(client, ctx, fake_llm):
    _, chat_id, ids, made = await made_memory(client, ctx, fake_llm)
    store = plugin_module("store")
    chapter = made["chapters"][1]
    before = store.current_summaries(ctx.db, chat_id)[chapter["id"]]["text"]
    outcome = await assistant_app.app_call(env(ctx, chat_id), {
        "method": "POST", "path": "/plugins/memory/actions/summary_edit", "what": "Пересказ главы 2",
        "body": {"chat_id": chat_id, "chapter_id": chapter["id"], "text": "Новый пересказ, написанный помощником."}})
    assert outcome.result["status"] == 200, outcome.result
    now = store.current_summaries(ctx.db, chat_id)[chapter["id"]]
    assert now["text"].startswith("Новый") and now["locked"] is True and now["by"] == "hand"
    assert outcome.what == "Пересказ главы 2"
    snapshot.restore(ctx.db, outcome.undo)
    assert store.current_summaries(ctx.db, chat_id)[chapter["id"]]["text"] == before


async def test_a_mini_card_and_a_paid_run_need_the_users_yes(client, ctx, fake_llm):
    _, chat_id, ids, made = await made_memory(client, ctx, fake_llm)
    for action in ("face_add", "bulk_start", "doc_start", "doc_delete", "face_make_real"):
        question = assistant_app._ask_app_call(env(ctx, chat_id), {
            "method": "POST", "path": f"/plugins/memory/actions/{action}"})
        assert question, action
    for action in ("chapters", "faces", "summary_edit", "vectorize"):
        assert assistant_app._ask_app_call(env(ctx, chat_id), {
            "method": "POST", "path": f"/plugins/memory/actions/{action}"}) == ""
    # after the yes: the card is made, linked to the chat, locked, and undo removes it
    outcome = await assistant_app.app_call(env(ctx, chat_id), {
        "method": "POST", "path": "/plugins/memory/actions/face_add", "what": "Грег",
        "body": {"chat_id": chat_id, "name": "Грег", "card": "Грег — заезжий торговец.", "aliases": ["торговец"]}})
    assert outcome.result["status"] == 200, outcome.result
    store = plugin_module("store")
    names = {f["name"]: f for f in store.face_rows(ctx.db)}
    assert names["Грег"]["locked"] is True and names["Грег"]["made_by"] == "hand"
    assert any(link["face_id"] == names["Грег"]["id"] for link in store.face_links(ctx.db, chat_id=chat_id))
    again = await client.post("/api/plugins/memory/actions/face_add", json={
        "chat_id": chat_id, "name": "торговец", "card": "Тот же."})
    assert again.status_code == 409  # the same person is already known: edit that card
    snapshot.restore(ctx.db, outcome.undo)
    assert "Грег" not in {f["name"] for f in store.face_rows(ctx.db)}


async def test_a_paid_run_without_the_confirmed_mark_is_refused(client, ctx, fake_llm):
    _, chat_id, ids, _ = await made_memory(client, ctx, fake_llm)
    r = await client.post("/api/plugins/memory/actions/bulk_start", json={"chat_id": chat_id})
    assert r.status_code == 409 and r.json()["error"]["code"] == "memory_confirm"


# -- «Разобрать весь чат» ------------------------------------------------------------------------------------


async def test_the_price_comes_first_and_the_run_does_the_work(client, ctx, fake_llm):
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава", 24: "На следующий день."},
                                            run=False)
    await models_on(client, summaries=True)
    await client.put("/api/spending/settings", json={"prices": {"m": {"in": 1.0, "out": 2.0}}})
    before = len(fake_llm.requests)
    estimate = (await client.post("/api/plugins/memory/actions/bulk_estimate", json={"chat_id": chat_id})).json()["result"]
    assert len(fake_llm.requests) == before  # asking the price spends nothing
    assert estimate["messages"] == 30 and estimate["steps"][0]["step"] == "summary" and estimate["steps"][0]["count"] >= 1
    assert estimate["cost"] and estimate["cost"] > 0 and estimate["price_text"].startswith("около")
    assert estimate["model"] == "m" and "векторы" not in estimate["off"]
    assert not plugin_module("store").is_enabled(ctx.db, chat_id)  # nothing was switched on by asking
    fake_llm.queue(*[json.dumps({"title": f"Г{i}", "summary": "Пересказ про эль и дорогу. " * 5}) for i in range(5)])
    memory_module().WAKE_DELAY = 0.01
    started = (await client.post("/api/plugins/memory/actions/bulk_start",
                                 json={"chat_id": chat_id, "confirmed": True})).json()["result"]
    assert started["on"] and started["estimate"]  # the price that was agreed to is remembered
    await idle(ctx, chat_id)
    state = (await client.post("/api/plugins/memory/actions/bulk_state", json={"chat_id": chat_id})).json()["result"]
    assert state["finished"] and not state["on"] and state["left"] == 0
    assert state["steps"][0]["done"] == state["steps"][0]["total"] >= 2
    assert state["spent"]["requests"] >= 2


async def test_without_a_known_price_the_text_says_so_and_never_invents_a_number(client, ctx, fake_llm):
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    estimate = (await client.post("/api/plugins/memory/actions/bulk_estimate", json={"chat_id": chat_id})).json()["result"]
    assert estimate["cost"] is None and "цена не известна" in estimate["price_text"]


async def test_a_run_that_gave_up_on_a_rate_limit_is_woken_again(client, ctx, fake_llm, monkeypatch):
    setup, chat_id, ids = await chapter_chat(client, ctx, fake_llm, 30, {12: "*** Глава"}, run=False)
    await models_on(client, summaries=True)
    server = memory_module()
    server.WAKE_DELAY = 0.01
    server.RATE_RETRY = 0.01
    work = plugin_module("work")
    calls = {"n": 0}
    real = work.run

    async def flaky(api, chat, job, cache=None):
        calls["n"] += 1
        if calls["n"] == 1:
            from tavern.core.background import JobGaveUp
            raise JobGaveUp("лимит")
        return await real(api, chat, job, cache)

    monkeypatch.setattr(work, "run", flaky)
    fake_llm.queue(*[json.dumps({"title": "Г", "summary": "Пересказ про эль и дорогу. " * 5}) for _ in range(5)])
    await client.post("/api/plugins/memory/actions/bulk_start", json={"chat_id": chat_id, "confirmed": True})
    import asyncio
    for _ in range(300):
        await asyncio.sleep(0.02)
        if calls["n"] >= 2 and not ctx.jobs.status("memory", f"chat:{chat_id}")["running"]:
            break
    assert calls["n"] >= 2  # it came back by itself
    await idle(ctx, chat_id)
    assert plugin_module("store").current_summaries(ctx.db, chat_id)


# -- the quiet window (ADR R2) -------------------------------------------------------------------------------


async def test_a_step_cancelled_three_times_waits_a_quiet_window(ctx):
    import asyncio
    fg = ctx.foreground
    fg.quiet_seconds = 0.15
    started = []

    async def step():
        started.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.2)
        return "done"

    notes = []
    task = asyncio.ensure_future(fg.run_background(step, lambda n, quiet: notes.append((n, quiet))))
    for _ in range(3):  # three replies, each cancels the step in flight
        await asyncio.sleep(0.03)
        with fg.busy():
            await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert len(started) == 3 and not task.done()  # after the third cancel it waits in quiet
    assert (3, True) in notes
    assert await task == "done"
    assert len(started) == 4 and started[3] - started[2] >= 0.14  # the fourth try began after the quiet window


async def test_a_reply_during_the_quiet_window_restarts_it(ctx):
    import asyncio
    fg = ctx.foreground
    fg.quiet_seconds = 0.12
    fg._last_busy = time.monotonic()
    ran = []

    async def step():
        ran.append(1)
        await asyncio.sleep(0.02)

    # a step that was cancelled three times already
    fg.quiet_after = 0
    task = asyncio.ensure_future(fg.run_background(step))
    await asyncio.sleep(0.06)
    with fg.busy():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.07)
    assert not ran  # still waiting: the reply moved the window
    await task
    assert ran == [1]


# -- a model of another service is never kept -----------------------------------------------------------------


async def test_changing_the_connection_empties_the_saved_model(client, ctx):
    other = (await client.post("/api/connections", json={"name": "o", "base_url": "http://o/v1", "model": "x"})).json()
    assert (await client.post("/api/plugins/memory/enable", json={})).status_code == 200
    r = await client.put("/api/plugins/memory/settings", json={"memory_model_name": "gemini-big"})
    assert r.json()["values"]["memory_model_name"] == "gemini-big"
    r = await client.put("/api/plugins/memory/settings", json={"memory_connection": other["id"]})
    assert r.status_code == 200 and r.json()["values"]["memory_model_name"] == ""
    # both in one save: the model the person chose goes in
    r = await client.put("/api/plugins/memory/settings", json={"memory_connection": "", "memory_model_name": "pick"})
    assert r.json()["values"]["memory_model_name"] == "pick"
    # the same connection saved again keeps the model
    r = await client.put("/api/plugins/memory/settings", json={"memory_connection": ""})
    assert r.json()["values"]["memory_model_name"] == "pick"


async def test_every_new_or_touched_setting_has_a_folded_explanation(client):
    plugins = (await client.get("/api/plugins")).json()["plugins"]
    memory = next(p for p in plugins if p["id"] == "memory")
    fields = {f["key"]: f for f in memory["settings"]}
    for key in ("docs_percent", "memory_connection", "memory_model_name"):
        assert len(fields[key].get("help", "")) > 80, key
    source = open("web/components/Plugins.js", encoding="utf-8").read()
    assert "<details" in source and "Что это?" in source  # help is folded, a small «Что это?» that opens
    assert "visibleSettings" in source  # model fields wait for the permission to use models
