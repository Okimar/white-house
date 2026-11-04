"""Regression tests for the assistant: refusals are shown, a critic that did no work is not a pass,
late messages are not lost, interrupted work can be resumed, nothing changes while a reply is written."""

from __future__ import annotations

import asyncio
import json

from tavern.services import assistant_app
from tavern.services import assistant_engine as engine

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731


def act(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


BIG = J(say="", actions=[], big=True)
QUESTIONS = J(say="Вопрос:", questions=[{"text": "Тон?", "options": ["Мрачный", "Светлый"], "default": 0}])
PLAN = J(say="План:", goal="Лорбук", decisions=[], items=[{"title": "Основа", "detail": "правила"}])
ACCEPT = J(say="", actions=[], checks=[{"n": n, "ok": True, "evidence": "цитата"} for n in range(1, 7)])


async def setup(client, base_url: str = "http://llm/v1") -> str:
    conn = (await client.post("/api/connections", json={"name": "Main", "base_url": base_url, "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return (await client.post("/api/assistant/chats")).json()["id"]


async def wait(client, ctx, conv: str, response) -> dict:
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)
    return (await client.get(f"/api/assistant/chats/{conv}")).json()


async def say(client, ctx, conv: str, text: str) -> dict:
    return await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": text}))


async def until_plan(client, ctx, fake_llm, conv: str) -> str:
    book = (await client.post("/api/lorebooks", json={"name": "Город"})).json()["id"]
    fake_llm.queue(BIG, QUESTIONS)
    await say(client, ctx, conv, "Сделай лорбук")
    fake_llm.queue(PLAN)
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}}))
    return book


def bubble(book: str, title: str = "Основа") -> str:
    return J(say="Пишу", actions=[act("create_bubble", owner_id=book, title=title, content="Текст", mode="always")])


