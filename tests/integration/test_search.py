"""Search across all chats (stage 11): plain with Russian endings, filters, paging, the assistant's own
conversations apart, the model's search that drops what it invented, and speed on a large database."""

from __future__ import annotations

import asyncio
import time

import pytest

from tavern.services import search


def make_chat(ctx, title, lines, character_id=None, parent=None):
    chat = ctx.chats.create_chat(character_id=character_id, title=title)
    parent_id = parent
    ids = []
    for role, text in lines:
        parent_id = ctx.chats.add_message(chat["id"], role=role, content=text, parent_id=parent_id)["id"]
        ids.append(parent_id)
    return chat["id"], ids


async def found(client, **params):
    response = await client.get("/api/search", params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_plain_finds_russian_forms_in_all_chats_and_marks_them(client, ctx):
    a, ids_a = make_chat(ctx, "Лес", [("user", "Я встретил эльфа у реки."), ("assistant", "Эльфы не любят людей.")])
    b, _ = make_chat(ctx, "Замок", [("user", "Король позвал эльфийку."), ("assistant", "Ничего про них.")])
    result = await found(client, q="эльф")
    assert {h["chat_id"] for h in result["hits"]} == {a, b}
    assert len(result["hits"]) == 3
    by_chat = {h["chat_id"]: h for h in result["hits"]}
    hit = next(h for h in result["hits"] if h["id"] == ids_a[0])
    snippet = hit["snippet"]
    assert snippet["text"][snippet["marks"][0][0]:snippet["marks"][0][1]] == "эльфа"
    assert by_chat[b]["title"] == "Замок" and hit["role"] == "user" and hit["on_path"] is True
    # case does not matter, every word must be there, a phrase in quotes is exact
    assert len((await found(client, q="ЭЛЬФЫ людей"))["hits"]) == 1
    assert len((await found(client, q='"эльфа у реки"'))["hits"]) == 1
    assert (await found(client, q='"у реки эльфа"'))["hits"] == []
    assert (await found(client, q="дракон"))["hits"] == []


async def test_marks_count_in_browser_positions_with_emoji(client, ctx):
    make_chat(ctx, "E", [("user", "😀😀 привет, дракон")])
    hit = (await found(client, q="дракон"))["hits"][0]["snippet"]
    # JS strings count an emoji as two units
    raw = hit["text"].encode("utf-16-le")
    assert raw[hit["marks"][0][0] * 2:hit["marks"][0][1] * 2].decode("utf-16-le") == "дракон"


async def test_hidden_messages_stay_out_and_other_branch_is_marked(client, ctx):
    chat_id, ids = make_chat(ctx, "Ветки", [("user", "старт"), ("assistant", "первая ветка про золото")])
    side = ctx.chats.add_message(chat_id, role="assistant", content="вторая ветка про золото", parent_id=ids[0],
                                 set_leaf=False)
    ctx.chats.add_message(chat_id, role="system", content="скрытое золото", parent_id=ids[1], hidden=True,
                          set_leaf=False)
    hits = (await found(client, q="золото"))["hits"]
    assert len(hits) == 2
    assert {h["id"]: h["on_path"] for h in hits} == {ids[1]: True, side["id"]: False}
    # the jump the page already uses works on a result from the other branch
    assert (await client.post(f"/api/chats/{chat_id}/branch", json={"leaf_id": side["id"]})).status_code == 200


async def test_deleted_branch_is_gone(client, ctx):
    chat_id, ids = make_chat(ctx, "Del", [("user", "старт"), ("assistant", "про золото")])
    assert len((await found(client, q="золото"))["hits"]) == 1
    ctx.chats.delete_message(ids[1])
    assert (await found(client, q="золото"))["hits"] == []


async def test_filters_character_dates_group_and_chat(client, ctx):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "x"})).json()
    bob = (await client.post("/api/characters", json={"name": "Bob", "description": "x"})).json()
    c1, m1 = make_chat(ctx, "Анн", [("user", "меч в камне")], character_id=ann["id"])
    c2, m2 = make_chat(ctx, "Боб", [("user", "меч и щит")], character_id=bob["id"])
    c3, m3 = make_chat(ctx, "Без", [("user", "меч дракона")])
    ctx.db.execute("UPDATE messages SET created_at = '2026-01-05T10:00:00.000Z' WHERE id = ?", (m1[0],))
    ctx.db.execute("UPDATE messages SET created_at = '2026-02-05T10:00:00.000Z' WHERE id = ?", (m2[0],))
    ctx.db.execute("UPDATE messages SET created_at = '2026-03-05T10:00:00.000Z' WHERE id = ?", (m3[0],))

    def ids(result):
        return [h["id"] for h in result["hits"]]

    assert ids(await found(client, q="меч")) == [m3[0], m2[0], m1[0]]  # newest first
    assert ids(await found(client, q="меч", character=bob["id"])) == [m2[0]]
    assert ids(await found(client, q="меч", since="2026-02-01", until="2026-02-28")) == [m2[0]]
    assert ids(await found(client, q="меч", until="2026-01-05")) == [m1[0]]  # the whole last day counts
    assert ids(await found(client, q="меч", chat=c3)) == [m3[0]]
    group = (await client.post("/api/groups", json={"kind": "chat", "name": "Г"})).json()
    await client.post("/api/groups/assign", json={"kind": "chat", "item_ids": [c1, c2], "group_id": group["id"]})
    assert set(ids(await found(client, q="меч", group=group["id"]))) == {m1[0], m2[0]}
    assert ids(await found(client, q="меч", group="none")) == [m3[0]]
    assert (await client.get("/api/search", params={"q": "меч", "since": "вчера"})).status_code == 400
    assert (await client.get("/api/search", params={"q": "меч", "since": "2026-05-01", "until": "2026-01-01"})
            ).status_code == 400


