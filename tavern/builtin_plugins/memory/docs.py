"""Documents given to the memory (ADR 0019 stage M8): a book or a long text, found from the chats it is attached to.

A document is cut like an old chat, but with no messages: CHAPTERS first by headings («Глава 3», «Chapter 2», «# Title»,
«## Title»; a section that is too short joins the next, one that is too long is cut again), else by the chat's own
border cutter (`borders.py`) over its paragraphs; each chapter into PIECES (`pieces.cut`), as a message is. The cut costs
no money and is done at once. Then, only after the user has seen the price (`estimate`) and agreed (`start`), a
background job (`run`, key `doc:<id>`, the same rules as the chat's: every step through `job.step`, so a reply cancels it
and it is redone; a 429 is waited out) gives the pieces and the chapters' summaries vectors and writes the summaries
(one request per chapter, the «Модель для памяти»).

Used from a chat (`contribute`, called by `inject.contribute`): only when memory is on in the chat AND the document is
attached to it («Документы» in the «Память» window). The same two levels as the chat's chapters: a found chapter goes as
its summary, or, when a piece beats the summary by the margin, as that piece with one neighbour each side; a chapter
marked «Вся глава» goes whole (cut to WHOLE_TOKENS). Own budget: `docs_percent` of the window (at least MIN_TOKENS), at
most MAX_ENTRIES entries per reply, each its own entry «Из документа …» (the memory spot, dropped before the chat's own
memory: DROP_RANK). Detached means gone from the next reply on; the document and its work stay.

Files: .txt and .md as text (UTF-8, else cp1251), .docx by reading `word/document.xml` (pure Python, headings kept).
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from array import array
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

from tavern.core.assembly import Extra
from tavern.core.prompt import estimate_tokens
from tavern.errors import AppError, NotFound
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ChatMessage, ProviderError
from tavern.services import character_tree, embeddings
from tavern.util.ids import new_id, now_iso

from . import borders, bulk, pieces, prompts, search, store, summaries

DOCS, DOC_TEXTS, DOC_CHAPTERS, DOC_PIECES = ("p_memory_docs", "p_memory_doc_texts", "p_memory_doc_chapters",
                                             "p_memory_doc_pieces")
DOC_SUMMARIES, DOC_CHATS, DOC_VECTORS = "p_memory_doc_summaries", "p_memory_doc_chats", "p_memory_doc_vectors"

MAX_BYTES = 8 * 1024 * 1024  # a file this big is refused (a book is about 1-2 MB)
MIN_SECTION_TOKENS = 150  # a section shorter than this joins the next one
MAX_SECTION_TOKENS = 6000  # a section longer than this is cut again by the cutter
MIN_TOKENS = 300  # the smallest documents budget
NO_WINDOW_TOKENS = 800  # the budget when the window is not known
WHOLE_TOKENS = 3000  # «Вся глава» is cut to this
MAX_ENTRIES = 4
DROP_RANK = 30  # under a short context the documents go before the chat's own memory
MAX_TRIES = 3
UNIT_CHARS = 700  # a text with no paragraph breaks is cut into pieces of about this for the cutter

_MD_HEADING = re.compile(r"^#{1,3}[ \t]+\S.*$", re.M)
_WORD_HEADING = re.compile(r"^[ \t]*(?:(?:глава|часть|chapter|part)[ \t]+(?:\d+|[IVXLCivxlc]+)\b.{0,100}|"
                           r"(?:пролог|эпилог|prologue|epilogue)\b.{0,60})[ \t]*$", re.M | re.I)
_ROMAN = re.compile(r"^[ \t]*[IVXLC]{1,6}\.?[ \t]*$", re.M)


class DocError(AppError):
    """A document problem the user can fix (a message for them)."""

    code = "memory_doc"
    status = 409


# -- reading a file ----------------------------------------------------------------------------------------


def decode(data: bytes) -> str:
    """Text from bytes: UTF-8 (with or without a BOM), else Windows-1251 (old Russian files)."""
    for coding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(coding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def docx_text(data: bytes) -> str:
    """The text of a .docx: its paragraphs, a heading style becomes «# Title». Pure Python, no tables / pictures."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DocError("Это не файл .docx (не открывается как документ Word)") from exc
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocError("Файл .docx повреждён") from exc
    lines = []
    for paragraph in root.iter(ns + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(ns + "t")).strip()
        style = paragraph.find(f"{ns}pPr/{ns}pStyle")
        name = (style.get(ns + "val") or "") if style is not None else ""
        lines.append(f"# {text}" if text and name.lower().startswith(("heading", "title")) else text)
    return "\n\n".join(line for line in lines if line)


