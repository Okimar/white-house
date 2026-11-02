"""The assistant's engine (stage 10а) against the fake model: big work goes questions → plan →
items → critic → report on a task kept with the conversation; the task is pinned in every call;
the critic only reads and never sees the conversation; messages sent meanwhile are folded into the
plan; «Что я о вас знаю»; a model per role and its spending."""

from __future__ import annotations

import asyncio
import json

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731


def act(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


async def setup(client) -> dict:
    conn = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "fake-model"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return conn


async def wait(client, ctx, conv: str, response) -> dict:
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)
    return (await client.get(f"/api/assistant/chats/{conv}")).json()


def texts(fake_llm, index: int) -> str:
    return "\n".join(m["content"] for m in fake_llm.requests[index]["messages"])


BIG = J(say="", actions=[], big=True)


def checks(**failed) -> str:
    """The critic's answer: every check passes with evidence, except those given as n=(where, problem, fix)."""
    out = []
    for n in range(1, 7):
        if f"c{n}" in failed:
            where, problem, fix = failed[f"c{n}"]
            out.append({"n": n, "ok": False, "evidence": "…", "where": where, "problem": problem, "fix": fix})
        else:
            out.append({"n": n, "ok": True, "evidence": "цитата"})
    return J(say="", actions=[], checks=out)
QUESTIONS = J(say="Пара вопросов:", questions=[
    {"text": "Какой тон?", "options": ["Мрачный", "Светлый"], "default": 0},
    {"text": "Изюминка?", "options": ["Магия стоит памяти", "Город на спине черепахи"], "default": 1},
])
PLAN = J(say="План:", goal="Лорбук о городе", decisions=["тон мрачный", "город на спине черепахи"],
         items=[{"title": "Основа мира", "detail": "правила"}, {"title": "Районы", "detail": "три района"}])


async def big_until_plan(client, ctx, fake_llm, conv: str) -> dict:
    fake_llm.queue(BIG, QUESTIONS)
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "Сделай крутой лорбук про город"}))
    assert state["task"]["phase"] == "questions"
    fake_llm.queue(PLAN)
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers",
                                                            json={"answers": {"q1": "Светлый"}}))
    assert state["task"]["phase"] == "plan"
    return state


