"""Owner's complaints about the assistant (2026-11): it asked for material that was in the app, it swallowed
a 100-entry book whole and skipped half, it could not link bubbles (no tool) and then linked far too much,
its plans had no detail inside, and it asked technical questions. Each is pinned here."""

from __future__ import annotations

import asyncio
import json

import pytest

from tavern.services import assistant_engine as engine
from tavern.services import assistant_tools as tools
from tavern.services import character_tree, lorebooks
from tavern.storage import snapshot

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731
BIG = J(say="", actions=[], big=True)
ACCEPT = J(say="", actions=[], checks=[{"n": n, "ok": True, "evidence": "цитата"} for n in range(1, 7)])


def act(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


async def setup(client) -> str:
    conn = (await client.post("/api/connections", json={"name": "Main", "base_url": "http://llm/v1",
                                                         "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return (await client.post("/api/assistant/chats")).json()["id"]


async def wait(client, ctx, conv: str, response) -> dict:
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=20)
    return (await client.get(f"/api/assistant/chats/{conv}")).json()


def all_text(fake_llm, index: int) -> str:
    return "\n".join(m["content"] for m in fake_llm.requests[index]["messages"])


async def book_with(ctx, count: int, name: str = "Старый мир") -> tuple[dict, list[dict]]:
    book = await lorebooks.create_book(ctx, {"name": name})
    blocks = [await character_tree.create_block(ctx, book["id"], {
        "title": f"Запись {n}", "content": f"Текст записи {n}.", "mode": "search", "keywords": [f"слово{n}"]})
        for n in range(1, count + 1)]
    return book, blocks


# --- 3 and 4: links: the tool exists, works, is undone, and is limited in code -----------------------------


def test_the_link_limit_is_sized_to_the_book_and_only_the_user_raises_it():
    assert tools.link_limits(100) == (12, 2)  # about one link per eight entries
    assert tools.link_limits(10)[0] == 3 and tools.link_limits(0)[0] == 3  # a small book still may have a few
    assert tools.link_limits(100, "свяжи записи")[0] == 12
    assert tools.link_limits(100, "сделай 30 связей")[0] == 30
    assert tools.link_limits(100, "Make 5 links")[0] == 5
    assert tools.link_limits(100, "свяжи как можно больше")[0] == 48
    assert tools.link_limits(10, "as many links as possible")[0] == 12


async def test_the_assistant_can_link_the_entries_of_a_book_and_undo_it(ctx):
    book, blocks = await book_with(ctx, 4)
    env = tools.Env(ctx)
    assert {"link_bubbles", "unlink_bubbles", "list_links"} <= set(tools.TOOLS)
    assert "link_bubbles" in tools.describe()
    out = await tools.get("link_bubbles").run(env, {"owner_id": book["id"], "a": blocks[0]["id"], "b": blocks[1]["id"],
                                                    "reason": "Запись 2 непонятна без записи 1"})
    assert out.what.startswith("«Старый мир»: связь «Запись 1» ↔ «Запись 2»")
    assert ctx.blocks.links([book["id"]]) == [tuple(sorted((blocks[0]["id"], blocks[1]["id"])))]
    assert env.link_counts == {book["id"]: 1}
    again = await tools.get("link_bubbles").run(env, {"owner_id": book["id"], "a": blocks[1]["id"],
                                                      "b": blocks[0]["id"], "reason": "Запись 2 непонятна без 1"})
    assert again.result["note"] == "already linked" and env.link_counts == {book["id"]: 1}
    shown = (await tools.get("read_bubble").run(env, {"id": blocks[0]["id"]})).result
    assert shown["linked_to"] == [{"id": blocks[1]["id"], "title": "Запись 2"}]
    snapshot.restore(ctx.db, out.undo)  # «Отменить»: the undo of a link that is only a created row
    assert ctx.blocks.links([book["id"]]) == []


async def test_a_link_needs_a_reason_and_one_book(ctx):
    book, blocks = await book_with(ctx, 3)
    other, foreign = await book_with(ctx, 1, "Другая")
    env = tools.Env(ctx)
    link = tools.get("link_bubbles")
    with pytest.raises(tools.ToolError, match="reason"):
        await link.run(env, {"owner_id": book["id"], "a": blocks[0]["id"], "b": blocks[1]["id"], "reason": "связаны"})
    with pytest.raises(tools.ToolError, match="belong"):
        await link.run(env, {"owner_id": book["id"], "a": blocks[0]["id"], "b": foreign[0]["id"],
                             "reason": "связаны через что-то общее"})


async def test_links_stop_at_the_limit_even_when_the_model_keeps_going(ctx):
    book, blocks = await book_with(ctx, 40)  # 40 entries: at most 5 links
    env = tools.Env(ctx)
    link = tools.get("link_bubbles")
    made = 0
    refusal = ""
    for n in range(0, 40, 2):
        try:
            await link.run(env, {"owner_id": book["id"], "a": blocks[n]["id"], "b": blocks[n + 1]["id"],
                                 "reason": "одна запись непонятна без другой"})
            made += 1
        except tools.ToolError as exc:
            refusal = str(exc.message)
            break
    assert made == 5 == tools.link_limits(40)[0]
    assert "Link limit reached" in refusal and "tell the user" in refusal
    # Asked for more, the user raises it: the limit follows the user's words, not the model's.
    env.asked_for = "свяжи как можно больше"
    await link.run(env, {"owner_id": book["id"], "a": blocks[38]["id"], "b": blocks[39]["id"],
                         "reason": "одна запись непонятна без другой"})


async def test_an_entry_is_never_linked_with_its_direct_parent(ctx):
    book, _ = await book_with(ctx, 0)
    top = await character_tree.create_block(ctx, book["id"], {"title": "Дед", "content": "т", "mode": "always"})
    mid = await character_tree.create_block(ctx, book["id"], {"title": "Отец", "content": "т", "mode": "always",
                                                              "parent_id": top["id"]})
    low = await character_tree.create_block(ctx, book["id"], {"title": "Сын", "content": "т", "mode": "always",
                                                              "parent_id": mid["id"]})
    free = await character_tree.create_block(ctx, book["id"], {"title": "Чужой", "content": "т", "mode": "always"})
    env = tools.Env(ctx)
    env.asked_for = "свяжи как можно больше"
    link = tools.get("link_bubbles")
    why = "одна запись непонятна без другой"
    for a, b in ((mid, low), (low, mid), (top, mid)):
        with pytest.raises(tools.ToolError, match="parent and child"):
            await link.run(env, {"owner_id": book["id"], "a": a["id"], "b": b["id"], "reason": why})
    for a, b in ((top, low), (low, free)):  # grandparent and unrelated still work
        await link.run(env, {"owner_id": book["id"], "a": a["id"], "b": b["id"], "reason": why})
    assert len(ctx.blocks.links([book["id"]])) == 2


async def test_no_entry_becomes_a_hub(ctx):
    book, blocks = await book_with(ctx, 40)
    env = tools.Env(ctx)
    link = tools.get("link_bubbles")
    for other in (1, 2):
        await link.run(env, {"owner_id": book["id"], "a": blocks[0]["id"], "b": blocks[other]["id"],
                             "reason": "одна запись непонятна без другой"})
    with pytest.raises(tools.ToolError, match="already has 2 links"):
        await link.run(env, {"owner_id": book["id"], "a": blocks[0]["id"], "b": blocks[3]["id"],
                             "reason": "одна запись непонятна без другой"})


async def test_a_long_list_of_entries_is_paged_never_cut_silently(ctx):
    book, blocks = await book_with(ctx, 95)
    env = tools.Env(ctx)
    page = (await tools.get("list_bubbles").run(env, {"owner_id": book["id"]})).result
    assert page["total"] == 95 and len(page["entries"]) == 40 and "offset=40" in page["more"]
    last = (await tools.get("list_bubbles").run(env, {"owner_id": book["id"], "offset": 80})).result
    assert last["showing"] == "81-95" and "more" not in last
    small, _ = await book_with(ctx, 3, "Малый")
    assert isinstance((await tools.get("list_bubbles").run(env, {"owner_id": small["id"]})).result, list)


# --- 2: a sweep counts every entry in code ----------------------------------------------------------


async def start_sweep_work(client, ctx, fake_llm, book: dict, request: str = "Прочитай и поправь все записи",
                           detail: str = "Каждую запись проверить") -> str:
    conv = await setup(client)
    fake_llm.queue(BIG, J(say="Вопрос:", questions=[{"text": "Тон?", "options": ["Мрачный", "Светлый"], "default": 0}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": request}))
    fake_llm.queue(J(say="План:", goal="Проверить книгу", decisions=["тон мрачный"],
                     items=[{"title": "Пройти по записям", "detail": detail, "over": book["id"]}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}}))
    assert state["task"]["items"][0]["over"] == book["id"]
    return conv


async def test_every_entry_of_a_big_book_is_handed_over_and_counted(client, ctx, fake_llm):
    book, blocks = await book_with(ctx, 25)
    conv = await start_sweep_work(client, ctx, fake_llm, book)
    fake_llm.queue(
        J(steps=["Прочитать пачку", "Поправить текст, где нужно", "Отчитаться"]),  # the item's mini-plan
        # batch 1 (entries 1-10): one is changed, all reported
        J(say="Правлю", actions=[act("update_bubble", id=blocks[0]["id"], changes={"content": "Новый текст 1."})],
          done_steps=[1]),
        J(say="Пачка 1", actions=[], handled=list(range(1, 11)), notes="поправил первую"),
        # batch 2 (11-20): the model forgets 18-20, is asked, then reports them (one skipped on purpose)
        J(say="Пачка 2", actions=[], handled=list(range(11, 18))),
        J(say="Остальное", actions=[], handled=[18, 19], skipped=[{"n": 20, "reason": "пустая по смыслу"}]),
        # batch 3 (21-25): reported as a range of strings
        J(say="Пачка 3", actions=[], handled=["21-25"]),
        # then the critic and the report
        ACCEPT,
        J(say="Прошёл по 25 записям."),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    task = state["task"]
    item = task["items"][0]
    assert task["phase"] == "done" and item["status"] == "done"
    sweep = item["sweep"]
    assert len(sweep["ids"]) == 25 and len(sweep["done"]) == 24 and list(sweep["skipped"]) == [blocks[19]["id"]]
    assert sweep["unreported"] == {} and sweep["batches"] == 3
    assert item["summary"].startswith("Handled 24 of 25 entries of «Старый мир» (changed: 1); deliberately skipped 1")
    # The writer saw the entries in batches with their full text, not the whole book at once.
    batch1 = next(all_text(fake_llm, i) for i in range(len(fake_llm.requests)) if "entries 1-10 of 25" in all_text(fake_llm, i))
    assert "Текст записи 10." in batch1 and "Текст записи 11." not in batch1
    # The mini-plan was written before the item and pinned while it ran.
    assert [s["text"] for s in item["steps"]][0] == "Прочитать пачку" and all(s["done"] for s in item["steps"])
    assert "MINI-PLAN of this item" in batch1 and "Поправить текст, где нужно" in batch1
    # The report is given the counts by Tavern, and told to state them as they are.
    report = all_text(fake_llm, len(fake_llm.requests) - 1)
    assert "Handled 24 of 25 entries" in report and "Do not write counts" in report
    last = state["messages"][-1]["text"]  # the count line is written by code under the model's words
    assert last.startswith("Прошёл по 25 записям.") and (
        "Записи «Старый мир»: обработано 24 из 25; пропущено намеренно 1 (" in last)
    progress = [m["text"] for m in state["messages"] if m["data"].get("kind") == "progress"]
    assert any("записи 11–20 из 25" in line for line in progress)
    assert any(line.startswith("Мини-план пункта 1") for line in progress)


async def test_an_entry_the_model_never_reports_is_not_counted_as_done(client, ctx, fake_llm):
    book, blocks = await book_with(ctx, 12)
    conv = await start_sweep_work(client, ctx, fake_llm, book)
    fake_llm.queue(
        J(steps=["Пройти записи"]),
        J(say="Правлю", actions=[act("update_bubble", id=blocks[0]["id"], changes={"title": "Первая"})]),
        J(say="", actions=[], handled=list(range(1, 9))),  # 9-10 are missing
        J(say="", actions=[], handled=[]),  # asked again (1), still nothing
        J(say="", actions=[], handled=[]),  # asked again (2), still nothing
        J(say="Пачка 2", actions=[], handled=[11, 12]),
        ACCEPT,
        J(say="Две записи не обработаны."),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    item = state["task"]["items"][0]
    assert item["status"] == "partial"  # never "done" while an entry is unaccounted for
    assert set(item["sweep"]["unreported"]) == {blocks[8]["id"], blocks[9]["id"]}
    assert "NOT handled 2" in item["summary"] and "Handled 10 of 12" in item["summary"]
    report = all_text(fake_llm, len(fake_llm.requests) - 1)
    assert "Items not finished: 1. Пройти по записям (partial)" in report
    assert "NOT handled 2 (the model did not report them): «Запись 9», «Запись 10»" in report


async def test_a_writer_that_finds_a_big_book_hands_the_item_to_the_sweep(client, ctx, fake_llm):
    book, blocks = await book_with(ctx, 3)
    conv = await setup(client)
    fake_llm.queue(BIG, J(say="Вопрос:", questions=[{"text": "Тон?", "options": ["Мрачный", "Светлый"]}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "Сделай что-нибудь"}))
    fake_llm.queue(J(say="План:", goal="г", decisions=[], items=[{"title": "Обработать", "detail": "всё"}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}}))
    fake_llm.queue(
        J(steps=["Начать"]),
        J(say="Это большая книга", actions=[act("begin_sweep", owner_id=book["id"])]),
        J(say="Все три", actions=[], handled=[1, 2, 3]),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    item = state["task"]["items"][0]
    assert item["over"] == book["id"] and len(item["sweep"]["done"]) == 3


async def test_a_plan_item_that_says_all_and_names_one_book_becomes_a_sweep(ctx):
    book, _ = await book_with(ctx, 3, "Хроники Севера")
    items = engine._make_items([{"title": "Прочитать все записи", "detail": "каждую"},
                                {"title": "Создать лорбук", "detail": "новый"}])
    engine._set_over(ctx, items, "Возьми персонажей из лорбука Хроники Севера")
    assert items[0]["over"] == book["id"] and items[1]["over"] == ""


# --- 1: material in the app is looked up, never asked for -------------------------------------------


async def test_the_planner_sees_what_is_in_the_app_and_questions_asking_to_send_it_are_dropped(client, ctx, fake_llm):
    book, _ = await book_with(ctx, 3, "Хроники Севера")
    conv = await setup(client)
    fake_llm.queue(BIG, J(say="Вопросы:", questions=[
        {"text": "Пришлите лорбук, из которого брать персонажей", "options": ["Вставлю текст", "Загружу файл"]},
        {"text": "Кого брать?", "options": ["Только людей", "Людей и существ"], "default": 0}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={
        "text": "Вынь персонажей из лорбука Хроники Севера и положи их в Персонажи"}))
    asked = [q["text"] for q in state["task"]["questions"]]
    assert asked == ["Кого брать?", "На каком языке писать содержимое?"]
    planner = all_text(fake_llm, 1)
    assert "IN THE APP RIGHT NOW" in planner and f"«Хроники Севера» (id {book['id']}, 3 entries)" in planner
    assert "EXISTING LOREBOOK «Хроники Севера»" in planner and "never ask the user to send" in planner


async def test_a_small_answer_that_asks_to_send_the_book_is_sent_back_to_look(client, ctx, fake_llm):
    book, _ = await book_with(ctx, 3, "Хроники Севера")
    conv = await setup(client)
    fake_llm.queue(
        J(say="Пришлите мне лорбук, пожалуйста.", actions=[]),
        J(say="Смотрю", actions=[act("list_lorebooks")]),
        J(say="Нашёл лорбук «Хроники Севера», в нём 3 записи.", actions=[]),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "посмотри мой лорбук"}))
    texts = [m["text"] for m in state["messages"] if m["role"] == "assistant"]
    assert not any("Пришлите" in t for t in texts)  # the ask never reached the user
    assert "Do not ask the user to send it" in all_text(fake_llm, 1)
    assert texts[-1].startswith("Нашёл лорбук")


# --- 6: technical questions are the assistant's own ------------------------------------------------------


@pytest.mark.parametrize("text", [
    "Сворачивать через CSS :hover или через скрипт?", "Use a JavaScript library or plain HTML?",
    "Опираться на наведение мыши?", "Нужна ли поддержка сенсорных экранов?"])
def test_technical_questions_are_recognised(text):
    assert engine.is_technical(text)


@pytest.mark.parametrize("text", ["Какой тон у мира?", "Что случается, когда игрок нажал на кнопку?",
                                  "Сколько фракций?", "Какой цвет у шапки?"])
def test_questions_about_taste_content_and_behaviour_pass(text):
    assert not engine.is_technical(text)


async def test_a_technical_question_is_dropped_and_the_writer_is_told_to_decide(client, ctx, fake_llm):
    conv = await setup(client)
    fake_llm.queue(BIG, J(say="Вопросы:", questions=[
        {"text": "Сворачивать панель через CSS :hover или через скрипт?", "options": ["CSS :hover", "Скрипт"]},
        {"text": "Какого цвета панель?", "options": ["Тёмная", "Светлая"], "default": 0}]))
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send",
                                                            json={"text": "Сделай сворачиваемую панель"}))
    task = state["task"]
    assert [q["text"] for q in task["questions"]][0] == "Какого цвета панель?"
    assert task["tech_dropped"] == ["Сворачивать панель через CSS :hover или через скрипт?"]
    pinned = engine.pinned(task)
    assert "never ask the user" in pinned and "CSS :hover" in pinned and "touch screens" in pinned
    assert "technical" in all_text(fake_llm, 1).lower() and "touch" in all_text(fake_llm, 1)


# --- 5: a mini-plan before each item -----------------------------------------------------------------------


async def test_each_item_gets_a_mini_plan_that_is_pinned_while_it_is_done(client, ctx, fake_llm):
    book = (await client.post("/api/lorebooks", json={"name": "Город"})).json()
    conv = await setup(client)
    fake_llm.queue(BIG, J(say="В:", questions=[{"text": "Тон?", "options": ["М", "С"], "default": 0}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/send", json={"text": "Сделай записи"}))
    fake_llm.queue(J(say="П:", goal="г", decisions=[], items=[{"title": "Порт", "detail": "запись о порте"}]))
    await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/answers", json={"answers": {}}))
    fake_llm.queue(
        J(steps=["Придумать, что хочет порт", "Написать запись с правилами «Если … →»", "Проверить ключевые слова"]),
        J(say="Пишу", actions=[act("create_bubble", owner_id=book["id"], title="Порт", content="Порт.",
                                   keywords=["порт"])], done_steps=[1, 2]),
        J(say="Готово", actions=[], done_steps=[3]),
        ACCEPT,
        J(say="Одна запись."),
    )
    state = await wait(client, ctx, conv, await client.post(f"/api/assistant/chats/{conv}/go", json={}))
    item = state["task"]["items"][0]
    assert [s["done"] for s in item["steps"]] == [True, True, True]
    writer_call = next(all_text(fake_llm, i) for i in range(len(fake_llm.requests))
                       if "CURRENT ITEM 1" in all_text(fake_llm, i) and "writer" in all_text(fake_llm, i).lower()
                       and "MINI-PLAN of this item" in all_text(fake_llm, i))
    assert "1. [ ] Придумать, что хочет порт" in writer_call
    shown = next(m for m in state["messages"] if m["text"].startswith("Мини-план пункта 1"))
    assert shown["data"]["detail"].splitlines()[1] == "2. Написать запись с правилами «Если … →»"
    assert any(m["data"].get("steps_done") == [1, 2] for m in state["messages"])
