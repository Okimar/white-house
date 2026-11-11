"""Built-in plugin «Орфография» with the model as the checker, through the HTTP API: the settings it shows, a
refusal without the model permission (its reason reaches the page), the owner's «Привит я бола» found by the model
of the chosen connection, no second request for the same text, and the spending counted as the plugin's."""

from __future__ import annotations

import json
import sys

import httpx

from tavern.storage.repos.spending import today


async def connect(client):
    conn = (await client.post("/api/connections", json={"name": "f", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return conn


async def check(client, text, part=None):
    body = {"text": text, "chat_id": ""} if part is None else {"text": text, "chat_id": "", "part": part}
    return await client.post("/api/plugins/spelling/actions/check", json=body)


def plugin_module():
    return sys.modules["tavern_plugin_spelling"]


def dictionary_service(words: dict[str, list[str]] | None = None, status: int = 200):
    """A LanguageTool-like server flagging `words` (they are real misspellings to it); `status` != 200: it is down.
    Remembers the texts it got."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        text = httpx.QueryParams(request.content.decode())["text"]
        seen.append(text)
        if status != 200:
            return httpx.Response(status)
        # offsets in UTF-16 units, as the real services count
        matches = [{"offset": len(text[:text.find(w)].encode("utf-16-le")) // 2, "length": len(w), "replacements": [{"value": s} for s in sug],
                    "rule": {"category": {"id": "TYPOS"}}} for w, sug in (words or {}).items() if w in text]
        return httpx.Response(200, json={"matches": matches})

    module = plugin_module()
    module._cache.clear()
    module._model_cache.clear()
    module.transport = httpx.MockTransport(handler)
    return seen


async def model_on(client, **extra):
    conn = await connect(client)
    await client.post("/api/plugins/spelling/enable", json={})
    await client.put("/api/plugins/spelling/access", json={"models": True})
    saved = await client.put("/api/plugins/spelling/settings", json={"engine": "model", **extra})
    assert saved.status_code == 200, saved.text
    return conn


def answer(*items) -> str:
    return json.dumps({"items": list(items)}, ensure_ascii=False)


async def test_the_model_engine_and_its_connection_are_in_the_settings(client):
    plugin = next(p for p in (await client.get("/api/plugins")).json()["plugins"] if p["id"] == "spelling")
    fields = {f["key"]: f for f in plugin["settings"]}
    assert ["model", "Модель + обычная проверка"] in fields["engine"]["choices"]
    assert [c[0] for c in fields["base"]["choices"]] == ["languagetool", "yandex", "off"]
    assert plugin["values"]["base"] == "languagetool"
    assert fields["connection"]["type"] == "connection" and fields["model"]["type"] == "model"
    assert fields["model"]["connection_key"] == "connection"
    assert plugin["values"]["model"] == "" and plugin["values"]["pause"] == 700
    assert "models" in plugin["uses"]


async def test_without_the_permission_the_page_gets_the_reason(client, fake_llm):
    await connect(client)
    await client.post("/api/plugins/spelling/enable", json={})
    await client.put("/api/plugins/spelling/settings", json={"engine": "model"})
    dictionary_service()
    response = await check(client, "Привит я бола")
    assert response.status_code == 409
    assert "не разрешено обращаться к моделям" in response.json()["error"]["message"]
    assert fake_llm.requests == []


async def test_the_owners_line_is_checked_by_the_chosen_model_once(client, ctx, fake_llm):
    conn = await connect(client)
    await client.post("/api/plugins/spelling/enable", json={})
    await client.put("/api/plugins/spelling/access", json={"models": True})
    saved = await client.put("/api/plugins/spelling/settings",
                             json={"engine": "model", "connection": conn["id"], "model": "flash-lite"})
    assert saved.status_code == 200, saved.text
    dictionary_service()
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
    await client.put("/api/plugins/spelling/access", json={"models": True})
    await client.put("/api/plugins/spelling/settings", json={"engine": "model"})
    dictionary_service()
    fake_llm.fail_next(500, "boom")
    response = await check(client, "Совсем новая строка", "model")
    assert response.status_code == 502
    assert response.json()["error"]["message"].startswith("модель не ответила")


# -- both engines together (owner, 2026-11-11: «если включена проверка моделью, обычная проверка не проводится») --


async def test_the_dictionary_still_runs_with_the_model_and_the_owners_line_is_flagged(client, fake_llm):
    await model_on(client)
    seen = dictionary_service({"превет": ["привет"], "бола": ["болта"]})
    fake_llm.queue(answer(
        {"n": 1, "start": 0, "end": 6, "wrong": "Привит", "variants": ["Привет,"], "kind": "spelling"},
        {"n": 1, "start": 9, "end": 13, "wrong": "бола", "variants": ["была"], "kind": "grammar"},
    ))
    text = "Привит я бола и превет"
    fast = (await check(client, text, "dict")).json()["result"]["issues"]
    assert [(i["word"], i["suggestions"]) for i in fast] == [("бола", ["болта"]), ("превет", ["привет"])]
    assert fake_llm.requests == [] and len(seen) == 1   # the dictionary part never waits for the model

    by_model = (await check(client, text, "model")).json()["result"]["issues"]
    assert [(i["word"], i["source"]) for i in by_model] == [("Привит", "model"), ("бола", "model")]
    assert len(fake_llm.requests) == 1

    both = (await check(client, text)).json()["result"]["issues"]  # merged, the model's «была» wins on «бола»
    assert [(i["word"], i["suggestions"][0]) for i in both] == [("Привит", "Привет,"), ("бола", "была"),
                                                               ("превет", "привет")]
    assert len(fake_llm.requests) == 1  # the line came from memory


async def test_one_engine_down_does_not_stop_the_other(client, fake_llm):
    await model_on(client)
    dictionary_service(status=503)
    fake_llm.queue(answer({"n": 1, "start": 0, "end": 6, "wrong": "Привит", "variants": ["Привет"],
                           "kind": "spelling"}))
    down = await check(client, "Привит я", "dict")
    assert down.status_code == 502 and "503" in down.json()["error"]["message"]
    assert [i["word"] for i in (await check(client, "Привит я", "model")).json()["result"]["issues"]] == ["Привит"]

    dictionary_service({"превет": ["привет"]})
    await client.put("/api/plugins/spelling/access", json={"models": False})
    assert (await check(client, "Это превет", "model")).status_code == 409
    fast = (await check(client, "Это превет", "dict")).json()["result"]["issues"]
    assert [i["word"] for i in fast] == ["превет"]


async def test_the_model_alone_when_the_dictionary_is_switched_off(client, fake_llm):
    await model_on(client, base="off")
    seen = dictionary_service({"превет": ["привет"]})
    assert (await check(client, "Это превет", "dict")).json()["result"]["issues"] == []
    assert seen == []


async def test_positions_reach_the_page_in_utf16_units_after_an_emoji(client, fake_llm):
    await model_on(client)
    dictionary_service({"превет": ["привет"]})
    fake_llm.queue(answer({"n": 1, "start": 2, "end": 8, "wrong": "Привит", "variants": ["Привет"],
                           "kind": "spelling"}))
    text = "😊 Привит и превет"
    model = (await check(client, text, "model")).json()["result"]["issues"]
    fast = (await check(client, text, "dict")).json()["result"]["issues"]
    # JavaScript counts the emoji as two: «Привит» starts at 3, «превет» at 12 in the page's string
    assert (model[0]["start"], model[0]["end"]) == (3, 9) and (fast[0]["start"], fast[0]["end"]) == (12, 18)