async def test_big_work_goes_questions_plan_items_critic_report(client, ctx, fake_llm):
    await setup(client)
    book = (await client.post("/api/lorebooks", json={"name": "Город"})).json()
    conv = (await client.post("/api/assistant/chats")).json()["id"]

    fake_llm.queue(BIG, QUESTIONS)
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "Сделай крутой лорбук про город"}))
    questions = state["task"]["questions"]
    # The planner's questions, then the engine's own question about the language (Russian first).
    assert [q["id"] for q in questions] == ["q1", "q2", "language"]
    assert questions[-1]["options"][0] == "Русский"
    card = state["messages"][-1]
    assert card["data"]["kind"] == "questions" and card["text"] == "Пара вопросов:"
    assert "planner" in texts(fake_llm, 1) and "Do not ask about the language" in texts(fake_llm, 1)

    # Answers: one given, the others take their defaults; the plan comes and waits for «Давай».
    fake_llm.queue(PLAN)
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers",
                                                            json={"answers": {"q1": "Светлый"}}))
    task = state["task"]
    assert task["answers"] == {"q1": "Светлый", "q2": "Город на спине черепахи", "language": "Русский"}
    assert task["phase"] == "plan" and [i["id"] for i in task["items"]] == ["1", "2"]
    assert "Какой тон? → Светлый" in texts(fake_llm, 2)
    assert state["messages"][-1]["data"]["kind"] == "plan"

    # «Давай»: each item is a short loop; the critic finds one problem, the writer fixes it, the
    # second round accepts; the report closes the task.
    fake_llm.queue(
        J(say="Пишу основу", actions=[act("create_bubble", owner_id=book["id"], title="Основа",
                                          content="Город стоит на черепахе.", mode="always")]),
        J(say="Основа готова: пузырь «Основа»", actions=[]),
        J(say="Районы", actions=[act("create_bubble", owner_id=book["id"], title="Порт", content="Порт",
                                     keywords=["порт"])]),
        J(say="Районы готовы", actions=[]),
        J(say="", actions=[act("list_bubbles", owner_id=book["id"])], checks=[]),
        checks(c4=("Порт", "пусто", "дописать")),
        J(say="Дописываю порт", actions=[act("list_bubbles", owner_id=book["id"])]),
        J(say="Исправлено", actions=[]),
        checks(),
        J(say="Сделан лорбук «Город»: 2 записи."),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    task = state["task"]
    assert task["phase"] == "done"
    assert [(i["id"], i["status"]) for i in task["items"]] == [("1", "done"), ("2", "done")]
    assert task["items"][0]["summary"] == "Основа готова: пузырь «Основа»"
    assert task["open_issues"] == [] and task["reviewed"] is True
    kinds = [m["data"].get("kind") for m in state["messages"] if m["role"] == "assistant"]
    assert kinds[-1] == "report" and "critic" in kinds and "step" in kinds
    assert state["messages"][-1]["text"] == "Сделан лорбук «Город»: 2 записи."
    blocks = ctx.db.fetchall("SELECT title FROM character_blocks WHERE owner_id = ? ORDER BY title", (book["id"],))
    assert [b["title"] for b in blocks] == ["Основа", "Порт"]

    # The task is pinned in every writer call, with the current item and a closing reminder.
    first_writer = fake_llm.requests[3]["messages"]
    assert "THE TASK" in first_writer[1]["content"] and "1. [NOW] Основа мира" in first_writer[1]["content"]
    assert "город на спине черепахи" in first_writer[1]["content"]
    assert "sum up names and ids)" in first_writer[-1]["content"]  # just before the form
    second_item = texts(fake_llm, 5)
    assert "1. [done] Основа мира" in second_item and "made: Основа готова" in second_item
    # The critic sees the task and the journal, never the conversation with the user.
    critic = texts(fake_llm, 7)
    assert "You are the critic" in critic and "WHAT WAS CHANGED" in critic
    # Tavern hands the critic the made text itself: titles, modes, keywords, nesting and content.
    assert "LOREBOOK «Город»" in critic and "- [Основа] (always; 24 chars)" in critic
    assert "- [Порт] (search; keywords: порт; 4 chars)" in critic and "6. Lorebook entries" in critic
    assert "Ответы:" not in critic and "The conversation so far" not in critic
    fix = texts(fake_llm, 9)
    assert "Исправить замечания критика" in fix and "Порт: пусто (check 4) → дописать" in fix

    # Spending per role is on the task; the page's lists show the phase.
    assert set(task["spent"]) >= {"main", "planner", "writer", "critic", "small"}
    assert task["spent"]["writer"]["requests"] == 6
    overview = (await client.get("/api/assistant")).json()
    assert overview["chats"][0]["phase"] == "done"


async def test_the_critic_only_reads_and_its_rounds_end_honestly(client, ctx, fake_llm):
    await setup(client)
    await client.put("/api/assistant/settings", json={"critic_rounds": 1})
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    await big_until_plan(client, ctx, fake_llm, conv)
    fake_llm.queue(
        J(say="книга", actions=[act("create_lorebook", name="Город")]), J(say="1 готов", actions=[]),
        J(say="2 готов", actions=[]),
        J(say="", actions=[act("create_character", name="Взлом")], checks=[]),
        J(say="", actions=[], checks=[{"n": 1, "ok": True, "evidence": "есть"}, {"n": 2, "ok": True},
                                      {"n": 4, "ok": False, "problem": "мало деталей", "fix": "добавить"}]),
        J(say="Не всё принято критиком: мало деталей."),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert (await client.get("/api/characters")).json()["characters"] == []
    assert "The critic only reads" in texts(fake_llm, 7)
    assert "FACTS (from Tavern, always true): changes made: 1. Items not finished: none." in texts(fake_llm, 6)
    # One round allowed: no fix item, the open issue reaches the report.
    # A pass without evidence counts as a problem; so do checks left unanswered.
    assert state["task"]["open_issues"] == [
        {"where": "", "problem": "passed without evidence (check 2)", "fix": ""},
        {"where": "", "problem": "мало деталей (check 4)", "fix": "добавить"},
        {"where": "", "problem": "the critic answered 3 of 6 checks", "fix": "check the rest"}]
    assert "мало деталей (check 4) → добавить" in texts(fake_llm, 8)
    assert state["messages"][-1]["data"]["open_issues"]


async def test_a_stuck_item_stops_the_work_and_tavern_says_nothing_was_made(client, ctx, fake_llm):
    # Regression (live Gemini, stage 10а): the writer repeated a call without arguments eleven times,
    # the critic accepted the empty result and the report claimed a finished lorebook.
    await setup(client)
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    await big_until_plan(client, ctx, fake_llm, conv)
    empty = J(say="Создаю лорбук", actions=[act("create_lorebook")])
    fake_llm.queue(empty, empty, empty, J(say="не должно дойти", actions=[]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert len(fake_llm.requests) == 3 + 3  # three tries of item 1, then no critic and no model report
    assert "create_lorebook takes {name: name" in texts(fake_llm, 4)
    assert "The same error as last time" in texts(fake_llm, 5)
    task = state["task"]
    assert [i["status"] for i in task["items"]] == ["failed", "todo"]
    assert task["failed"].startswith("1. Основа мира: stopped: the same error 3 times")
    last = state["messages"][-1]
    assert last["data"]["kind"] == "report" and last["data"]["failed"] is True
    assert last["text"].startswith("Ничего не сделано")
    assert not [m for m in state["messages"] if m["data"].get("kind") == "critic"]


async def test_words_on_a_plan_remake_it_or_approve_it(client, ctx, fake_llm):
    await setup(client)
    await client.put("/api/assistant/settings", json={"critic": False})
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    await big_until_plan(client, ctx, fake_llm, conv)

    fake_llm.queue(J(say="Добавил", goal="g", decisions=["d"], items=[{"title": "A"}, {"title": "B"}, {"title": "C"}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "добавь пункт про драконов"}))
    assert [i["title"] for i in state["task"]["items"]] == ["A", "B", "C"]
    assert "The user's words now:\nдобавь пункт про драконов" in texts(fake_llm, -1)
    assert "Current plan" in texts(fake_llm, -1)

    # Words that only approve start the work without a new plan card.
    fake_llm.queue(J(say="", goal="g", decisions=["d"], items=[{"title": "A"}], approved=True),
                   J(say="A", actions=[]), J(say="B", actions=[]), J(say="C", actions=[]), J(say="Готово"))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "давай"}))
    assert state["task"]["phase"] == "done"
    assert [i["status"] for i in state["task"]["items"]] == ["done", "done", "done"]
    assert sum(1 for m in state["messages"] if m["data"].get("kind") == "plan") == 2


async def test_a_writer_splits_its_item_once(client, ctx, fake_llm):
    await setup(client)
    await client.put("/api/assistant/settings", json={"critic": False})
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    await big_until_plan(client, ctx, fake_llm, conv)
    fake_llm.queue(
        J(say="Разбиваю", actions=[], add_items=[{"title": "Верхний город"}, {"title": "Нижний город"}]),
        J(say="верх", actions=[]), J(say="низ", actions=[]),
        J(say="районы", actions=[], add_items=[{"title": "x"}, {"title": "y"}]),  # sub-items get no split
        J(say="Готово"),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    items = state["task"]["items"]
    assert [(i["id"], i["title"], i["parent"]) for i in items] == [
        ("1", "Основа мира", ""), ("1.1", "Верхний город", "1"), ("1.2", "Нижний город", "1"),
        ("2", "Районы", ""), ("2.1", "x", "2"), ("2.2", "y", "2")]
    assert "1.1. [NOW] Верхний город" in texts(fake_llm, 4)


async def test_a_message_during_the_work_goes_into_the_plan(client, ctx, fake_llm):
    await setup(client)
    await client.put("/api/assistant/settings", json={"critic": False})
    character = (await client.post("/api/characters", json={"name": "Старик"})).json()
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    await big_until_plan(client, ctx, fake_llm, conv)
    # Item 1 deletes something: the run waits for «Разрешить»; meanwhile the user writes.
    fake_llm.queue(
        J(say="Удаляю", actions=[act("delete_character", id=character["id"])]),
        J(note="добавлю драконов", add_items=[{"title": "Драконы", "detail": "два вида"}]),
        J(say="1 готов", actions=[]), J(say="драконы", actions=[]), J(say="2", actions=[]), J(say="Готово"),
    )
    sent = await client.post(f"/api/assistant/chats/{conv}/go", json={})
    run = ctx.runs.get(sent.json()["run_id"])
    for _ in range(500):
        pending = (await client.get(f"/api/assistant/chats/{conv}")).json()["ask"]
        if pending:
            break
        await asyncio.sleep(0.01)
    meanwhile = await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "и драконов добавь"})
    assert meanwhile.status_code == 202 and meanwhile.json()["run_id"] == run.id
    await client.post(f"/api/assistant/chats/{conv}/confirm", json={"ask_id": pending["id"], "allow": True})
    await asyncio.wait([run.task], timeout=10)
    state = (await client.get(f"/api/assistant/chats/{conv}")).json()
    assert [(i["id"], i["title"]) for i in state["task"]["items"]] == [
        ("1", "Основа мира"), ("2", "Драконы"), ("3", "Районы")]
    assert state["task"]["added"] == ["и драконов добавь"]
    assert any(m["text"] == "В план: добавлю драконов" for m in state["messages"])
    assert "и драконов добавь" in texts(fake_llm, 4)