def read_file(name: str, data: bytes) -> str:
    """The text of an uploaded file by its extension (.txt, .md, .docx; anything else is tried as text)."""
    if len(data) > MAX_BYTES:
        raise DocError("Файл больше 8 МБ: разрежьте его на части")
    text = docx_text(data) if name.lower().endswith(".docx") else decode(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise DocError("В файле нет текста")
    return text


# -- cutting into chapters (pure) --------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    title: str
    start: int
    end: int
    by: str  # heading | cutter


def _units(text: str, base: int = 0) -> list[tuple[int, int]]:
    """Paragraphs of `text` as (start, end) offsets (+ base); a text without blank lines goes by lines, then by size."""
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S[^\n]*(?:\n(?!\s*\n)[^\n]*)*", text)]
    if len(spans) < 4:
        spans = [(m.start(), m.end()) for m in re.finditer(r"\S[^\n]*", text)]
    out: list[tuple[int, int]] = []
    for a, b in spans:
        while b - a > 4 * UNIT_CHARS:  # one huge paragraph: cut at a sentence end near the size
            cut = text.rfind(". ", a + UNIT_CHARS, a + 2 * UNIT_CHARS)
            cut = cut + 1 if cut > 0 else a + UNIT_CHARS
            out.append((a + base, cut + base))
            a = cut
            while a < b and text[a].isspace():
                a += 1
        if a < b:
            out.append((a + base, b + base))
    return out


def cut_by_cutter(text: str, start: int, end: int, size: int) -> list[Section]:
    """[start, end) of `text` into chapters by the chat's border cutter, paragraphs standing for messages."""
    units = _units(text[start:end], start)
    if not units:
        return []
    messages = [{"id": str(i), "role": "user", "content": text[a:b]} for i, (a, b) in enumerate(units)]
    out: list[Section] = []
    at = 0
    while at < len(units):
        remaining = len(units) - at
        cut = None
        if remaining > size:
            window = messages[at:at + 3 * size + borders.WINDOW]
            cut = borders.choose(borders.score_gaps(window), remaining, size)
        take = cut.index if cut else remaining
        out.append(Section("", units[at][0] if at else start, units[at + take - 1][1] if at + take < len(units) else end,
                           "cutter"))
        at += take
    return out


def _title(line: str) -> str:
    return line.strip().lstrip("#").strip()[:120]


def headings(text: str) -> list[tuple[int, str]]:
    """(offset, title) of the heading lines. Markdown headings win; else «Глава N» lines; else lone Roman numerals."""
    for pattern in (_MD_HEADING, _WORD_HEADING, _ROMAN):
        found = [(m.start(), _title(m.group())) for m in pattern.finditer(text)]
        if len(found) >= 2:
            return found
    return []


def cut_document(text: str, size: int = 40) -> list[Section]:
    """Chapters of the whole text (see the module docstring). Never empty for a text with words."""
    found = headings(text)
    sections: list[Section] = []
    if found:
        if text[:found[0][0]].strip() and estimate_tokens(text[:found[0][0]]) >= MIN_SECTION_TOKENS:
            sections.append(Section("Начало", 0, found[0][0], "heading"))
        edges = [*found, (len(text), "")]
        sections += [Section(title, a, edges[i + 1][0], "heading") for i, (a, title) in enumerate(found)]
        # tiny sections join the next (a heading followed at once by another); the last joins the one before
        merged: list[Section] = []
        carry: Section | None = None
        for section in sections:
            if carry is not None:
                section = Section(carry.title, carry.start, section.end, "heading")
                carry = None
            if estimate_tokens(text[section.start:section.end]) < MIN_SECTION_TOKENS:
                carry = section
            else:
                merged.append(section)
        if carry is not None:
            if merged:
                last = merged.pop()
                merged.append(Section(last.title, last.start, carry.end, "heading"))
            else:
                merged.append(carry)
        out: list[Section] = []
        for section in merged:
            if estimate_tokens(text[section.start:section.end]) <= MAX_SECTION_TOKENS:
                out.append(section)
                continue
            parts = cut_by_cutter(text, section.start, section.end, size)
            for n, part in enumerate(parts, 1):
                out.append(Section(section.title + (f" (часть {n})" if len(parts) > 1 else ""), part.start, part.end,
                                   "heading"))
        if len(out) >= 2:
            return _trim(text, out)
    return _trim(text, cut_by_cutter(text, 0, len(text), size) or [Section("", 0, len(text), "cutter")])


def _trim(text: str, sections: list[Section]) -> list[Section]:
    return [s for s in sections if text[s.start:s.end].strip()]


# -- database ---------------------------------------------------------------------------------------------


def _doc(row: Any) -> dict[str, Any]:
    out = dict(row)
    out["started"] = bool(out["started"])
    out["with_summaries"] = bool(out["with_summaries"])
    return out


def get(db: Any, doc_id: str) -> dict[str, Any]:
    row = db.fetchone(f"SELECT * FROM {DOCS} WHERE id = ?", (doc_id,))
    if row is None:
        raise NotFound("Такого документа нет")
    return _doc(row)


def chapter_rows(db: Any, doc_id: str) -> list[dict[str, Any]]:
    rows = db.fetchall(f"SELECT * FROM {DOC_CHAPTERS} WHERE doc_id = ? ORDER BY number", (doc_id,))
    return [{**dict(r), "whole": bool(r["whole"])} for r in rows]


def piece_rows(db: Any, doc_id: str) -> list[dict[str, Any]]:
    rows = db.fetchall(f"SELECT id, chapter_id, part, text, text_hash, tokens, keywords FROM {DOC_PIECES} "
                       "WHERE doc_id = ? ORDER BY chapter_id, part", (doc_id,))
    return [{**dict(r), "keywords": json.loads(r["keywords"])} for r in rows]


def summaries_of(db: Any, doc_id: str) -> dict[str, dict[str, Any]]:
    rows = db.fetchall(f"SELECT chapter_id, text, made_by FROM {DOC_SUMMARIES} WHERE doc_id = ?", (doc_id,))
    return {r["chapter_id"]: dict(r) for r in rows}


def put_summary(db: Any, doc_id: str, chapter_id: str, text: str, by: str) -> None:
    db.execute(f"INSERT INTO {DOC_SUMMARIES} (chapter_id, doc_id, text, made_by, created_at) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT(chapter_id) DO UPDATE SET text = excluded.text, made_by = excluded.made_by, "
               "created_at = excluded.created_at", (chapter_id, doc_id, text, by, now_iso()))


def create(db: Any, title: str, source_name: str, text: str, sections: list[Section]) -> dict[str, Any]:
    """Store the document with its chapters and pieces (no vectors, no summaries yet)."""
    doc_id, now = new_id(), now_iso()
    with db.transaction() as conn:
        conn.execute(f"INSERT INTO {DOCS} (id, title, source_name, state, tokens, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'cut', ?, ?, ?)", (doc_id, title, source_name, estimate_tokens(text), now, now))
        conn.execute(f"INSERT INTO {DOC_TEXTS} (doc_id, text) VALUES (?, ?)", (doc_id, text))
        for number, section in enumerate(sections, 1):
            body = text[section.start:section.end]
            chapter_id = new_id()
            conn.execute(f"INSERT INTO {DOC_CHAPTERS} (id, doc_id, number, title, at_start, at_end, tokens, cut_by) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (chapter_id, doc_id, number, section.title, section.start, section.end,
                          estimate_tokens(body), section.by))
            for part, piece in enumerate(pieces.cut(body)):
                conn.execute(f"INSERT INTO {DOC_PIECES} (id, doc_id, chapter_id, part, text, text_hash, tokens, "
                             "keywords) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (new_id(), doc_id, chapter_id, part, piece, pieces.text_hash(piece),
                              estimate_tokens(piece), json.dumps(pieces.keywords(piece), ensure_ascii=False)))
    return get(db, doc_id)


