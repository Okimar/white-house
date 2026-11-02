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
    assert line["text"] == "📖 Инструкция: Лорбуки" and line["data"]["skill_id"] == "builtin:lorebooks"
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
    assert any(m["text"] == "📖 Инструкция: Лорбуки" for m in state["messages"])
    assert skills.find(ctx, "lorebooks") is not None  # still on
