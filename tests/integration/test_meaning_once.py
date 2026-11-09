"""Meaning search of a reply: one embedding request for every character and book, one input per chat message,
a bubble scores by its best message, stale bubbles are not scored, nothing is re-embedded during a reply."""

from __future__ import annotations

from tests.conftest import settle_vectors

import asyncio
import json

import pytest

from tavern.devtools.fake_llm import _word_vector
from tavern.core.retrieval import cosine
from tavern.services import character_tree


async def setup(client, *, vectorize=True):
    conn = (await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"], "embedding_connection": conn["id"]})
    people = {}
    for name in ("Ann", "Bob", "Cy"):
        people[name] = (await client.post("/api/characters", json={"name": name, "description": f"{name} лучший"})).json()
        r = await client.post(f"/api/characters/{people[name]['id']}/blocks",
                              json={"title": f"Про {name}", "content": f"яблоко {name}", "keywords": ["qqq"], "mode": "search"})
        assert r.status_code == 201, r.text
    book = (await client.post("/api/lorebooks", json={"name": "Мир"})).json()
    for n in range(2):
        r = await client.post(f"/api/lorebooks/{book['id']}/blocks",
                              json={"title": f"Место{n}", "content": f"город{n}", "keywords": ["qqq"], "mode": "search"})
        assert r.status_code == 201, r.text
    await client.put("/api/lorebook-uses", json={"scope": "global", "target": None, "lorebook_ids": [book["id"]]})
    chat_id = (await client.post("/api/chats", json={"character_id": people["Ann"]["id"]})).json()["chat"]["id"]
    for name in ("Bob", "Cy"):
        r = await client.post(f"/api/chats/{chat_id}/members", json={"character_id": people[name]["id"]})
        assert r.status_code in (200, 201), r.text
    if vectorize:
        for person in people.values():
            await client.post(f"/api/characters/{person['id']}/vectorize")
        await client.post(f"/api/lorebooks/{book['id']}/vectorize")
        await settle_vectors()
    return conn, people, book, chat_id


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    body = (await client.get(f"/api/runs/{run_id}/events")).text
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


async def test_one_request_for_all_characters_and_books_with_a_text_per_message(client, ctx, fake_llm):
    _, _, _, chat_id = await setup(client)
    for text in ("раз", "два", "три"):
        await send(client, ctx, chat_id, text)
    fake_llm.embedding_requests.clear()
    events = await send(client, ctx, chat_id, "четыре")
    assert events[-1]["type"] == "done"
    assert len(fake_llm.embedding_requests) == 1
    messages = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    chat_texts = [m["content"] for m in messages[:-1] if m["role"] in ("user", "assistant")]
    assert fake_llm.embedding_requests[0]["input"] == chat_texts[-3:]
    assert len(chat_texts[-3:]) == 3


async def test_a_bubble_scores_by_its_best_message(client, ctx, fake_llm):
    _, people, _, _ = await setup(client)
    blocks = ctx.blocks.list(people["Ann"]["id"])
    texts = ["совсем другое слово", "яблоко Ann"]
    scores, error = await character_tree.meaning_search(ctx, blocks, texts)
    assert error is None
    block = blocks[0]
    own = _word_vector("Ann › Про Ann\nяблоко Ann")
    expected = max(cosine(_word_vector(t), own) for t in texts)
    assert scores[block["id"]] == pytest.approx(expected)
    assert expected > cosine(_word_vector(texts[0]), own)  # matches only the second message


async def test_a_stale_bubble_gets_no_score_and_is_not_re_embedded(client, ctx, fake_llm):
    _, people, _, _ = await setup(client)
    ann = people["Ann"]["id"]
    extra = (await client.post(f"/api/characters/{ann}/blocks",
                               json={"title": "Второй", "content": "груша", "keywords": ["qqq"], "mode": "search"})).json()
    await client.post(f"/api/characters/{ann}/vectorize")
    await settle_vectors()
    first = ctx.blocks.list(ann)[0]
    await client.put(f"/api/blocks/{first['id']}", json={"content": "изменено после векторов"})
    fake_llm.embedding_requests.clear()
    scores, _ = await character_tree.meaning_search(ctx, ctx.blocks.list(ann), ["груша"])
    assert extra["id"] in scores and first["id"] not in scores
    assert [r["input"] for r in fake_llm.embedding_requests] == [["груша"]]  # the chat only, no bubble text


async def _off_makes_no_request(client, ctx, fake_llm, patch):
    _, _, _, chat_id = await setup(client)
    await client.put("/api/settings", json={"retrieval": patch})
    fake_llm.embedding_requests.clear()
    events = await send(client, ctx, chat_id, "привет")
    assert events[-1]["type"] == "done"
    assert fake_llm.embedding_requests == []


async def test_meaning_off_makes_no_request(client, ctx, fake_llm):
    await _off_makes_no_request(client, ctx, fake_llm, {"meaning_on": False})


async def test_both_message_kinds_off_makes_no_request(client, ctx, fake_llm):
    await _off_makes_no_request(client, ctx, fake_llm, {"meaning_user": False, "meaning_characters": False})


async def test_no_current_vectors_makes_no_request(client, ctx, fake_llm):
    _, _, _, chat_id = await setup(client, vectorize=False)
    fake_llm.embedding_requests.clear()
    await send(client, ctx, chat_id, "привет")
    assert fake_llm.embedding_requests == []


async def test_only_wanted_kinds_of_messages_go(client, ctx, fake_llm):
    _, _, _, chat_id = await setup(client)
    await send(client, ctx, chat_id, "моё слово")
    await client.put("/api/settings", json={"retrieval": {"meaning_characters": False, "meaning_messages": 5}})
    fake_llm.embedding_requests.clear()
    await send(client, ctx, chat_id, "ещё моё")
    assert fake_llm.embedding_requests[0]["input"] == ["моё слово", "ещё моё"]


async def test_failure_keeps_the_reply_and_tells_the_page(client, ctx, fake_llm):
    _, _, _, chat_id = await setup(client)
    fake_llm.failures.append((404, "Not Found"))
    events = await send(client, ctx, chat_id, "привет")
    notice = next(e for e in events if e["type"] == "meaning_failed")
    assert notice["text"].startswith("Поиск по смыслу не сработал") and "Ответ без него" in notice["text"]
    assert events[-1]["type"] == "done"


async def test_meaning_settings_are_validated(client):
    ok = await client.put("/api/settings", json={"retrieval": {"meaning_messages": 20, "meaning_on": False}})
    assert ok.status_code == 200
    for bad in ({"meaning_messages": 0}, {"meaning_messages": 21}, {"meaning_messages": "3"},
                {"meaning_messages": True}, {"meaning_on": "yes"}, {"meaning_user": 1}, {"meaning_characters": None}):
        response = await client.put("/api/settings", json={"retrieval": bad})
        assert response.status_code == 400, bad


async def test_meaning_defaults(ctx):
    settings = character_tree.retrieval_settings(ctx)
    assert (settings["meaning_on"], settings["meaning_messages"], settings["meaning_user"],
            settings["meaning_characters"]) == (True, 3, True, True)