async def test_a_refused_change_shows_in_the_journal_without_undo(client, ctx, fake_llm):
    conv = await setup(client)
    fake_llm.queue(J(say="Меняю", actions=[act("app_call", method="PUT", path="/characters/nobody",
                                               body={"name": "x"}, what="переименовать персонажа")]),
                   J(say="Готово.", actions=[]))
    state = await say(client, ctx, conv, "Переименуй")
    failed = [a for a in state["actions"] if a["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["what"].startswith("Не вышло: переименовать персонажа")
    assert "nobody" in failed[0]["what"]
    assert (await client.post(f"/api/assistant/actions/{failed[0]['id']}/undo")).status_code == 409


async def test_a_tool_error_shows_but_reading_errors_do_not(client, ctx, fake_llm):
    conv = await setup(client)
    fake_llm.queue(J(say="", actions=[act("create_lorebook"), act("read_character", id="nobody"),
                                      act("app_call", method="GET", path="/characters/nobody")]),
                   J(say="Всё.", actions=[]))
    state = await say(client, ctx, conv, "Сделай")
    assert [a["tool"] for a in state["actions"] if a["status"] == "failed"] == ["create_lorebook"]


async def test_restoring_a_copy_is_closed_to_the_assistant(client, ctx, fake_llm):
    conv = await setup(client)
    name = (await client.post("/api/backups", json={})).json()["name"]
    await client.post("/api/characters", json={"name": "После копии"})
    fake_llm.queue(J(say="", actions=[act("app_call", method="POST", path=f"/backups/{name}/restore")]),
                   J(say="Готово.", actions=[]))
    state = await say(client, ctx, conv, "Восстанови")
    tool = next(m for m in state["messages"] if m["role"] == "tool")
    assert "closed" in tool["data"]["results"][0]["error"] or "Копии" in tool["data"]["results"][0]["error"]
    assert [a["status"] for a in state["actions"]] == ["failed"]
    assert "После копии" in [c["name"] for c in (await client.get("/api/characters")).json()["characters"]]
    routes = (await assistant_app.app_routes(assistant_app.Env(ctx, None), {})).result
    assert "/backups/{name}/restore" not in routes


async def test_a_critic_that_gave_no_checks_is_not_a_pass(client, ctx, fake_llm):
    conv = await setup(client)
    await client.put("/api/assistant/settings", json={"max_steps": 2})
    book = await until_plan(client, ctx, fake_llm, conv)
    fake_llm.queue(bubble(book), J(say="Готово", actions=[]),
                   J(say="", actions=[]), J(say="", actions=[]),  # the critic checks nothing, twice
                   J(say="Лорбук сделан."))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    critic = [m for m in state["messages"] if m["data"].get("kind") == "critic"]
    assert len(critic) == 1 and "замечаний нет" not in critic[0]["text"] and "не проверил" in critic[0]["text"]
    assert state["task"]["open_issues"] and state["messages"][-1]["data"]["open_issues"]
    assert "does not accept" in "\n".join(m["content"] for m in fake_llm.requests[-1]["messages"])


async def test_a_message_sent_during_the_critic_goes_into_the_plan(client, ctx, fake_llm, monkeypatch):
    conv = await setup(client)
    book = await until_plan(client, ctx, fake_llm, conv)
    real = engine.review
    calls = []

    async def review(*args):
        await real(*args)
        if not calls:
            ctx.assistant_repo.add_message(conv, "user", "Добавь порт", {"inbox": True})
        calls.append(1)

    monkeypatch.setattr(engine, "review", review)
    fake_llm.queue(bubble(book), J(say="Готово", actions=[]), ACCEPT,
                   J(note="Добавил порт", add_items=[{"title": "Порт", "detail": "район"}]),  # fold
                   bubble(book, "Порт"), J(say="Порт готов", actions=[]), ACCEPT,
                   J(say="Сделан лорбук с портом."))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert len(calls) == 2  # what was added is checked too
    assert [i["status"] for i in state["task"]["items"]] == ["done", "done"]
    assert any(m["text"] == "В план: Добавил порт" for m in state["messages"])
    assert state["task"]["phase"] == "done"


async def test_a_message_too_late_for_the_work_is_answered_not_dropped(client, ctx, fake_llm, monkeypatch):
    conv = await setup(client)
    book = await until_plan(client, ctx, fake_llm, conv)
    real = engine.report

    async def report(*args):
        await real(*args)
        ctx.assistant_repo.add_message(conv, "user", "И ещё башню", {"inbox": True})

    monkeypatch.setattr(engine, "report", report)
    fake_llm.queue(bubble(book), J(say="Готово", actions=[]), ACCEPT, J(say="Сделано."))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert "И ещё башню" in state["messages"][-1]["text"] and "не успел" in state["messages"][-1]["text"]


async def test_interrupted_work_waits_for_the_user_and_can_be_continued(client, ctx, fake_llm):
    conv = await setup(client)
    book = await until_plan(client, ctx, fake_llm, conv)
    task = ctx.assistant_repo.task(conv)
    ctx.assistant_repo.set_task(conv, {**task, "phase": "work", "journal_from": 0, "seen": 10_000})  # as after a restart
    assert conv in (await client.get("/api/assistant/state")).json()["waiting"]

    fake_llm.queue(bubble(book), J(say="Готово", actions=[]), ACCEPT, J(say="Сделано."))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert state["task"]["phase"] == "done"
    assert any(m["role"] == "user" and m["text"] == "Продолжай" for m in state["messages"])


async def test_what_the_user_writes_when_resuming_goes_into_the_plan(client, ctx, fake_llm):
    conv = await setup(client)
    book = await until_plan(client, ctx, fake_llm, conv)
    task = ctx.assistant_repo.task(conv)
    ctx.assistant_repo.set_task(conv, {**task, "phase": "work", "journal_from": 0, "seen": 0})
    fake_llm.queue(J(note="Учёл мрачный тон"), bubble(book), J(say="Готово", actions=[]), ACCEPT, J(say="Сделано."))
    state = await say(client, ctx, conv, "Пиши мрачнее")
    assert any(m["text"] == "В план: Учёл мрачный тон" for m in state["messages"])
    assert "Пиши мрачнее" in state["task"]["added"]


async def test_work_stopped_by_the_user_does_not_nag(client, ctx, live_llm):
    conv = await setup(client, live_llm.base_url)
    live_llm.queue(BIG, QUESTIONS)
    await say(client, ctx, conv, "Сделай лорбук")
    live_llm.queue(PLAN)
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}}))
    live_llm.chunk_delay = 0.05
    live_llm.queue(" ".join(["долго"] * 200))
    run_id = (await client.post(f"/api/assistant/chats/{conv}/go", json={})).json()["run_id"]
    while ctx.assistant_repo.task(conv).get("phase") != "work":
        await asyncio.sleep(0.01)
    await client.post(f"/api/assistant/chats/{conv}/stop")
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    assert ctx.assistant_repo.task(conv)["stopped"] is True
    assert conv not in (await client.get("/api/assistant/state")).json()["waiting"]


async def test_nothing_changes_while_a_reply_is_written_and_undo_waits(client, ctx, fake_llm, monkeypatch):
    conv = await setup(client)
    fake_llm.queue(J(say="", actions=[act("create_character", name="Новая", description="д")]), J(say="ок", actions=[]))
    state = await say(client, ctx, conv, "Сделай")
    done = next(a for a in state["actions"] if a["status"] == "done")

    monkeypatch.setattr(assistant_app, "QUIET_WAIT", 0.0)
    real = ctx.runs.running
    monkeypatch.setattr(ctx.runs, "running", lambda: [*real(), "some-chat"])
    undo = await client.post(f"/api/assistant/actions/{done['id']}/undo")
    assert undo.status_code == 409 and undo.json()["error"]["code"] == "busy"

    fake_llm.queue(J(say="", actions=[act("create_character", name="Вторая", description="д"),
                                      act("list_characters")]),
                   J(say="ок", actions=[]))
    state = await say(client, ctx, conv, "Ещё")
    assert "Вторая" not in [c["name"] for c in (await client.get("/api/characters")).json()["characters"]]
    tool = [m for m in state["messages"] if m["role"] == "tool"][-1]["data"]["results"]
    assert "error" in tool[0] and "result" in tool[1]  # reading still works
