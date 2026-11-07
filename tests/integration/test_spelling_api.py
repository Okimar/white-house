"""Built-in plugin «Орфография» through the HTTP API: off until enabled, the check action with a fake
service, the person's name not flagged, the dictionary kept in the plugin's settings, a dead service."""

from __future__ import annotations

import sys

import httpx


def fake_service(words: dict[str, list[str]]):
    """A LanguageTool-like server that flags every word in `words`."""
    def handler(request: httpx.Request) -> httpx.Response:
        text = httpx.QueryParams(request.content.decode())["text"]
        matches = []
        for word, suggestions in words.items():
            at = text.find(word)
            if at >= 0:
                matches.append({"offset": at, "length": len(word), "replacements": [{"value": s} for s in suggestions],
                                "rule": {"category": {"id": "TYPOS"}}})
        return httpx.Response(200, json={"matches": matches})
    return httpx.MockTransport(handler)


async def check(client, text, chat_id=""):
    return await client.post("/api/plugins/spelling/actions/check", json={"text": text, "chat_id": chat_id})


async def test_it_is_off_until_enabled_and_works_after(client):
    plugin = next(p for p in (await client.get("/api/plugins")).json()["plugins"] if p["id"] == "spelling")
    assert plugin["builtin"] and not plugin["enabled"]
    assert plugin["values"]["autocorrect"] is False and plugin["values"]["engine"] == "languagetool"
    await client.post("/api/plugins/spelling/enable", json={})
    module = sys.modules["tavern_plugin_spelling"]
    module._cache.clear()
    module.transport = fake_service({"превет": ["привет"]})
    answer = (await check(client, "Это превет")).json()["result"]
    assert answer["issues"] == [{"start": 4, "end": 10, "word": "превет", "suggestions": ["привет"], "sure": True}]


async def test_the_persons_name_and_dictionary_words_are_left_alone(client):
    await client.post("/api/plugins/spelling/enable", json={})
    module = sys.modules["tavern_plugin_spelling"]
    module._cache.clear()
    module.transport = fake_service({"Гарольд": ["Гарольда"], "кролик": ["кролики"]})
    await client.put("/api/settings", json={"user_name": "Гарольд"})
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    shown = (await check(client, "Гарольд и кролик", chat["id"])).json()["result"]["issues"]
    assert [i["word"] for i in shown] == ["кролик"]

    added = await client.post("/api/plugins/spelling/actions/add_word", json={"word": "кролик"})
    assert added.json()["result"] == {"dictionary": "кролик"}
    plugin = next(p for p in (await client.get("/api/plugins")).json()["plugins"] if p["id"] == "spelling")
    assert plugin["values"]["dictionary"] == "кролик"
    module._cache.clear()
    assert (await check(client, "Гарольд и кролик", chat["id"])).json()["result"]["issues"] == []
    bad = await client.post("/api/plugins/spelling/actions/add_word", json={"word": "два слова"})
    assert bad.status_code == 502 and bad.json()["error"]["code"] == "spelling_failed"


async def test_a_dead_service_is_a_plain_error_and_changes_nothing(client):
    await client.post("/api/plugins/spelling/enable", json={})
    module = sys.modules["tavern_plugin_spelling"]
    module._cache.clear()
    module.transport = httpx.MockTransport(lambda r: httpx.Response(503))
    response = await check(client, "Это превет")
    assert response.status_code == 502 and response.json()["error"]["code"] == "spelling_failed"
    assert (await check(client, "")).json()["result"] == {"issues": []}