def full_text(db: Any, doc_id: str) -> str:
    row = db.fetchone(f"SELECT text FROM {DOC_TEXTS} WHERE doc_id = ?", (doc_id,))
    return row["text"] if row else ""


def chapter_text(db: Any, chapter: dict[str, Any]) -> str:
    return full_text(db, chapter["doc_id"])[chapter["at_start"]:chapter["at_end"]].strip()


def attached(db: Any, chat_id: str) -> list[dict[str, Any]]:
    rows = db.fetchall(f"SELECT d.* FROM {DOCS} d JOIN {DOC_CHATS} c ON c.doc_id = d.id WHERE c.chat_id = ? "
                       "ORDER BY c.created_at, d.id", (chat_id,))
    return [_doc(r) for r in rows]


def attach(db: Any, chat_id: str, doc_id: str) -> None:
    get(db, doc_id)
    db.execute(f"INSERT INTO {DOC_CHATS} (chat_id, doc_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
               (chat_id, doc_id, now_iso()))


def detach(db: Any, chat_id: str, doc_id: str) -> None:
    db.execute(f"DELETE FROM {DOC_CHATS} WHERE chat_id = ? AND doc_id = ?", (chat_id, doc_id))


def delete(db: Any, doc_id: str) -> None:
    get(db, doc_id)
    db.execute(f"DELETE FROM {DOCS} WHERE id = ?", (doc_id,))  # the rest cascades (chapters, pieces, vectors, links)