async def test_memory_is_shown_given_to_every_role_and_learned(client, ctx, fake_llm):
    await setup(client)
    made = await client.post("/api/assistant/memory", json={"text": "любит мрачное фэнтези"})
    assert made.status_code == 201
    fact = made.json()
    edited = await client.put(f"/api/assistant/memory/{fact['id']}", json={"text": "любит тёмное фэнтези"})
    assert edited.json()["text"] == "любит тёмное фэнтези"
    assert (await client.post("/api/assistant/memory", json={"text": ""})).status_code == 400
    conv = (await client.post("/api/assistant/chats")).json()["id"]

    fake_llm.queue(J(say="Запомню", actions=[], remember=["без эмодзи", "любит тёмное фэнтези"]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "запомни: без эмодзи"}))
    assert "любит тёмное фэнтези" in texts(fake_llm, 0)
    memory = (await client.get("/api/assistant")).json()["memory"]
    assert [f["text"] for f in memory] == ["любит тёмное фэнтези", "без эмодзи"]  # no duplicate
    line = next(m for m in state["messages"] if m["data"].get("kind") == "remembered")
    assert line["text"] == "Запомнил: без эмодзи" and line["data"]["fact_id"] == memory[1]["id"]
    assert (await client.delete(f"/api/assistant/memory/{memory[1]['id']}")).status_code == 200
    assert len((await client.get("/api/assistant/memory")).json()["memory"]) == 1

    fake_llm.queue(BIG, QUESTIONS)
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "лорбук"}))
    assert "любит тёмное фэнтези" in texts(fake_llm, -1)  # the planner knows it too


