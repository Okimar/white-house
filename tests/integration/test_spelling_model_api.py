"""Built-in plugin «Орфография» with the model as the checker, through the HTTP API: the settings it shows, a
refusal without the model permission (its reason reaches the page), the owner's «Привит я бола» found by the model
of the chosen connection, no second request for the same text, and the spending counted as the plugin's."""

from __future__ import annotations

import json
import sys

from tavern.storage.repos.spending import today


async def connect(client):
    conn = (await client.post("/api/connections", json={"name": "f", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return conn


async def check(client, text):
    return await client.post("/api/plugins/spelling/actions/check", json={"text": text, "chat_id": ""})


def answer(*items) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


async def test_the_model_engine_and_its_connection_are_in_the_settings(client):
    plugin = next(p for p in (await client.get("/api/plugins")).json()["plugins"] if p["id"] == "spelling")
    fields = {f["key"]: f for f in plugin["settings"]}
    assert ["model", "Модель (подключение и модель ниже)"] in fields["engine"]["choices"]
    assert fields["connection"]["type"] == "connection" and fields["model"]["type"] == "model"
    assert fields["model"]["connection_key"] == "connection"
    assert plugin["values"]["model"] == "" and plugin["values"]["pause"] == 700
    assert "models" in plugin["uses"]


async def test_without_the_permission_the_page_gets_the_reason(client, fake_llm):
    await connect(client)
    await client.post("/api/plugins/spelling/enable", json={})
    await client.put("/api/plugins/spelling/settings", json={"engine": "model"})
    response = await check(client, "Привит я бола")
    assert response.status_code == 409
    assert "не разрешено обращаться к моделям" in response.json()["error"]["message"]
    assert fake_llm.requests == []


async def test_the_owners_line_is_checked_by_the_chosen_model_once(client, ctx, fake_llm):
    conn = await connect(client)
    await client.post("/api/plugins/spelling/enable", json={})
    sys.modules["tavern_plugin_spelling"]._model_cache.clear()
    await client.put("/api/plugins/spelling/access", json={"models": True})
    saved = await client.put("/api/plugins/spelling/settings",
                             json={"engine": "model", "connection": conn["id"], "model": "flash-lite"})
    assert saved.status_code == 200, saved.text
    fake_llm.queue(answer(
        {"n": 1, "start": 0, "end": 6, "wrong": "Привит", "variants": ["Привет,"], "kind": "spelling"},
        {"n": 1, "start": 20, "end": 24, "wrong": "бола", "variants": ["была"], "kind": "grammar"},  # off: found anyway
        {"n": 1, "start": 0, "end": 3, "wrong": "дом", "variants": ["дома"], "kind": "spelling"},     # made up: dropped
    ))
    result = (await check(client, "Привит я бола")).json()["result"]
    assert [(i["word"], i["start"], i["end"], i["suggestions"]) for i in result["issues"]] == [
        ("Привит", 0, 6, ["Привет,"]), ("бола", 9, 13, ["была"])]
    assert len(fake_llm.requests) == 1 and fake_llm.requests[0]["model"] == "flash-lite"
    sent = json.dumps(fake_llm.requests[0]["messages"], ensure_ascii=False)
    assert "1: Привит я бола" in sent

    again = (await check(client, "Привит я бола")).json()["result"]
    assert again["issues"] == result["issues"] and len(fake_llm.requests) == 1
    assert ctx.spending.purpose("plugin:spelling", month=today()[:7])["requests"] == 1


async def test_a_failing_model_is_a_plain_error(client, fake_llm):
    await connect(client)
    await client.post("/api/plugins/spelling/enable", json={})
    sys.modules["tavern_plugin_spelling"]._model_cache.clear()
    await client.put("/api/plugins/spelling/access", json={"models": True})
    await client.put("/api/plugins/spelling/settings", json={"engine": "model"})
    fake_llm.fail_next(500, "boom")
    response = await check(client, "Совсем новая строка")
    assert response.status_code >= 400 and response.json()["error"]["message"]