async def test_paging(client, ctx):
    make_chat(ctx, "Много", [("user", f"слово {n}") for n in range(25)])
    first = await found(client, q="слово", limit=10)
    second = await found(client, q="слово", limit=10, offset=10)
    third = await found(client, q="слово", limit=10, offset=20)
    assert [len(p["hits"]) for p in (first, second, third)] == [10, 10, 5]
    assert [p["has_more"] for p in (first, second, third)] == [True, True, False]
    everything = [h["id"] for p in (first, second, third) for h in p["hits"]]
    assert len(set(everything)) == 25


async def test_bad_queries_are_refused(client):
    assert (await client.get("/api/search", params={"q": "я"})).status_code == 400
    assert (await client.get("/api/search", params={"q": "ab", "scope": "other"})).status_code == 400


async def test_assistant_conversations_are_searched_apart(client, ctx):
    make_chat(ctx, "Обычный", [("user", "про эльфа и лес")])
    conv = ctx.assistant_repo.create_chat("Моя беседа")
    first = ctx.assistant_repo.add_message(conv["id"], "user", "создай лорбук про эльфов")
    ctx.assistant_repo.add_message(conv["id"], "assistant", "Готово: эльфы добавлены.")
    ctx.assistant_repo.add_message(conv["id"], "tool", "эльфы служебное")
    ctx.assistant_repo.add_message(conv["id"], "assistant", "эльфы шаг", {"kind": "progress"})
    chats = await found(client, q="эльф", scope="chats")
    helper = await found(client, q="эльф", scope="assistant")
    assert len(chats["hits"]) == 1 and chats["hits"][0]["scope"] == "chats"
    assert {h["scope"] for h in helper["hits"]} == {"assistant"}
    assert len(helper["hits"]) == 2  # a tool line and a progress line are not conversation
    assert first["id"] in {h["id"] for h in helper["hits"]}
    assert all(h["title"] == "Моя беседа" and h["chat_id"] == conv["id"] for h in helper["hits"])
    assert len((await found(client, q="эльф", scope="assistant", since="2000-01-01"))["hits"]) == 2


@pytest.fixture
async def with_model(client, fake_llm):
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    return fake_llm


async def test_info_tells_the_cost_and_never_the_key(client, with_model):
    data = (await client.get("/api/search/info")).json()
    assert data["max_requests"] == search.MAX_REQUESTS and data["model"]["model"] == "m"
    assert "key" not in str(data).lower()


async def test_model_search_keeps_only_real_numbers_and_records_spending(client, ctx, with_model):
    c1, m1 = make_chat(ctx, "Предательство", [("user", "Эльф предал короля и бежал."), ("assistant", "Король в ярости.")])
    make_chat(ctx, "Погода", [("user", "Сегодня дождь и король ушёл.")])
    with_model.queue('{"words": ["эльф", "предать", "король"]}',
                     '{"matches": [{"n": 1, "reason": "эльф предал короля"}, {"n": 99, "reason": "выдумка"},'
                     ' {"n": 1, "reason": "повтор"}, {"n": "x", "reason": "мусор"}]}')
    response = await client.post("/api/search/ask", json={"scope": "chats", "question": "чат, где эльф предал короля"})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["requests"] == 2 and data["stopped"] is False
    assert len(data["hits"]) == 1
    hit = data["hits"][0]
    assert hit["id"] == m1[0] and hit["reason"] == "эльф предал короля" and hit["chat_id"] == c1
    prompt = with_model.requests[-1]["messages"][-1]["content"]
    assert "[1]" in prompt and "Предательство" in prompt
    spent = ctx.db.fetchone("SELECT COUNT(*) AS n FROM spending WHERE purpose = 'search'")
    assert spent["n"] == 2


