"""Assistant: unanswered questions are not sent as chosen, a stopped request can always be continued,
and a running one goes on while another conversation is made."""

from __future__ import annotations

import asyncio
import json

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731

BIG = J(say="", actions=[], big=True)
TWO = J(say="Вопросы:", questions=[
    {"text": "Тон?", "options": ["Мрачный", "Светлый"], "default": 0},
    {"text": "Размер?", "options": ["Малый", "Большой"], "default": 1}])
PLAN = J(say="План:", goal="Лорбук", decisions=[], items=[{"title": "Основа", "detail": "правила"}])


async def setup(client, base_url: str = "http://llm/v1") -> str:
    conn = (await client.post("/api/connections", json={"name": "Main", "base_url": base_url, "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return (await client.post("/api/assistant/chats")).json()["id"]


async def wait(client, ctx, conv: str, response) -> dict:
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)
    return (await client.get(f"/api/assistant/chats/{conv}")).json()


async def test_a_question_left_alone_is_the_assistants_choice_not_the_users(client, ctx, fake_llm):
    conv = await setup(client)
    fake_llm.queue(BIG, TWO)
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "Сделай лорбук"}))
    fake_llm.queue(PLAN)
    q1, q2 = [q["id"] for q in ctx.assistant_repo.task(conv)["questions"]][:2]
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers",
                                                            json={"answers": {q1: "Свой тон"}}))
    said = next(m["text"] for m in state["messages"] if m["data"].get("answers"))
    assert "Тон? — Свой тон" in said
    assert "Размер? — на ваш вкус (предложено: Большой)" in said
    assert "Мрачный" not in said


async def test_a_stopped_short_request_can_be_continued(client, ctx, fake_llm):
    conv = await setup(client)
    repo = ctx.assistant_repo
    assert (await client.get(f"/api/assistant/chats/{conv}")).json()["resumable"] is False
    repo.add_message(conv, "user", "Сделай что-нибудь", {})  # the run died: a restart, an error, «Стоп»
    assert (await client.get(f"/api/assistant/chats/{conv}")).json()["resumable"] is True
    fake_llm.queue(J(say="Сделал.", actions=[]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert any(m["role"] == "user" and m["text"] == "Продолжай" for m in state["messages"])
    assert state["messages"][-1]["text"] == "Сделал."
    assert state["resumable"] is False
    assert (await client.post(f"/api/assistant/chats/{conv}/go", json={})).status_code == 409


async def test_an_error_and_a_step_limit_can_be_continued(client, ctx):
    conv = await setup(client)
    repo = ctx.assistant_repo
    repo.add_message(conv, "user", "Сделай", {})
    repo.add_message(conv, "assistant", "Лимит расхода достигнут", {"error": True})
    assert (await client.get(f"/api/assistant/chats/{conv}")).json()["resumable"] is True
    other = (await client.post("/api/assistant/chats")).json()["id"]
    repo.add_message(other, "user", "Сделай", {})
    repo.add_message(other, "assistant", "Я сделал 8 шагов — это предел на одну просьбу.", {})
    assert (await client.get(f"/api/assistant/chats/{other}")).json()["resumable"] is True


async def test_answered_questions_without_a_plan_can_be_continued(client, ctx, fake_llm):
    conv = await setup(client)
    fake_llm.queue(BIG, TWO)
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "Сделай лорбук"}))
    assert (await client.get(f"/api/assistant/chats/{conv}")).json()["resumable"] is False  # waits for answers
    task = ctx.assistant_repo.task(conv)
    ctx.assistant_repo.set_task(conv, {**task, "answers": {"language": "Русский"}})  # the plan run was stopped
    assert (await client.get(f"/api/assistant/chats/{conv}")).json()["resumable"] is True
    fake_llm.queue(PLAN)
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert state["task"]["phase"] == "plan"


async def test_a_running_request_goes_on_when_another_conversation_is_made(client, ctx, live_llm):
    conv = await setup(client, live_llm.base_url)
    live_llm.chunk_delay = 0.05
    live_llm.queue(" ".join(["долго"] * 200))
    run_id = (await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "Что-то долгое"})).json()["run_id"]
    new = (await client.post("/api/assistant/chats")).json()["id"]
    chat = (await client.post("/api/chats", json={})).status_code  # an ordinary chat (any answer): must not touch it
    assert chat in (200, 201, 400, 422)
    listed = {c["id"]: c for c in (await client.get("/api/assistant")).json()["chats"]}
    assert listed[conv]["running"] is True and listed[new]["running"] is False
    assert (await client.get(f"/api/assistant/chats/{conv}")).json()["run_id"] == run_id
    assert not ctx.runs.get(run_id).task.done()
    await client.post(f"/api/assistant/chats/{conv}/stop")
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