def all_docs(db: Any) -> list[dict[str, Any]]:
    return [_doc(r) for r in db.fetchall(f"SELECT * FROM {DOCS} ORDER BY created_at, id")]


def set_state(db: Any, doc_id: str, state: str, error: str = "", **more: int) -> None:
    sets = ", ".join(f"{k} = ?" for k in more)
    db.execute(f"UPDATE {DOCS} SET state = ?, error = ?, updated_at = ?{', ' + sets if sets else ''} WHERE id = ?",
               (state, error[:300], now_iso(), *more.values(), doc_id))


# -- vectors ----------------------------------------------------------------------------------------------


def vector_ids(db: Any, doc_id: str, model: str) -> dict[str, str]:
    rows = db.fetchall(f"SELECT item_id, text_hash FROM {DOC_VECTORS} WHERE doc_id = ? AND model = ?", (doc_id, model))
    return {r["item_id"]: r["text_hash"] for r in rows}


def save_vectors(db: Any, doc_id: str, model: str, items: list[tuple[str, str, list[float]]]) -> None:
    with db.transaction() as conn:
        for item_id, digest, vector in items:
            conn.execute(f"INSERT INTO {DOC_VECTORS} (item_id, doc_id, model, text_hash, vector) VALUES (?, ?, ?, ?, ?) "
                         "ON CONFLICT(item_id, model) DO UPDATE SET text_hash = excluded.text_hash, "
                         "vector = excluded.vector", (item_id, doc_id, model, digest, store.normalized(vector).tobytes()))


def load_vectors(db: Any, doc_id: str, model: str) -> dict[str, tuple[str, array]]:
    out: dict[str, tuple[str, array]] = {}
    for row in db.fetchall(f"SELECT item_id, text_hash, vector FROM {DOC_VECTORS} WHERE doc_id = ? AND model = ?",
                           (doc_id, model)):
        vector = array("f")
        vector.frombytes(row["vector"])
        out[row["item_id"]] = (row["text_hash"], vector)
    return out


def vector_todo(db: Any, doc_id: str, model: str) -> list[tuple[str, str, str]]:
    """(item id, text, hash) of the pieces and summaries still without a current vector."""
    stored = vector_ids(db, doc_id, model)
    todo = []
    for piece in piece_rows(db, doc_id):
        if stored.get("p:" + piece["id"]) != piece["text_hash"]:
            todo.append(("p:" + piece["id"], piece["text"], piece["text_hash"]))
    for chapter_id, summary in summaries_of(db, doc_id).items():
        digest = pieces.text_hash(summary["text"])
        if stored.get("s:" + chapter_id) != digest:
            todo.append(("s:" + chapter_id, summary["text"], digest))
    return todo


