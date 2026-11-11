"""Memory++ M8 through the HTTP API: documents given to the memory. A file is cut into chapters and pieces at once, with no
model and no money; the price is shown before any model work and the run needs a yes; vectors and summaries are made in
the background; a chat finds only what is attached to it (and only with memory on in it), in its own budget; detached
means gone from the next reply; deleting takes everything with it."""

from __future__ import annotations

import base64
import io
import json
import zipfile

from tests.integration.test_memory_plugin import boot, idle, say, switch
from tests.integration.test_memory_summaries import models_on, plugin_module
from tests.integration.test_memory_threads import entries, reply

DRAGON = "Дракон Вальгард спит в пещере у реки Серебрянки и охраняет золото. " * 12
FORGE = "Кузнец Торвин куёт мечи в кузнице на площади и продаёт их стражникам. " * 12
SEA = "Корабль Морская Лиса уходит в плавание к острову Мелькор через бурю. " * 12
BOOK = f"# Глава 1. Пещера\n\n{DRAGON}\n\n# Глава 2. Кузница\n\n{FORGE}\n\n# Глава 3. Море\n\n{SEA}\n"


def docs_entries(saw) -> dict[str, str]:
    return {s: t for s, t in entries(saw).items() if s.startswith("plugin:memory:doc:")}


async def call(client, action, **payload) -> dict:
    r = await client.post(f"/api/plugins/memory/actions/{action}", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def doc_chat(client, ctx, *, window=4000, settings=None):
    setup = await boot(client, ctx, window=window, settings=settings)
    chat_id = setup["chat"]["id"]
    for i in range(4):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i} дело{i}")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    return setup, chat_id


async def add(client, chat_id, text=BOOK, name="книга.md", **more) -> dict:
    data = base64.b64encode(text.encode("utf-8")).decode()
    return await call(client, "doc_add", chat_id=chat_id, name=name, data=data, **more)


