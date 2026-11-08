"""Stage 4б over HTTP: the block tree of a character, finding blocks in a chat, vectors, ST lorebooks."""

from __future__ import annotations

from tests.conftest import settle_vectors

import asyncio
import base64

from tavern.formats.cards import decode_png_card


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    return ann, chat_id


async def add(client, character_id, **fields):
    response = await client.post(f"/api/characters/{character_id}/blocks", json=fields)
    assert response.status_code == 201, response.text
    return response.json()


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


def system_text(fake_llm):
    return "\n".join(m["content"] for m in fake_llm.requests[-1]["messages"])


async def test_blocks_are_sent_always_by_topic_and_stay_a_few_replies(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    look = await add(client, ann["id"], title="Внешность", content="Высокая.", mode="always")
    await add(client, ann["id"], title="Волосы", content="Рыжие до пояса.", parent_id=look["id"],
              keywords=["коса"], sticky=1)
    await add(client, ann["id"], title="Тайна", content="Ведьма.", mode="off")

    await send(client, ctx, chat_id, "Привет")
    first = system_text(fake_llm)
    assert "[Ann › Внешность]\nВысокая." in first and "Рыжие" not in first and "Ведьма" not in first

    await send(client, ctx, chat_id, "Я трогаю её косу")
    assert "[Ann › Внешность › Волосы]\nРыжие до пояса." in system_text(fake_llm)

    # scan looks at the last 3 messages, so talk about something else a few times
    for text in ("погода", "дорога", "ужин"):
        await send(client, ctx, chat_id, text)
    assert "Рыжие" not in system_text(fake_llm)

    reply = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    saw = (await client.get(f"/api/messages/{reply['id']}/prompt")).json()
    assert any(e["source"] == f"block:{look['id']}" and e["kind"] == "block" for e in saw["entries"])


async def test_block_edits_are_checked_and_announced(client, ctx):
    ann, _ = await setup(client)
    seen = []
    ctx.events.on("character.blocks_changed", lambda **kw: seen.append(kw["character_id"]))
    parent = await add(client, ann["id"], title="A")
    child = await add(client, ann["id"], title="B", parent_id=parent["id"])
    loop = await client.put(f"/api/blocks/{parent['id']}", json={"parent_id": child["id"]})
    assert loop.json()["error"]["code"] == "bad_block"
    bad = await client.post(f"/api/characters/{ann['id']}/blocks", json={"title": "x", "mode": "blue"})
    assert bad.json()["error"]["code"] == "bad_block"
    moved = await client.put(f"/api/blocks/{child['id']}", json={"layout": {"x": 10, "y": 20, "display": "nested"}})
    assert moved.json()["layout"] == {"x": 10.0, "y": 20.0, "display": "nested"}
    many = await client.put(f"/api/characters/{ann['id']}/blocks/layout",
                            json={"items": [{"id": parent["id"], "x": 1, "y": 2}, {"id": child["id"], "x": 3, "y": 4}]})
    assert many.json() == {"saved": 2}
    child_now = next(b for b in (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["blocks"]
                     if b["id"] == child["id"])
    assert child_now["layout"] == {"x": 3.0, "y": 4.0, "display": "nested"}  # display kept
    await client.put(f"/api/characters/{ann['id']}/blocks/layout", json={"items": [{"id": child["id"], "auto": True}]})
    child_now = next(b for b in (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["blocks"]
                     if b["id"] == child["id"])
    assert child_now["layout"] == {"display": "nested"}  # back to automatic placement
    await client.delete(f"/api/blocks/{parent['id']}")
    assert (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["blocks"] == []  # child went too
    assert seen == [ann["id"]] * 6


async def test_meaning_search_uses_only_block_text(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await add(client, ann["id"], title="Детство", content="Росла у реки рыбаком отца", keywords=["zzz"])
    nothing = await client.post(f"/api/characters/{ann['id']}/vectorize")
    assert nothing.json()["error"]["code"] == "no_embedding_connection"

    conn = (await client.get("/api/connections")).json()["connections"][0]
    await client.put("/api/settings", json={"embedding_connection": conn["id"],
                                            "retrieval": {"threshold": 0.3}})
    made = await client.post(f"/api/characters/{ann['id']}/vectorize")
    assert made.json() == {"made": 0, "started": True, "todo": 1}
    await settle_vectors()
    assert fake_llm.embedding_requests[-1]["input"] == ["Ann › Детство\nРосла у реки рыбаком отца"]
    again = await client.post(f"/api/characters/{ann['id']}/vectorize")
    assert again.json() == {"made": 0, "started": False, "todo": 0}  # unchanged blocks are not sent again

    await send(client, ctx, chat_id, "расскажи как ты росла у реки")
    assert "Росла у реки" in system_text(fake_llm)
    assert (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["vectorized"]


async def test_keywords_suggested_by_the_model(client, ctx, fake_llm):
    ann, _ = await setup(client)
    await client.put("/api/settings", json={"active_connection":
                                            (await client.get("/api/connections")).json()["connections"][0]["id"]})
    block = await add(client, ann["id"], title="Волосы", content="Рыжие")
    fake_llm.queue('["волосы", "коса", "hair"]')
    result = await client.post(f"/api/blocks/{block['id']}/suggest-keywords")
    assert result.json() == {"keywords": ["коса", "hair"]}  # the title already counts as a keyword


async def test_suggesting_again_gives_only_new_words(client, ctx, fake_llm):
    ann, _ = await setup(client)
    await client.put("/api/settings", json={"active_connection":
                                            (await client.get("/api/connections")).json()["connections"][0]["id"]})
    block = await add(client, ann["id"], title="Волосы", content="Рыжие", keywords=["коса"])
    # the model repeats itself: kept words and earlier suggestions (any ending) are dropped
    fake_llm.queue('["Косу", "рыжие", "локоны", "hair", "локоны"]')
    result = await client.post(f"/api/blocks/{block['id']}/suggest-keywords", json={"avoid": ["рыжий", "Hair"]})
    assert result.json() == {"keywords": ["локоны"]}
    asked = fake_llm.requests[-1]["messages"][-1]["content"]
    assert "коса" in asked and "рыжий" in asked  # the model is told what it must not repeat
    bad = await client.post(f"/api/blocks/{block['id']}/suggest-keywords", json={"avoid": "коса"})
    assert bad.status_code == 400


async def test_order_number_among_neighbours(client):
    ann, _ = await setup(client)
    a = await add(client, ann["id"], title="A")
    await add(client, ann["id"], title="B")
    c = await add(client, ann["id"], title="C")
    free = await add(client, ann["id"], title="Free")
    await client.put(f"/api/blocks/{free['id']}", json={"detached": True})
    inner = await add(client, ann["id"], title="Inner", parent_id=a["id"])

    async def order(parent=None):
        blocks = (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["blocks"]
        row = [x for x in blocks if x["parent_id"] == parent and not x["detached"]]
        return [x["title"] for x in sorted(row, key=lambda x: x["position"])]

    moved = await client.put(f"/api/blocks/{c['id']}/order", json={"place": 1})
    assert moved.status_code == 200, moved.text
    assert await order() == ["C", "A", "B"]  # one number per bubble: the others moved down
    await client.put(f"/api/blocks/{c['id']}/order", json={"place": 99})
    assert await order() == ["A", "B", "C"]  # past the end = last
    await client.put(f"/api/blocks/{inner['id']}/order", json={"place": 1})
    assert await order(a["id"]) == ["Inner"]  # a sub-bubble counts inside its own group
    assert (await client.put(f"/api/blocks/{c['id']}/order", json={"place": 0})).json()["error"]["code"] == "bad_block"
    assert (await client.put(f"/api/blocks/{free['id']}/order", json={"place": 1})).json()["error"]["code"] == "bad_block"


async def test_st_card_lorebook_becomes_blocks_and_comes_back(client, ctx):
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "Mira", "description": "Guard.", "character_book": {"name": "Mira book", "entries": [
            {"keys": ["gate"], "content": "The north gate.", "comment": "Gate", "constant": False, "enabled": True,
             "insertion_order": 50, "extensions": {"position": 4, "depth": 2, "role": 1}},
            {"keys": ["x"], "content": "Always here.", "comment": "Rules", "constant": True, "enabled": True},
            {"keys": ["v"], "content": "Vector one.", "comment": "Vec", "enabled": True,
             "extensions": {"vectorized": True}},
            {"keys": ["off"], "content": "Hidden.", "comment": "Off", "enabled": False},
        ]}}}
    mira = (await client.post("/api/characters", json=card)).json()
    assert mira["data"]["character_book"] == {"name": "Mira book"}  # entries moved into blocks
    blocks = {b["title"]: b for b in (await client.get(f"/api/characters/{mira['id']}/blocks")).json()["blocks"]}
    assert {t: b["mode"] for t, b in blocks.items()} == {"Gate": "search", "Rules": "always", "Vec": "search",
                                                       "Off": "off"}
    assert (blocks["Gate"]["depth"], blocks["Gate"]["role"], blocks["Gate"]["keywords"]) == (2, "user", ["gate"])

    await client.put(f"/api/blocks/{blocks['Gate']['id']}", json={"content": "The north gate, rebuilt."})
    exported = (await client.get(f"/api/characters/{mira['id']}/export.json")).json()
    entries = {e["comment"]: e for e in exported["data"]["character_book"]["entries"]}
    assert entries["Gate"]["content"] == "The north gate, rebuilt." and entries["Gate"]["insertion_order"] == 50
    assert entries["Rules"]["constant"] is True and entries["Off"]["enabled"] is False
    assert exported["data"]["character_book"]["name"] == "Mira book"

    png = await client.get(f"/api/characters/{mira['id']}/export.png")
    _, data = decode_png_card(png.content)
    assert any(e["content"] == "The north gate, rebuilt." for e in data["character_book"]["entries"])


async def test_chat_file_is_named_after_the_chat(client):
    ann, chat_id = await setup(client)
    await client.put(f"/api/chats/{chat_id}", json={"title": "Аня: у реки / вечер"})
    exported = await client.get(f"/api/chats/{chat_id}/export.jsonl")
    disposition = exported.headers["content-disposition"]
    assert chat_id not in disposition
    assert "filename*=UTF-8''" in disposition and "%D0%90%D0%BD%D1%8F" in disposition  # "Аня"
    assert "/" not in disposition.split("filename*=", 1)[1].replace("%2F", "")


async def test_png_export_keeps_the_picture_and_writes_the_current_card(client):
    from tavern.formats.cards import card_to_png

    original = card_to_png({"name": "Pic", "description": "old"})
    created = (await client.post("/api/characters/import/png", content=original,
                                 headers={"Content-Type": "image/png"})).json()
    card = (await client.get(f"/api/characters/{created['id']}")).json()["data"]
    await client.put(f"/api/characters/{created['id']}", json={**card, "description": "new"})
    exported = (await client.get(f"/api/characters/{created['id']}/export.png")).content
    _, data = decode_png_card(exported)
    assert data["description"] == "new"
    assert exported.count(b"tEXt") == original.count(b"tEXt")  # the old card chunk was replaced
    assert base64.b64encode(exported)  # still a valid file body


async def test_free_bubbles_links_and_chance_over_http(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    seen = []
    ctx.events.on("character.blocks_changed", lambda **kw: seen.append(kw["character_id"]))
    hands = await add(client, ann["id"], title="Руки", content="Тонкие.")
    fingers1 = await add(client, ann["id"], title="Пальцы", content="Кольцо на безымянном.",
                         parent_id=hands["id"], keywords=["кольцо"])
    legs = await add(client, ann["id"], title="Ноги", content="Длинные.")
    fingers2 = await add(client, ann["id"], title="Пальцы", content="Пальцы ног босые.", parent_id=legs["id"])
    scar = await add(client, ann["id"], title="Шрам", content="Шрам на щеке.", mode="always")

    linked = await client.post(f"/api/characters/{ann['id']}/links", json={"a": fingers2["id"], "b": fingers1["id"]})
    assert linked.status_code == 201
    same = await client.post(f"/api/characters/{ann['id']}/links", json={"a": fingers1["id"], "b": fingers1["id"]})
    assert same.json()["error"]["code"] == "bad_block"
    listed = (await client.get(f"/api/characters/{ann['id']}/blocks")).json()
    assert listed["links"] == [sorted([fingers1["id"], fingers2["id"]])]

    freed = await client.put(f"/api/blocks/{scar['id']}", json={"parent_id": None, "detached": True})
    assert freed.json()["detached"] is True
    await send(client, ctx, chat_id, "Я надеваю ей кольцо")
    text = system_text(fake_llm)
    assert "Пальцы ног босые." in text and "Длинные." not in text and "Шрам" not in text

    joined = await client.put(f"/api/blocks/{scar['id']}", json={"parent_id": hands["id"]})
    assert joined.json()["detached"] is False  # joining a bubble makes it active again

    bad = await client.put(f"/api/characters/{ann['id']}/blocks/chance", json={"chance": 150})
    assert bad.json()["error"]["code"] == "bad_block"
    some = await client.put(f"/api/characters/{ann['id']}/blocks/chance", json={"chance": 40, "under": legs["id"]})
    assert some.json() == {"changed": 1}  # only the sub-bubbles of "Ноги"
    chances = {b["id"]: b["chance"] for b in (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["blocks"]}
    assert chances[fingers2["id"]] == 40 and chances[legs["id"]] == 100 and chances[fingers1["id"]] == 100
    every = await client.put(f"/api/characters/{ann['id']}/blocks/chance", json={"chance": 70})
    assert every.json() == {"changed": 5}

    await client.delete(f"/api/characters/{ann['id']}/links", params={"a": fingers1["id"], "b": fingers2["id"]})
    assert (await client.get(f"/api/characters/{ann['id']}/blocks")).json()["links"] == []
    assert len(seen) >= 6


async def test_st_entry_chance_comes_in_and_goes_back(client):
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "Lia", "description": "x", "character_book": {"entries": [
            {"keys": ["ring"], "content": "A ring.", "comment": "Ring", "enabled": True,
             "extensions": {"probability": 30, "useProbability": True}}]}}}
    lia = (await client.post("/api/characters", json=card)).json()
    ring = (await client.get(f"/api/characters/{lia['id']}/blocks")).json()["blocks"][0]
    assert ring["chance"] == 30
    await client.put(f"/api/blocks/{ring['id']}", json={"parent_id": None, "detached": True})
    entry = (await client.get(f"/api/characters/{lia['id']}/export.json")).json()["data"]["character_book"]["entries"][0]
    assert entry["extensions"]["probability"] == 30 and entry["extensions"]["useProbability"] is True
    assert entry["enabled"] is False  # a free bubble is not sent, so it goes out switched off


async def test_the_whole_tree_goes_out_with_the_card_and_comes_back(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "x"})).json()
    hands = await add(client, ann["id"], title="Руки", content="Тонкие.", mode="always", every=2)
    f1 = await add(client, ann["id"], title="Пальцы", content="Кольцо.", parent_id=hands["id"], keywords=["кольцо"])
    legs = await add(client, ann["id"], title="Ноги", content="Длинные.")
    f2 = await add(client, ann["id"], title="Пальцы", content="Босые.", parent_id=legs["id"], chance=40)
    await add(client, ann["id"], title="Шрам", content="На щеке.", detached=True)
    await client.post(f"/api/characters/{ann['id']}/links", json={"a": f1["id"], "b": f2["id"]})

    card = (await client.get(f"/api/characters/{ann['id']}/export.json")).json()
    assert card["data"]["extensions"]["tavern_tree"]["version"] == 1
    assert len(card["data"]["character_book"]["entries"]) == 5  # SillyTavern still gets a flat lorebook

    copy = (await client.post("/api/characters", json=card)).json()
    got = (await client.get(f"/api/characters/{copy['id']}/blocks")).json()
    by_title = {}
    for b in got["blocks"]:
        by_title.setdefault(b["title"], []).append(b)
    ids = {b["id"]: b for b in got["blocks"]}
    fingers = {ids[b["parent_id"]]["title"]: b for b in by_title["Пальцы"]}
    assert fingers["Ноги"]["chance"] == 40 and fingers["Руки"]["keywords"] == ["кольцо"]
    assert by_title["Руки"][0]["every"] == 2 and by_title["Шрам"][0]["detached"] is True
    assert got["links"] == [sorted([fingers["Руки"]["id"], fingers["Ноги"]["id"]])]
    stored = (await client.get(f"/api/characters/{copy['id']}")).json()["data"]
    assert "tavern_tree" not in stored.get("extensions", {}) and "entries" not in stored.get("character_book", {})
