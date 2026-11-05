"""Skills (stage 10б): the «Инструкции» tab and how the assistant reads them — a copy of a built-in
replaces it, each role reads its own part, the critic gets the skill's checks, a line shows which
skill was taken, and the assistant never changes skills itself."""

from __future__ import annotations

import asyncio
import json

import pytest

from tavern.services import skills

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731

LORE = """---
name: lorebooks
title: Лорбуки
description: Making or changing a lorebook
---
Common rule: a lorebook is the rules of a world.
## Plan
Ask about the vibe first.
## Write
Nest details under their parent.
## Check
- Every entry has its own keywords
  in several word forms.
- No entry is longer than 1200 characters.
"""


@pytest.fixture
def builtin(tmp_path, monkeypatch):
    folder = tmp_path / "skills"
    folder.mkdir()
    (folder / "lorebooks.md").write_text(LORE, encoding="utf-8")
    monkeypatch.setattr(skills, "BUILTIN_DIR", folder)
    return folder


def test_parts_and_checks_of_a_skill():
    _, body = skills.parse(LORE)
    skill = {"body": body}
    assert skills.part(skill, "planner") == "Common rule: a lorebook is the rules of a world.\nAsk about the vibe first."
    assert skills.part(skill, "writer").endswith("Nest details under their parent.")
    assert "Ask about" not in skills.part(skill, "writer") and "Every entry" not in skills.part(skill, "writer")
    assert skills.checks(skill) == ["Every entry has its own keywords in several word forms.",
                                    "No entry is longer than 1200 characters."]
    assert skills.part({"body": "Just text."}, "writer") == "Just text." and skills.checks({"body": "x"}) == []


async def test_the_tab_lists_copies_switches_and_protects_built_ins(client, ctx, builtin):
    listed = (await client.get("/api/skills")).json()["skills"]
    lore = next(s for s in listed if s["id"] == "builtin:lorebooks")
    assert lore["title"] == "Лорбуки" and lore["used"] and lore["readonly"] and "body" not in lore
    dev = [s for s in listed if s["source"] == "dev"]
    assert dev and all(s["audience"] == "programmer" and not s["used"] for s in dev)
    assert (await client.get("/api/skills/builtin:lorebooks")).json()["body"].startswith("Common rule")

    assert (await client.put("/api/skills/builtin:lorebooks", json={"body": "x"})).status_code == 409
    assert (await client.delete("/api/skills/builtin:lorebooks")).status_code == 409

    # A copy replaces the built-in for the assistant; switching the copy off brings the built-in back.
    copy = (await client.post("/api/skills", json={"from": "builtin:lorebooks"})).json()
    assert copy["name"] == "lorebooks" and copy["based_on"] == "lorebooks" and copy["title"] == "Лорбуки (моя)"
    await client.put(f"/api/skills/{copy['id']}", json={"body": "My own rules."})
    assert [s["source"] for s in skills.for_assistant(ctx)] == ["own"]
    assert skills.find(ctx, "lorebooks")["body"] == "My own rules."
    original = (await client.get("/api/skills/builtin:lorebooks")).json()
    assert original["replaced"] and not original["used"]
    await client.put(f"/api/skills/{copy['id']}", json={"enabled": False})
    assert skills.find(ctx, "lorebooks")["source"] == "builtin"

    # Built-in ones switch off by a setting; own ones are made, renamed and deleted.
    await client.put("/api/skills/builtin:lorebooks", json={"enabled": False})
    assert skills.find(ctx, "lorebooks") is None and skills.catalog(ctx) == ""
    made = await client.post("/api/skills", json={"name": "Плохое имя"})
    assert made.status_code == 400
    made = (await client.post("/api/skills", json={"name": "horror-presets", "description": "Presets for horror",
                                                   "body": "Use dread."})).json()
    assert skills.catalog(ctx) == "- horror-presets: Presets for horror"
    assert (await client.post("/api/skills", json={"name": "horror-presets"})).status_code == 409
    assert (await client.delete(f"/api/skills/{made['id']}")).status_code == 200
    assert (await client.delete(f"/api/skills/{copy['id']}")).status_code == 200


async def setup(client) -> None:
    conn = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "fake-model"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})


async def wait(client, ctx, conv: str, response) -> dict:
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)
    return (await client.get(f"/api/assistant/chats/{conv}")).json()


def sent(fake_llm, index: int) -> str:
    return "\n".join(m["content"] for m in fake_llm.requests[index]["messages"])