def docx(paragraphs: list[tuple[str, str]]) -> bytes:
    body = "".join(
        f'<w:p>{f"<w:pPr><w:pStyle w:val=\"{style}\"/></w:pPr>" if style else ""}<w:r><w:t>{text}</w:t></w:r></w:p>'
        for style, text in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


# -- cutting (pure) ----------------------------------------------------------------------------------------------


async def test_headings_make_the_chapters(client):
    docs = await _docs(client)
    sections = docs.cut_document(BOOK)
    assert [BOOK[s.start:s.end].splitlines()[0] for s in sections] == [
        "# Глава 1. Пещера", "# Глава 2. Кузница", "# Глава 3. Море"]
    assert [s.title for s in sections] == ["Глава 1. Пещера", "Глава 2. Кузница", "Глава 3. Море"]
    assert all(s.by == "heading" for s in sections)
    assert sections[0].start == 0 and sections[-1].end == len(BOOK)


async def test_word_headings_and_tiny_sections(client):
    docs = await _docs(client)
    text = f"Глава 1\n{DRAGON}\n\nГлава 2\nкоротко\n\nГлава 3\n{SEA}\n"
    sections = docs.cut_document(text)
    assert len(sections) == 2  # the tiny «Глава 2» joined the next one
    assert sections[0].title == "Глава 1" and sections[1].title == "Глава 2"
    assert "Корабль" in text[sections[1].start:sections[1].end]


async def test_no_headings_uses_the_chat_cutter_and_loses_no_text(client):
    docs = await _docs(client)
    paragraphs = [f"Абзац {i}. " + ("слово%d " % i) * 20 for i in range(100)]
    paragraphs[50] = "*** Прошло три года. " + paragraphs[50]
    text = "\n\n".join(paragraphs)
    sections = docs.cut_document(text, size=20)
    assert len(sections) >= 2 and all(s.by == "cutter" for s in sections)
    joined = "".join(text[s.start:s.end] for s in sections)
    assert "слово99" in joined and "слово0 " in joined
    starts = [s.start for s in sections]
    assert starts == sorted(starts) and all(a.end <= b.start + 2 for a, b in zip(sections, sections[1:]))
    assert text[sections[1].start:].startswith("*** Прошло три года")  # the marker made the border


async def test_one_line_text_is_still_cut_and_a_huge_section_is_cut_again(client):
    docs = await _docs(client)
    sections = docs.cut_document("слово " * 20000, size=10)
    assert len(sections) >= 2
    big = "# Книга\n\n" + "\n\n".join(f"Абзац {i}. " + "текст " * 40 for i in range(400)) + "\n\n# Конец\n\nкоротко. " * 1
    parts = docs.cut_document(big, size=10)
    assert len(parts) >= 3


async def test_files_are_read_by_their_kind(client):
    docs = await _docs(client)
    assert docs.read_file("a.txt", "Привет, мир".encode("cp1251")) == "Привет, мир"
    assert docs.read_file("a.md", "﻿Текст\r\nвторая".encode("utf-8")) == "Текст\nвторая"
    text = docs.read_file("b.docx", docx([("Heading1", "Глава 1"), ("", "Абзац первый."), ("", "Абзац второй.")]))
    assert text == "# Глава 1\n\nАбзац первый.\n\nАбзац второй."
    for bad in (b"not a zip",):
        try:
            docs.read_file("c.docx", bad)
        except Exception as exc:
            assert "docx" in str(exc)
        else:
            raise AssertionError("a broken .docx must be refused")
    try:
        docs.read_file("d.txt", b"   \n ")
    except Exception as exc:
        assert "нет текста" in str(exc)
    else:
        raise AssertionError("an empty file must be refused")


async def _docs(client):
    """The plugin's `docs` module as the manager loaded it (the plugin is switched on first)."""
    assert (await client.post("/api/plugins/memory/enable", json={})).status_code == 200
    return plugin_module("docs")


# -- the HTTP flow -----------------------------------------------------------------------------------------------


async def test_a_document_is_cut_at_once_with_no_model_and_found_by_words_before_any_run(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    before, embeds = len(fake_llm.requests), len(fake_llm.embedding_requests)
    made = await add(client, chat_id)
    assert (len(fake_llm.requests), len(fake_llm.embedding_requests)) == (before, embeds)  # no money
    doc = made["doc"]
    assert doc["title"] == "книга" and doc["chapters"] == 3 and doc["state"] == "cut" and not doc["started"]
    assert doc["attached"] is True and doc["summarized"] == 0
    estimate = made["estimate"]
    assert estimate["chapters"] == 3 and estimate["summary_requests"] == 3 and estimate["embed_tokens"] > 0
    assert estimate["price_text"]  # the price is there before anything is started
    view = await call(client, "doc_view", doc_id=doc["id"])
    assert [c["title"] for c in view["chapters"]] == ["Глава 1. Пещера", "Глава 2. Кузница", "Глава 3. Море"]
    saw = await reply(client, ctx, chat_id, "Расскажи про дракона Вальгарда в пещере")
    found = docs_entries(saw)
    assert len(found) == 1
    source, text = next(iter(found.items()))
    assert "Вальгард" in text and "Из документа «книга», глава 1" in text
    assert "Кузнец" not in text  # only what the reply is about


async def test_the_run_needs_a_yes_then_makes_vectors_and_summaries(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    await models_on(client)
    doc = (await add(client, chat_id))["doc"]
    r = await client.post("/api/plugins/memory/actions/doc_start", json={"doc_id": doc["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "memory_confirm"
    assert fake_llm.embedding_requests == [] or all("Вальгард" not in json.dumps(e) for e in fake_llm.embedding_requests)
    fake_llm.queue(*[json.dumps({"title": t, "summary": s}, ensure_ascii=False) for t, s in (
        ("Пещера", "Дракон Вальгард живёт в пещере у Серебрянки и стережёт своё золото."),
        ("Кузница", "Кузнец Торвин делает мечи в кузнице и продаёт их страже."),
        ("Море", "Корабль Морская Лиса плывёт к острову Мелькор через бурю."))])
    await call(client, "doc_start", doc_id=doc["id"], confirmed=True)
    import asyncio
    for _ in range(400):
        await asyncio.sleep(0.02)
        state = (await call(client, "docs", chat_id=chat_id))["docs"][0]
        if state["state"] == "ready":
            break
    assert state["state"] == "ready" and state["summarized"] == 3
    summary_calls = [q for q in fake_llm.requests if "memory of a long text" in json.dumps(q["messages"])]
    assert len(summary_calls) == 3 and "Document: книга" in json.dumps(summary_calls[0]["messages"], ensure_ascii=False)
    count = ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_doc_vectors")["n"]
    pieces = ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_doc_pieces")["n"]
    assert count == pieces + 3  # every piece and every summary
    view = await call(client, "doc_view", doc_id=doc["id"])
    assert all(c["summary"] for c in view["chapters"])
    saw = await reply(client, ctx, chat_id, "Что с кузнецом Торвином и мечами?")
    found = docs_entries(saw)
    assert len(found) == 1 and "глава 2" in next(iter(found.values()))
    # the vectors are never part of the assistant's journal or a file
    assert "p_memory_doc_vectors" not in json.dumps(await call(client, "state", chat_id=chat_id))


async def test_a_chat_finds_only_what_is_attached_and_detach_stops_it(client, ctx, fake_llm):
    setup, chat_id = await doc_chat(client, ctx)
    other = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    for i in range(4):
        say(ctx, other, "user" if i % 2 == 0 else "assistant", f"слово{i} дело{i}")
    await switch(client, other)
    await idle(ctx, other)
    doc = (await add(client, chat_id))["doc"]
    ask = "Расскажи про дракона Вальгарда в пещере"
    assert docs_entries(await reply(client, ctx, other, ask)) == {}  # not attached there
    assert len(docs_entries(await reply(client, ctx, chat_id, ask))) == 1
    listed = (await call(client, "docs", chat_id=chat_id))["docs"][0]
    assert listed["attached"] is True
    await call(client, "doc_detach", chat_id=chat_id, doc_id=doc["id"])
    assert docs_entries(await reply(client, ctx, chat_id, ask)) == {}  # gone from the next reply on
    await call(client, "doc_attach", chat_id=chat_id, doc_id=doc["id"])
    await call(client, "doc_attach", chat_id=other, doc_id=doc["id"])  # one document, two chats
    assert len(docs_entries(await reply(client, ctx, other, ask))) == 1
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_docs")["n"] == 1


async def test_memory_off_in_the_chat_means_no_document(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    await add(client, chat_id)
    await switch(client, chat_id, False)
    assert docs_entries(await reply(client, ctx, chat_id, "Расскажи про дракона Вальгарда")) == {}


async def test_the_documents_have_their_own_budget_and_a_zero_share_switches_them_off(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx, window=4000, settings={"docs_percent": 0})
    doc = (await add(client, chat_id))["doc"]
    ask = "Расскажи про дракона Вальгарда в пещере, про кузнеца Торвина и про корабль Морская Лиса"
    assert docs_entries(await reply(client, ctx, chat_id, ask)) == {}
    await client.put("/api/plugins/memory/settings", json={"docs_percent": 10})
    from tavern.core.prompt import estimate_tokens
    found = docs_entries(await reply(client, ctx, chat_id, ask))
    assert found  # budget = max(300, 10 % of 4000) = 400 tokens: at least the best chapter goes
    total = sum(estimate_tokens(t) for t in found.values())
    assert total <= 400 + max(estimate_tokens(t) for t in found.values())  # the first always fits, the rest within
    docs = await _docs(client)
    assert docs.budget({"docs_percent": 10}, 4000) == 400 and docs.budget({"docs_percent": 1}, 4000) == 300
    assert docs.budget({"docs_percent": 10}, 0) == docs.NO_WINDOW_TOKENS and doc


async def test_the_whole_chapter_form_sends_the_chapter_text(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    doc = (await add(client, chat_id))["doc"]
    view = await call(client, "doc_view", doc_id=doc["id"])
    chapter = next(c for c in view["chapters"] if c["number"] == 2)
    await call(client, "doc_chapter", chapter_id=chapter["id"], whole=True)
    saw = await reply(client, ctx, chat_id, "А что там с кузнецом Торвином?")
    text = next(iter(docs_entries(saw).values()))
    assert text.count("Кузнец Торвин куёт мечи") >= 10  # the whole chapter, not one piece
    labels = [e["label"] for e in saw["entries"] if e["source"].startswith("plugin:memory:doc:")]
    assert labels and labels[0].endswith("(вся)")


async def test_deleting_takes_everything_with_it(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    doc = (await add(client, chat_id))["doc"]
    ctx.db.execute("INSERT INTO p_memory_doc_vectors (item_id, doc_id, model, text_hash, vector) VALUES "
                   "('p:x', ?, 'm', 'h', x'00')", (doc["id"],))
    await call(client, "doc_delete", doc_id=doc["id"])
    for table in ("docs", "doc_texts", "doc_chapters", "doc_pieces", "doc_summaries", "doc_chats", "doc_vectors"):
        assert ctx.db.fetchone(f"SELECT COUNT(*) AS n FROM p_memory_{table}")["n"] == 0, table
    assert docs_entries(await reply(client, ctx, chat_id, "Расскажи про дракона Вальгарда")) == {}


async def test_a_docx_goes_through_the_same_way_and_a_bad_file_is_refused(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    data = base64.b64encode(docx([("Heading1", "Глава 1. Пещера"), ("", DRAGON), ("Heading1", "Глава 2. Кузница"),
                                  ("", FORGE)])).decode()
    made = await call(client, "doc_add", chat_id=chat_id, name="роман.docx", data=data)
    assert made["doc"]["title"] == "роман" and made["doc"]["chapters"] == 2
    r = await client.post("/api/plugins/memory/actions/doc_add", json={
        "chat_id": chat_id, "name": "x.docx", "data": base64.b64encode(b"nope").decode()})
    assert r.status_code == 409 and "docx" in r.json()["error"]["message"]


async def test_documents_are_not_in_the_chat_file_and_the_assistant_sees_them(client, ctx, fake_llm):
    _, chat_id = await doc_chat(client, ctx)
    await add(client, chat_id)
    text = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).text
    assert "Вальгард" not in text and "p_memory_doc" not in text
    from tavern.services import assistant_app
    routes = (await assistant_app.app_routes(assistant_app.Env(ctx, chat_id), {"filter": "memory"})).result
    for name in ("docs", "doc_view", "doc_estimate", "doc_attach", "doc_detach", "doc_start", "doc_delete"):
        assert f"actions/{name} " in routes, name
    assert "actions/doc_add " not in routes  # a file comes from the page
    assert plugin_module("docs").MAX_ENTRIES == 4
