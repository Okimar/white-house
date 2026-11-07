"""The live test stand (stage 10а) plays a scenario to the end and reports what was made, in full."""

from __future__ import annotations

import json

from tavern.devtools import assistant_bench as bench
import pytest
from tavern.services import assistant_engine as engine


@pytest.fixture(autouse=True)
def _no_mini_plan(monkeypatch):
    """These tests queue the model's answers one by one; the mini-plan call has its own tests."""
    monkeypatch.setattr(engine, "MINI_PLAN", False)

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731


async def test_a_scenario_is_played_through_and_the_report_has_the_whole_book(client, ctx, fake_llm):
    conn = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "fake-model"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    book = (await client.post("/api/lorebooks", json={"name": "Мир"})).json()
    fake_llm.queue(
        J(say="", actions=[], big=True),
        J(say="Вопрос:", questions=[{"text": "Тон?", "options": ["Мрачный", "Светлый"], "default": 0}]),
        J(say="План:", goal="Лорбук", decisions=["мрачно"], items=[{"title": "Основа", "detail": "правила"}]),
        J(say="Пишу", actions=[{"tool": "create_bubble", "args": {
            "owner_id": book["id"], "title": "Закон крови", "content": "Кровь платит за магию.", "mode": "always"}}]),
        J(say="Готово: «Закон крови»", actions=[]),
        J(say="", actions=[], checks=[{"n": n, "ok": True, "evidence": "x"} for n in range(1, 7)]),
        J(say="Сделан лорбук «Мир»."),
    )
    scenario = {"name": "t", "title": "Проба", "turns": [{"text": "Сделай лорбук", "answers": {}}],
                "look_for": "правила мира"}
    result = await bench.play(ctx, scenario)
    assert [t["step"] for t in result["timings"]] == ["«Сделай лорбук»", "ответы", "давай"]
    made = bench.products(ctx, result)
    assert [b["name"] for b in made["lorebooks"]] == ["Мир"]
    text = bench.report_md(scenario, {"source": "", "roles": {"planner": {"model": ""}}, "critic": True,
                                      "critic_rounds": 2}, result, made, 3.0)
    assert "**Вопросы:** Вопрос:" in text and "**Мрачный**" in text
    assert "### Закон крови — 🔵 всегда" in text and "Кровь платит за магию." in text
    assert "**Помощник (итог):** Сделан лорбук «Мир»." in text
    assert "**Что ищем:** правила мира" in text