async def test_a_model_per_role_and_settings_are_checked(client, ctx, fake_llm):
    await setup(client)
    assert (await client.put("/api/assistant/settings", json={"critic_rounds": 9})).status_code == 400
    assert (await client.put("/api/assistant/settings", json={"progress": "loud"})).status_code == 400
    assert (await client.put("/api/assistant/settings",
                             json={"roles": {"planner": {"source": "connection:nope"}}})).status_code == 404
    saved = (await client.put("/api/assistant/settings", json={
        "roles": {"planner": {"model": "smart-model"}, "small": {"model": "lite-model"}},
        "progress": "minimal"})).json()
    assert saved["roles"]["planner"] == {"source": "", "model": "smart-model"}
    assert saved["roles"]["writer"] == {"source": "", "model": ""} and saved["progress"] == "minimal"
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    await big_until_plan(client, ctx, fake_llm, conv)
    assert [r["model"] for r in fake_llm.requests] == ["fake-model", "smart-model", "smart-model"]


async def test_no_questions_goes_straight_to_the_plan_and_a_task_can_be_dropped(client, ctx, fake_llm):
    await setup(client)
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    fake_llm.queue(J(say="", actions=[], big=True, no_questions=True), PLAN)
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "лорбук без вопросов"}))
    assert state["task"]["phase"] == "plan" and state["task"]["language"] == "Русский"
    assert (await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}})).status_code == 409
    assert (await client.post(f"/api/assistant/chats/{conv}/drop")).status_code == 200
    state = (await client.get(f"/api/assistant/chats/{conv}")).json()
    assert state["task"]["phase"] == "dropped"
    assert (await client.post(f"/api/assistant/chats/{conv}/go", json={})).status_code == 409
    # The next request is an ordinary one again.
    fake_llm.queue(J(say="Привет", actions=[]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "привет"}))
    assert state["messages"][-1]["text"] == "Привет"