async def test_model_search_says_nothing_when_nothing_matches_the_words(client, ctx, with_model):
    make_chat(ctx, "Погода", [("user", "Сегодня дождь.")])
    with_model.queue('{"words": ["дракон"]}')
    data = (await client.post("/api/search/ask", json={"scope": "chats", "question": "где дракон сжёг город"})).json()
    assert data["hits"] == [] and data["candidates"] == 0 and data["requests"] == 1


async def test_model_search_works_for_the_assistant_too(client, ctx, with_model):
    conv = ctx.assistant_repo.create_chat("Беседа")
    mid = ctx.assistant_repo.add_message(conv["id"], "user", "сделай тему с сакурой")["id"]
    with_model.queue('{"words": ["сакура"]}', '{"matches": [{"n": 1, "reason": "про тему"}]}')
    data = (await client.post("/api/search/ask", json={"scope": "assistant", "question": "где просил тему с цветами"})
            ).json()
    assert [h["id"] for h in data["hits"]] == [mid] and data["hits"][0]["scope"] == "assistant"


async def test_model_search_bad_input_and_no_connection(client, ctx):
    assert (await client.post("/api/search/ask", json={"scope": "chats", "question": "x"})).status_code == 400
    response = await client.post("/api/search/ask", json={"scope": "chats", "question": "длинное описание чата"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "no_connection"


async def test_model_search_can_be_stopped(client, ctx, with_model, monkeypatch):
    make_chat(ctx, "Эльфы", [("user", "эльф предал")])
    with_model.queue('{"words": ["эльф"]}')
    gate = asyncio.Event()
    real = search.LLMClient.generate_json
    calls = {"n": 0}

    async def slow(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            gate.set()
            await asyncio.sleep(30)
        return await real(self, *a, **k)

    monkeypatch.setattr(search.LLMClient, "generate_json", slow)
    pending = asyncio.ensure_future(client.post(
        "/api/search/ask", json={"scope": "chats", "question": "про предательство эльфа", "token": "t1"}))
    await asyncio.wait_for(gate.wait(), 10)
    assert (await client.post("/api/search/stop", json={"token": "t1"})).json() == {"stopped": True}
    data = (await asyncio.wait_for(pending, 10)).json()
    assert data["stopped"] is True and data["hits"] == []
    assert (await client.post("/api/search/stop", json={"token": "t1"})).json() == {"stopped": False}


def test_speed_on_fifty_thousand_messages(ctx, capsys):
    ctx.db.execute("INSERT INTO characters (id, name, spec, data, created_at, updated_at) "
                   "VALUES ('c', 'C', 's', '{}', 'x', 'x')")
    words = "лес река замок эльф король дракон меч щит ночь город дорога ветер огонь камень золото".split()
    with ctx.db.transaction() as conn:
        for chat in range(500):
            conn.execute("INSERT INTO chats (id, character_id, title, leaf_id) VALUES (?, 'c', ?, ?)",
                         (f"chat{chat}", f"Чат {chat}", f"m{chat}_99"))
            for n in range(100):
                text = " ".join(words[(chat * 7 + n * 3 + k) % len(words)] for k in range(60))
                if chat == 321 and n == 42:
                    text += " хрустальные черепки"
                conn.execute("INSERT INTO messages (id, chat_id, parent_id, depth, role, created_at) "
                             "VALUES (?, ?, ?, ?, 'user', ?)",
                             (f"m{chat}_{n}", f"chat{chat}", f"m{chat}_{n - 1}" if n else None, n,
                              f"2026-01-{1 + n % 28:02d}T10:00:00.000Z"))
                conn.execute("INSERT INTO swipes (message_id, idx, content, created_at) VALUES (?, 0, ?, 'x')",
                             (f"m{chat}_{n}", text))
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM messages")["n"] == 50_000
    start = time.perf_counter()
    rare = search.search(ctx, "chats", "хрустальные черепки")
    rare_time = time.perf_counter() - start
    assert [h["id"] for h in rare["hits"]] == ["m321_42"] and rare["hits"][0]["on_path"] is True
    start = time.perf_counter()
    common = search.search(ctx, "chats", "эльф король")
    common_time = time.perf_counter() - start
    assert len(common["hits"]) == search.PAGE and common["has_more"]
    with capsys.disabled():
        print(f"\n50 000 messages: rare word {rare_time * 1000:.0f} ms, common words (a page of 30) "
              f"{common_time * 1000:.0f} ms")
    assert rare_time < 5 and common_time < 5
