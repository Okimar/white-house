"""Regression tests: free bubbles rejoin the card, vectors follow edited text, ST second keys survive,
and requests outside the chat count as spending."""

from __future__ import annotations

from tests.conftest import settle_vectors

import asyncio


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    conn = (await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})).json()
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    return ann, conn, chat_id


async def add(client, owner, **fields):
    response = await client.post(f"/api/characters/{owner}/blocks", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


async def test_a_free_bubble_dropped_on_the_card_joins_it_and_reaches_the_model(client, ctx, fake_llm):
    ann, _, chat_id = await setup(client)
    bubble = await add(client, ann["id"], title="Коса", content="ЗАПЛЕТЕНА", mode="always")
    await client.put(f"/api/blocks/{bubble['id']}", json={"parent_id": None, "detached": True})
    await send(client, ctx, chat_id, "привет")
    assert "ЗАПЛЕТЕНА" not in str(fake_llm.requests[-1]["messages"])

    # What the map sends when a bubble is dropped on the card: no word about "detached".
    joined = await client.put(f"/api/blocks/{bubble['id']}", json={"parent_id": None, "position": 0})
    assert joined.json()["detached"] is False
    await send(client, ctx, chat_id, "ещё раз")
    assert "ЗАПЛЕТЕНА" in str(fake_llm.requests[-1]["messages"])


async def test_setting_a_bubble_free_still_works(client):
    ann, _, _ = await setup(client)
    bubble = await add(client, ann["id"], title="Коса")
    freed = await client.put(f"/api/blocks/{bubble['id']}", json={"parent_id": None, "detached": True})
    assert freed.json()["detached"] is True


async def test_an_edited_bubble_gets_a_new_vector_before_meaning_search_uses_it(client, ctx, fake_llm):
    ann, conn, chat_id = await setup(client)
    await client.put("/api/settings", json={"embedding_connection": conn["id"], "retrieval": {"threshold": 0.3}})
    bubble = await add(client, ann["id"], title="Детство", content="Росла у реки", keywords=["zzz"])
    await add(client, ann["id"], title="Другое", content="Не менялось", keywords=["yyy"])
    await client.post(f"/api/characters/{ann['id']}/vectorize")
    await settle_vectors()
    other = await add(client, ann["id"], title="Без вектора", content="Никто не векторизовал", keywords=["xxx"])

    await client.put(f"/api/blocks/{bubble['id']}", json={"content": "Выросла в горах"})
    fake_llm.embedding_requests.clear()
    await send(client, ctx, chat_id, "расскажи про горы")
    sent = [text for request in fake_llm.embedding_requests for text in request["input"]]
    assert "Ann › Детство\nВыросла в горах" in sent
    assert not any("Не менялось" in text for text in sent)  # unchanged ones are not paid for again
    assert not any(other["title"] in text for text in sent)  # the user did not ask to vectorize this one

    fake_llm.embedding_requests.clear()
    await send(client, ctx, chat_id, "ещё про горы")
    assert not any("Выросла" in text for request in fake_llm.embedding_requests for text in request["input"])


async def test_st_card_second_keys_stay_second_keys_both_ways(client):
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "Mira", "character_book": {"entries": [
            {"keys": ["gate"], "secondary_keys": ["night"], "selective": True, "comment": "Night gate",
             "content": "Locked.", "enabled": True, "extensions": {"selectiveLogic": 3}},
            {"keys": ["well"], "secondary_keys": ["ignored"], "selective": False, "comment": "Well",
             "content": "Deep.", "enabled": True},
        ]}}}
    mira = (await client.post("/api/characters", json=card)).json()
    blocks = {b["title"]: b for b in (await client.get(f"/api/characters/{mira['id']}/blocks")).json()["blocks"]}
    night = blocks["Night gate"]
    assert night["keywords"] == ["gate"]
    assert night["rules"]["keys2"] == ["night"] and night["rules"]["logic"] == "and_all"
    assert blocks["Well"]["keywords"] == ["well"] and "keys2" not in blocks["Well"]["rules"]

    entries = {e["comment"]: e for e in
               (await client.get(f"/api/characters/{mira['id']}/export.json")).json()["data"]["character_book"]["entries"]}
    assert entries["Night gate"]["secondary_keys"] == ["night"] and entries["Night gate"]["selective"] is True
    assert entries["Night gate"]["extensions"]["selectiveLogic"] == 3
    assert entries["Well"]["selective"] is False


async def test_requests_outside_the_chat_count_as_spending(client, ctx, fake_llm):
    ann, conn, chat_id = await setup(client)

    await client.put("/api/settings", json={"embedding_connection": conn["id"], "image_connection": conn["id"]})
    await add(client, ann["id"], title="Детство", content="Росла у реки", keywords=["zzz"])
    await client.post(f"/api/characters/{ann['id']}/vectorize")
    await settle_vectors()
    await client.post(f"/api/connections/{conn['id']}/probe", json={"model": "m"})
    await client.post("/api/assets/generate", json={"prompt": "море"})
    await send(client, ctx, chat_id, "росла у реки")

    purposes = {row["purpose"] for row in ctx.db.fetchall("SELECT DISTINCT purpose FROM spending")}
    assert {"vectors", "probe", "picture", "chat"} <= purposes
    vectors = ctx.db.fetchone("SELECT prompt_tokens, estimated FROM spending WHERE purpose = 'vectors'")
    assert vectors["prompt_tokens"] > 0 and vectors["estimated"]