# -- the price (before any model work) ----------------------------------------------------------------------


def estimate(api: Any, doc_id: str, with_summaries: bool = True) -> dict[str, Any]:
    """What the document would cost: vectors for every piece, and (with_summaries) a summary request per chapter."""
    db = api.ctx.db
    chapters_ = chapter_rows(db, doc_id)
    summarized = summaries_of(db, doc_id)
    target = int(api.settings["summary_tokens"])
    todo = [c for c in chapters_ if c["id"] not in summarized] if with_summaries else []
    requests = [("summary", len(todo), (sum(c["tokens"] for c in todo) // len(todo) if todo else 0)
                 + bulk.OVERHEAD["summary"], target)] if todo else []
    model = api.vectors_model()
    embed = sum(estimate_tokens(text) for _, text, _ in vector_todo(db, doc_id, model)) if model else 0
    embed += len(todo) * target if model else 0
    money = bulk.price(api, None, requests, embed)
    doc = get(db, doc_id)
    return {**money, "doc_id": doc_id, "title": doc["title"], "tokens": doc["tokens"], "chapters": len(chapters_),
            "summary_requests": len(todo), "with_summaries": with_summaries}


# -- the job ----------------------------------------------------------------------------------------------


def summary_messages(title: str, chapter_title: str, body: str, previous: str, target: int, note: str,
                     ) -> tuple[list[ChatMessage], str]:
    source = prompts.shorten(" ".join(body.split()), prompts.MAX_INPUT_TOKENS)
    system = (f"You write the memory of a long text (a book or a document). Retell the chapter below as a summary of "
              f"about {target} tokens that a reader will use later instead of the full text. Give the chapter a short "
              f"title of two to six words in the same language.\n"
              f"{prompts.RULES.format(hint=prompts.language_hint(source))}")
    context = f"The text before this chapter (for context only, do not retell it):\n{previous}\n\n" if previous else ""
    head = f"Document: {title}" + (f"\nChapter: {chapter_title}" if chapter_title else "")
    user = f"{context}{head}\n\nThe chapter:\n{source}"
    if note:
        user += f"\n\nYour previous answer was refused: {note}. Answer again."
    return [ChatMessage("system", system), ChatMessage("user", user)], source


async def run(api: Any, doc_id: str, job: Any, clients: Any) -> None:
    """The document's job: vectors, then a summary per chapter, until nothing is left. Todo-driven (see work.py)."""
    ctx = api.ctx
    db = ctx.db
    while not job.stopped:
        try:
            doc = get(db, doc_id)
        except NotFound:
            return
        if not doc["started"]:
            return
        set_state(db, doc_id, "working")
        model = api.vectors_model()
        todo = vector_todo(db, doc_id, model) if model else []
        chapters_ = chapter_rows(db, doc_id)
        sums = summaries_of(db, doc_id)
        if todo:
            batch = embeddings.batches(todo)[0]
            texts = [text for _, text, _ in batch]
            try:
                vectors = await job.step(lambda: api.embed(texts))
            except ProviderError as exc:
                if exc.upstream_status != 429:
                    raise
                await job.rate_wait(embeddings.RATE_WAIT)
                continue
            if len(vectors) != len(batch):
                raise ProviderError("The service returned fewer vectors than texts", reason="request")
            save_vectors(db, doc_id, model, [(i, d, v) for (i, _, d), v in zip(batch, vectors)])
            job.progress(len(sums), len(chapters_), "документ: векторы")
            continue
        chosen = next((c for c in chapters_ if doc["with_summaries"] and c["id"] not in sums
                       and c["tries"] < MAX_TRIES), None)
        if chosen is None:
            failed = [c for c in chapters_ if doc["with_summaries"] and c["id"] not in sums]
            set_state(db, doc_id, "failed" if failed else "ready",
                      "не все главы пересказаны: " + (failed[0]["error"] or "") if failed else "")
            job.progress(len(sums), len(chapters_), "")
            api.notify("docs", doc_id=doc_id)
            return
        job.progress(len(sums), len(chapters_), "документ: пересказы")
        previous = ""
        for c in chapters_:
            if c["number"] < chosen["number"] and c["id"] in sums:
                previous = sums[c["id"]]["text"]
        body = chapter_text(db, chosen)
        target = int(api.settings["summary_tokens"])
        try:
            llm = clients.get("summary")
            value = await job.step(lambda: summaries.ask(
                llm, lambda note: summary_messages(doc["title"], chosen["title"], body, previous, target, note),
                prompts.SUMMARY_SCHEMA, "summary", target))
        except PluginRefused as exc:
            set_state(db, doc_id, "working", exc.message)
            return
        except ProviderError as exc:  # before AppError: it is one
            if exc.upstream_status == 429:
                await job.rate_wait(embeddings.RATE_WAIT)
                continue
            _chapter_failed(db, chosen, exc)
            continue
        except AppError as exc:
            if exc.code == "no_connection":
                set_state(db, doc_id, "working", "нет подключения к модели для пересказов")
                return
            _chapter_failed(db, chosen, exc)
            continue
        except summaries.FAILURES as exc:
            _chapter_failed(db, chosen, exc)
            continue
        put_summary(db, doc_id, chosen["id"], str(value["summary"]).strip(), "auto")
        title = chosen["title"] or str(value.get("title") or "").strip()[:120]
        db.execute(f"UPDATE {DOC_CHAPTERS} SET title = ?, error = '', tries = 0 WHERE id = ?", (title, chosen["id"]))
        api.notify("docs", doc_id=doc_id)


def _chapter_failed(db: Any, chapter: dict[str, Any], exc: Exception) -> None:
    db.execute(f"UPDATE {DOC_CHAPTERS} SET error = ?, tries = tries + 1 WHERE id = ?",
               (summaries.failure_text(exc)[:300], chapter["id"]))


# -- search from a chat -----------------------------------------------------------------------------------


class DocVectorCache:
    """A document's vectors in memory, reloaded when the table changed (count and newest rowid)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[tuple[int, int], dict[str, tuple[str, array]]]] = {}

    def get(self, db: Any, doc_id: str, model: str) -> dict[str, tuple[str, array]]:
        stamp = db.fetchone(f"SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS top FROM {DOC_VECTORS} "
                            "WHERE doc_id = ? AND model = ?", (doc_id, model))
        key = (int(stamp["n"]), int(stamp["top"]))
        cached = self._data.get((doc_id, model))
        if cached is not None and cached[0] == key:
            return cached[1]
        loaded = load_vectors(db, doc_id, model)
        self._data[(doc_id, model)] = (key, loaded)
        return loaded

    def clear(self) -> None:
        self._data.clear()


def has_vectors(db: Any, chat_id: str, model: str) -> bool:
    row = db.fetchone(f"SELECT 1 AS x FROM {DOC_VECTORS} v JOIN {DOC_CHATS} c ON c.doc_id = v.doc_id "
                      "WHERE c.chat_id = ? AND v.model = ? LIMIT 1", (chat_id, model))
    return row is not None


def budget(settings: dict[str, Any], max_context: int) -> int:
    if not max_context:
        return NO_WINDOW_TOKENS
    return max(MIN_TOKENS, int(max_context * float(settings.get("docs_percent") or 0) / 100))


def contribute(api: Any, cache: DocVectorCache, job: Any, scan: list[str]) -> None:
    """The documents attached to the chat, as entries «Из документа …» in the memory spot (see the docstring)."""
    ctx = api.ctx
    db = ctx.db
    chat_id = job.chat["id"]
    prompt = job.prompt_input
    docs = [d for d in attached(db, chat_id)]
    if not docs or float(api.settings.get("docs_percent") or 0) <= 0:
        return
    model = api.vectors_model()
    meaning = getattr(job, "meaning", None)
    queries: list[array] = []
    if model and meaning is not None and meaning.queries and not meaning.error and meaning.model == model:
        queries = [search.unit(q) for q in meaning.queries]
    stems = search.query_stems(scan, [m.name for m in prompt.members])
    threshold = float(character_tree.retrieval_settings(ctx)["threshold"])
    found: list[tuple[float, int, str, dict[str, Any]]] = []  # (score, order, form, data)
    for doc in docs:
        chapters_ = {c["id"]: c for c in chapter_rows(db, doc["id"])}
        sums = summaries_of(db, doc["id"])
        rows = piece_rows(db, doc["id"])
        candidates = [search.Candidate("p:" + p["id"], p["chapter_id"], chapters_[p["chapter_id"]]["number"] * 1000 + p["part"],
                                       p["text_hash"], p["tokens"], tuple(p["keywords"]))
                      for p in rows if p["chapter_id"] in chapters_]
        for chapter_id, summary in sums.items():
            if chapter_id in chapters_:
                candidates.append(search.Candidate("s:" + chapter_id, chapter_id, chapters_[chapter_id]["number"] * 1000 - 1,
                                                   pieces.text_hash(summary["text"]), estimate_tokens(summary["text"]),
                                                   tuple(pieces.keywords(summary["text"]))))
        if not candidates:
            continue
        vectors = cache.get(db, doc["id"], model) if model else {}
        scored = search.score(candidates, vectors, queries, stems, threshold)
        by_chapter: dict[str, dict[str, Any]] = {}
        for item in scored:
            chapter_id = item.candidate.message_id
            slot = by_chapter.setdefault(chapter_id, {"summary": None, "piece": None})
            if item.candidate.piece_id.startswith("s:"):
                slot["summary"] = item
            elif slot["piece"] is None:
                slot["piece"] = item
        by_id = {p["id"]: p for p in rows}
        for chapter_id, slot in by_chapter.items():
            chapter = chapters_[chapter_id]
            s_item, p_item = slot["summary"], slot["piece"]
            s_score = s_item.score if s_item else None
            p_score = p_item.score if p_item else 0.0
            form = "whole" if chapter["whole"] else search.choose_form(s_score if chapter_id in sums else None, p_score)
            found.append((max(p_score, s_score or 0.0), chapter["number"], form,
                          {"doc": doc, "chapter": chapter, "summary": sums.get(chapter_id),
                           "piece": by_id.get(p_item.candidate.piece_id[2:]) if p_item else None,
                           "pieces": [p for p in rows if p["chapter_id"] == chapter_id]}))
    if not found:
        return
    found.sort(key=lambda x: (-x[0], x[1]))
    left = budget(api.settings, job.max_context_tokens)
    chosen = []
    for score, number, form, data in found[:MAX_ENTRIES * 2]:
        text = _entry_text(db, form, data)
        cost = estimate_tokens(text)
        if chosen and cost > left:
            continue
        chosen.append((data["doc"]["title"], number, data["chapter"], text, form))
        left -= cost
        if len(chosen) >= MAX_ENTRIES:
            break
    for title, number, chapter, text, form in sorted(chosen, key=lambda c: (c[0], c[1])):
        name = f"Из документа «{title}», глава {number}" + (f". {chapter['title']}" if chapter["title"] else "")
        prompt.extras.append(Extra(text=f"[{name}]\n{text}", source=f"plugin:memory:doc:{chapter['id']}",
                                   label=name + (" (вся)" if form == "whole" else " (пересказ)" if form == "summary"
                                                 else " (отрывок)"), drop_rank=DROP_RANK))


def _entry_text(db: Any, form: str, data: dict[str, Any]) -> str:
    if form == "whole":
        return prompts.cut_to(chapter_text(db, data["chapter"]), WHOLE_TOKENS)
    if form == "summary" and data["summary"] is not None:
        return data["summary"]["text"].strip()
    rows, best = data["pieces"], data["piece"]
    if best is None:
        return (data["summary"] or {}).get("text", "").strip()
    at = next((i for i, p in enumerate(rows) if p["id"] == best["id"]), 0)
    return "\n".join(p["text"] for p in rows[max(0, at - 1):at + 2])


async def add_from_bytes(api: Any, name: str, data: bytes, title: str = "") -> dict[str, Any]:
    """Read and cut a document (CPU work in a thread), then store it. No model, no money."""
    text = await asyncio.to_thread(read_file, name, data)
    size = max(4, int(api.settings["chapter_size"]))
    sections = await asyncio.to_thread(cut_document, text, size)
    clean = (title or re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name) or "Документ").strip()[:120]
    return create(api.ctx.db, clean, name, text, sections)