async def test_big_work_takes_a_skill_and_each_role_reads_its_part(client, ctx, fake_llm, builtin):
    await setup(client)
    book = (await client.post("/api/lorebooks", json={"name": "Мир"})).json()
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    fake_llm.queue(
        J(say="", actions=[], big=True),
        J(skill="lorebooks"),
        J(say="Вопрос:", questions=[{"text": "Вайб?", "options": ["Мрачный", "Светлый"], "default": 0}]),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "Сделай лорбук"}))
    assert "- lorebooks: Making or changing a lorebook" in sent(fake_llm, 1)  # the small role chose
    line = next(m for m in state["messages"] if m["data"].get("kind") == "skill")
    assert line["text"] == "Инструкция: Лорбуки" and line["data"]["skill_id"] == "builtin:lorebooks"
    asked = sent(fake_llm, 2)
    assert "SKILL «lorebooks»" in asked and "Ask about the vibe first." in asked and "Nest details" not in asked
    assert state["task"]["skill"] == "lorebooks"

    fake_llm.queue(J(say="План", goal="g", decisions=["d"], items=[{"title": "Основа"}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}}))
    assert "Ask about the vibe first." in sent(fake_llm, 3)

    fake_llm.queue(
        J(say="Пишу", actions=[{"tool": "create_bubble", "args": {"owner_id": book["id"], "title": "Закон",
                                                                   "content": "Кровь платит."}}]),
        J(say="Готово", actions=[]),
        J(say="", actions=[], checks=[{"n": n, "ok": True, "evidence": "x"} for n in range(1, 9)]),
        J(say="Сделано."),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    assert state["task"]["phase"] == "done" and state["task"]["open_issues"] == []
    writer = sent(fake_llm, 4)
    assert "Nest details under their parent." in writer and "Ask about the vibe" not in writer
    critic = sent(fake_llm, 6)
    assert "7. Every entry has its own keywords in several word forms." in critic
    assert "8. No entry is longer than 1200 characters." in critic


async def test_small_requests_read_a_skill_with_a_line_and_cannot_change_skills(client, ctx, fake_llm, builtin):
    await setup(client)
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    fake_llm.queue(
        J(say="Читаю", actions=[{"tool": "read_skill", "args": {"name": "lorebooks"}},
                                 {"tool": "app_call", "args": {"method": "PUT", "path": "/skills/builtin:lorebooks",
                                                                "body": {"enabled": False}}}]),
        J(say="Лорбук — это правила мира.", actions=[]),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "Как устроен хороший лорбук?"}))
    assert "Skills (instructions for kinds of work" in sent(fake_llm, 0)
    assert "read_skill" in sent(fake_llm, 0)
    results = sent(fake_llm, 1)
    assert "Nest details under their parent." in results and "never changes its own instructions" in results
    assert any(m["text"] == "Инструкция: Лорбуки" for m in state["messages"])
    assert skills.find(ctx, "lorebooks") is not None  # still on


async def test_a_skill_with_more_questions_asks_a_second_round_before_the_plan(client, ctx, fake_llm, tmp_path,
                                                                                monkeypatch):
    # Stage 10д: characters need up to 10 questions, asked in two rounds; the second round reads the
    # first answers and the skill's "More questions" part; the language answer is kept.
    folder = tmp_path / "skills2"
    folder.mkdir()
    (folder / "characters.md").write_text(
        "---\nname: characters\ndescription: Making a character\n---\nVoice first.\n## Plan\nAsk who they are."
        "\n## More questions\nAsk about the armour.\n## Write\nWrite the card.\n", encoding="utf-8")
    monkeypatch.setattr(skills, "BUILTIN_DIR", folder)
    _, body = skills.parse((folder / "characters.md").read_text(encoding="utf-8"))
    assert skills.asks_more({"body": body}) and not skills.asks_more({"body": LORE})
    assert skills.part({"body": body}, "more") == "Voice first.\nAsk about the armour."
    assert "armour" not in skills.part({"body": body}, "planner")

    await setup(client)
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    fake_llm.queue(J(say="", actions=[], big=True), J(skill="characters"),
                   J(say="Первые:", questions=[{"text": "Кто он?", "options": ["Наёмник", "Жрица"], "default": 0}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "Сделай персонажа"}))
    assert [q["id"] for q in state["task"]["questions"]] == ["q1", "language"]

    fake_llm.queue(J(say="Ещё:", questions=[{"text": "Какая броня?", "options": ["Мат", "Молчание"], "default": 1}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers",
                                                            json={"answers": {"q1": "Жрица", "language": "English"}}))
    task = state["task"]
    assert task["phase"] == "questions" and [q["id"] for q in task["questions"]] == ["q1"]  # no language again
    assert task["earlier"] == [{"text": "Кто он?", "answer": "Жрица"}] and task["language"] == "English"
    second = sent(fake_llm, 3)
    assert "SECOND ROUND" in second and "Ask about the armour." in second and "Кто он? → Жрица" in second
    assert state["messages"][-1]["data"]["kind"] == "questions"

    fake_llm.queue(J(say="План", goal="g", decisions=["d"], items=[{"title": "Карточка"}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers",
                                                            json={"answers": {}}))
    assert state["task"]["phase"] == "plan" and state["task"]["language"] == "English"
    planned = sent(fake_llm, 4)
    assert "Кто он? → Жрица" in planned and "Какая броня? → Молчание" in planned and "SECOND ROUND" not in planned


async def test_questions_see_the_named_card_and_the_language_the_request_asks_for(client, ctx, fake_llm):
    # Stage 10д: "improve the card of Lydia, keep it in English" — the planner has no tools, so Tavern
    # hands it Lydia's card; the language question defaults to the language the request names.
    from tavern.services import assistant_engine

    assert assistant_engine.language_question("Улучши Лидию, язык оставь английский")["options"][0] == "English"
    assert assistant_engine.language_question("Make her, but in Russian")["options"][0] == "Русский"
    assert assistant_engine.language_question("Сделай ведьму")["options"][0] == "Русский"

    await setup(client)
    await client.post("/api/characters", json={"name": "Lydia Neraia", "description": "Airship captain, shy."})
    conv = (await client.post("/api/assistant/chats")).json()["id"]
    fake_llm.queue(J(say="", actions=[], big=True),
                   J(say="Вопрос:", questions=[{"text": "Форма?", "options": ["Обычная", "Досье"], "default": 0}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                    json={"text": "Улучши карточку Lydia Neraia"}))
    asked = sent(fake_llm, 1)
    assert "EXISTING CHARACTER «Lydia Neraia»" in asked and "Airship captain, shy." in asked
    task = (await client.get(f"/api/assistant/chats/{conv}")).json()["task"]
    assert "Airship captain, shy." in task["before"]  # kept as it was before the work
    assert "THE CARDS AS THEY WERE BEFORE" in assistant_engine.pinned(task)
    assert "THE CARDS AS THEY WERE" not in assistant_engine.pinned({"request": "x", "before": ""})
