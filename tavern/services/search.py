"""Search across all chats (ordinary and the assistant's), plain and by the model (stage 11).

Plain: the words of the query are found by their stems like bubble keywords (core/retrieval.keyword_pattern:
Russian endings are ignored, case does not matter), every word must be in the message; "in quotes" = that
exact phrase. The text is read from the database by a small SQL function, not an index: FTS5 exists in
this SQLite but Termux's Python build is not promised to have it, and an index needs triggers on every
write that would break all saving if a copy of the data is opened by a build without it. One pass over
50 000 messages takes well under a second (measured in tests/integration/test_search.py).

Which messages: the text of the shown version of each message that is not hidden. Another branch of a
chat is searched too (marked, a tap switches to it, as the in-chat search does); a deleted branch is gone
from the database, so it is never found.

By the model: the question is turned into search words (1 request), the messages found by any of those
words become candidates, the model judges them in batches (up to MAX_BATCHES requests) and names the
numbers that fit with a reason. The numbers are looked up in OUR list: an invented number is dropped, and
every kept message is read from the database again before it is shown. Spending is recorded (purpose
"search").
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, NamedTuple

from tavern.context import AppContext
from tavern.core.llm import LLMClient
from tavern.core.retrieval import keyword_pattern, stem
from tavern.errors import AppError, Conflict
from tavern.providers.base import ChatMessage, ProviderError
from tavern.services import spending

log = logging.getLogger(__name__)

SCOPES = ("chats", "assistant")
PAGE = 30
MAX_PAGE = 100
MAX_WORDS = 8
MAX_QUERY = 300
CONTEXT_BEFORE = 70
CONTEXT_AFTER = 110
# By the model
CANDIDATES = 60
BATCH = 20
MAX_BATCHES = CANDIDATES // BATCH
MAX_REQUESTS = 1 + MAX_BATCHES
CANDIDATE_CHARS = 450
MAX_REASON = 240
MAX_FOUND = 15
# Assistant bookkeeping lines are not conversation: nobody searches them
FILLER = {"где", "что", "как", "это", "был", "была", "были", "было", "чат", "чате", "про", "когда", "там", "тут",
          "нас", "мне", "меня", "его", "она", "они", "все", "для", "или", "что-то", "какой", "которой", "который"}
HIDDEN_KINDS = ("progress", "step", "critic", "skill", "remembered")


class BadSearch(AppError):
    code = "bad_search"


# --- the query ---------------------------------------------------------------------------------


class Term(NamedTuple):
    """One thing to find. `needle` is a plain lowercase piece the text must contain (checked first, it is
    much cheaper than the pattern); `pattern` decides."""

    needle: str
    pattern: re.Pattern[str]


def _patterns(query: Any) -> list[Term]:
    """The query as terms that must all match. Quoted text is one phrase, other words are separate."""
    text = query.strip() if isinstance(query, str) else ""
    if len(text) < 2:
        raise BadSearch("Напишите хотя бы два символа")
    if len(text) > MAX_QUERY:
        raise BadSearch("Запрос слишком длинный")
    found: list[Term] = []
    for phrase, loose in re.findall(r'"([^"]+)"|([^\s"]+)', text):
        pattern = keyword_pattern(phrase, whole=True) if phrase else None
        if pattern is None:
            for word in re.findall(r"\w+", loose):
                pattern = keyword_pattern(word)
                if pattern is not None:
                    found.append(Term(stem(word), pattern))
            continue
        found.append(Term(re.findall(r"\w+", phrase.lower())[0], pattern))
    if not found:
        raise BadSearch("В запросе нет слов для поиска")
    return found[:MAX_WORDS]


def _day(value: Any, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise BadSearch(f"Дата «{name}» должна быть в виде ГГГГ-ММ-ДД") from None


@dataclass
class Filters:
    character: str = ""
    since: date | None = None
    until: date | None = None
    group: str = ""
    chat: str = ""

    @classmethod
    def make(cls, raw: dict[str, Any]) -> Filters:
        since, until = _day(raw.get("since"), "с"), _day(raw.get("until"), "по")
        if since and until and since > until:
            raise BadSearch("Дата «с» позже даты «по»")
        return cls(character=str(raw.get("character") or ""), since=since, until=until,
                   group=str(raw.get("group") or ""), chat=str(raw.get("chat") or ""))

    def sql(self, scope: str, time_col: str, chat_col: str) -> tuple[str, list[Any]]:
        parts: list[str] = []
        args: list[Any] = []
        if self.since:
            parts.append(f"{time_col} >= ?")
            args.append(self.since.isoformat())
        if self.until:
            parts.append(f"{time_col} < ?")
            args.append((self.until + timedelta(days=1)).isoformat())
        if self.chat:
            parts.append(f"{chat_col} = ?")
            args.append(self.chat)
        if scope == "chats":
            if self.character:
                parts.append("c.character_id = ?")
                args.append(self.character)
            if self.group == "none":
                parts.append("c.id NOT IN (SELECT item_id FROM group_members WHERE kind = 'chat')")
            elif self.group:
                parts.append("c.id IN (SELECT item_id FROM group_members WHERE kind = 'chat' AND group_id = ?)")
                args.append(self.group)
        return "".join(f" AND {p}" for p in parts), args


# --- reading -----------------------------------------------------------------------------------

_CHAT_FROM = """
FROM messages m
JOIN swipes s ON s.message_id = m.id AND s.idx = m.active_swipe
JOIN chats c ON c.id = m.chat_id
LEFT JOIN characters ch ON ch.id = c.character_id
"""
_CHAT_COLS = """m.id, m.chat_id, m.depth, m.role, m.name, m.created_at, c.title, c.leaf_id, c.character_id,
       COALESCE(ch.name, '') AS character_name, s.content AS text"""
_ASSISTANT_FROM = """
FROM assistant_messages am
JOIN assistant_chats ac ON ac.id = am.chat_id
"""
_ASSISTANT_COLS = "am.id, am.chat_id, am.role, am.created_at, ac.title, am.text"


def _u16(text: str, index: int) -> int:
    """A position in the browser's string (UTF-16 units) for a position in ours (code points)."""
    return len(text[:index].encode("utf-16-le")) // 2


def snippet(text: str, patterns: list[re.Pattern[str]]) -> dict[str, Any]:
    """A piece of text around the first match: {text, marks: [[from, to], ...], before, after}. Marks are
    in the browser's string positions; newlines become spaces so the piece is one line."""
    spots = sorted((m.start(), m.end()) for p in patterns for m in p.finditer(text[:200_000]) if m.end() > m.start())
    first = spots[0][0] if spots else 0
    start = max(0, first - CONTEXT_BEFORE)
    end = min(len(text), first + CONTEXT_AFTER)
    if start:  # do not begin in the middle of a word
        while start < first and text[start].isalnum() and text[start - 1].isalnum():
            start += 1
    flat = re.sub(r"\s", " ", text[start:end])
    marks: list[list[int]] = []
    for a, b in spots:
        a, b = max(a, start) - start, min(b, end) - start
        if b <= a:
            continue
        if marks and a <= marks[-1][1]:
            marks[-1][1] = max(marks[-1][1], b)
        else:
            marks.append([a, b])
    return {"text": flat, "before": start > 0, "after": end < len(text),
            "marks": [[_u16(flat, a), _u16(flat, b)] for a, b in marks]}


def _scan(ctx: AppContext, scope: str, test: Any, filters: Filters, *, limit: int, offset: int) -> list[Any]:
    """Rows whose text passes `test` (a Python function the database calls per message), the latest added
    first (rowid order: the database walks back from the newest and stops once the page is full, no sorting
    of everything). A message edited later keeps its place."""
    db = ctx.db
    if scope == "chats":
        extra, args = filters.sql(scope, "m.created_at", "m.chat_id")
        sql = (f"SELECT {_CHAT_COLS} {_CHAT_FROM} WHERE m.hidden = 0 AND tv_test(s.content){extra} "
               "ORDER BY m.rowid DESC LIMIT ? OFFSET ?")
    else:
        extra, args = filters.sql(scope, "am.created_at", "am.chat_id")
        hidden = "".join(f" AND am.data NOT LIKE '%\"kind\": \"{k}\"%'" for k in HIDDEN_KINDS)
        sql = (f"SELECT {_ASSISTANT_COLS} {_ASSISTANT_FROM} WHERE am.role IN ('user', 'assistant') AND am.text != '' "
               f"AND tv_test(am.text){hidden}{extra} ORDER BY am.id DESC LIMIT ? OFFSET ?")
    with db._lock:  # the function is named on the one shared connection: nobody else may swap it meanwhile
        db.connection.create_function("tv_test", 1, test, deterministic=True)
        try:
            return db.connection.execute(sql, [*args, limit, offset]).fetchall()
        finally:
            db.connection.create_function("tv_test", 1, None)


def _matcher(terms: list[Term]) -> Any:
    def test(text: str | None) -> int:
        if not text:
            return 0
        low = text.lower()
        return 1 if all(t.needle in low for t in terms) and all(t.pattern.search(text) for t in terms) else 0
    return test


def _on_path(ctx: AppContext, rows: list[Any]) -> set[str]:
    """Ids among the rows that sit on their chat's shown branch (walked up from the chat's tip)."""
    shallow: dict[str, tuple[str, int]] = {}
    for row in rows:
        leaf, depth = shallow.get(row["chat_id"], (row["leaf_id"], row["depth"]))
        shallow[row["chat_id"]] = (leaf, min(depth, row["depth"]))
    wanted = {row["id"] for row in rows}
    found: set[str] = set()
    for leaf, depth in shallow.values():
        if not leaf:
            continue
        for walked in ctx.db.fetchall(
            "WITH RECURSIVE p(id, parent_id, depth) AS ("
            " SELECT id, parent_id, depth FROM messages WHERE id = ?"
            " UNION ALL SELECT m.id, m.parent_id, m.depth FROM messages m JOIN p ON m.id = p.parent_id"
            " WHERE m.depth >= ?) SELECT id FROM p", (leaf, depth),
        ):
            if walked["id"] in wanted:
                found.add(walked["id"])
    return found


def _hit(scope: str, row: Any, patterns: list[re.Pattern[str]], on_path: set[str]) -> dict[str, Any]:
    hit: dict[str, Any] = {
        "scope": scope, "id": row["id"], "chat_id": row["chat_id"], "title": row["title"],
        "role": row["role"], "date": row["created_at"], "snippet": snippet(row["text"], patterns),
    }
    if scope == "chats":
        hit.update(depth=row["depth"], name=row["name"], character_id=row["character_id"] or "",
                   character=row["character_name"], on_path=row["id"] in on_path)
    return hit


def search(ctx: AppContext, scope: Any, query: Any, raw_filters: dict[str, Any] | None = None,
           offset: Any = 0, limit: Any = PAGE) -> dict[str, Any]:
    """One page of plain results, newest first: {hits, has_more, offset}."""
    if scope not in SCOPES:
        raise BadSearch("Искать можно в «chats» или «assistant»")
    terms = _patterns(query)
    patterns = [t.pattern for t in terms]
    filters = Filters.make(raw_filters or {})
    try:
        offset, limit = max(0, int(offset)), max(1, min(MAX_PAGE, int(limit)))
    except (TypeError, ValueError):
        raise BadSearch("offset и limit должны быть числами") from None
    rows = _scan(ctx, scope, _matcher(terms), filters, limit=limit + 1, offset=offset)
    more, rows = len(rows) > limit, rows[:limit]
    on_path = _on_path(ctx, rows) if scope == "chats" else set()
    return {"hits": [_hit(scope, r, patterns, on_path) for r in rows], "has_more": more, "offset": offset}


# --- by the model ------------------------------------------------------------------------------

_ASKS: dict[str, asyncio.Task[Any]] = {}

WORDS_PROMPT = (
    "A person looks for something in their old roleplay chats and describes it:\n\n{question}\n\n"
    "Give up to 10 short words (single words, no phrases) that the wanted messages probably contain: names, "
    "objects, actions, in the same language as the description, plus close synonyms. Skip filler words.")
JUDGE_PROMPT = (
    "A person looks for chat messages and describes them:\n\n{question}\n\n"
    "Below are numbered messages (the chat's title, the author, a piece of text). Name the numbers of the "
    "messages that really fit the description, each with a short reason in the language of the description. "
    "Name none if none fit; never invent numbers.\n\n{items}")
WORDS_SCHEMA = {"type": "object", "properties": {"words": {"type": "array", "items": {"type": "string"}}},
                "required": ["words"]}
# Only the shape is demanded; every number and reason is checked in code (a wrong one is dropped, the
# whole answer is not thrown away).
JUDGE_SCHEMA = {"type": "object", "properties": {"matches": {"type": "array", "items": {"type": "object"}}},
                "required": ["matches"]}


def info(ctx: AppContext) -> dict[str, Any]:
    """What «Спросить нейронку» would do: how many requests at most and with which model. No keys."""
    model = None
    try:
        from tavern.services.chat import resolve_connection
        connection = resolve_connection(ctx, {"meta": {}})
        model = {"connection": connection.get("name", ""), "model": connection.get("model", "")}
    except Conflict:
        pass
    return {"max_requests": MAX_REQUESTS, "candidates": CANDIDATES, "model": model}


def _words_from(question: str, model_words: Any) -> list[str]:
    """Search words: the model's, then the question's own. Short ones and repeats are dropped."""
    seen: dict[str, str] = {}
    own = re.findall(r"\w+", question)
    for word in [*(model_words if isinstance(model_words, list) else []), *own]:
        for part in re.findall(r"\w+", str(word)):
            if len(part) >= 3 and part.lower() not in FILLER and part.lower() not in seen:
                seen[part.lower()] = part
    return list(seen.values())[:20]


def _candidates(ctx: AppContext, scope: str, words: list[str], filters: Filters) -> list[tuple[Any, int]]:
    """Messages with any of the words, best (most different words) first, at most CANDIDATES."""
    patterns = [p for w in words if (p := keyword_pattern(w)) is not None]
    if not patterns:
        return []

    def test(text: str | None) -> int:
        return 1 if text and any(p.search(text) for p in patterns) else 0

    rows = _scan(ctx, scope, test, filters, limit=5000, offset=0)
    ranked = []
    for row in rows:
        score = sum(1 for p in patterns if p.search(row["text"]))
        ranked.append((row, score))
    ranked.sort(key=lambda pair: -pair[1])  # stable: newer first among equals
    return ranked[:CANDIDATES]


def _describe(scope: str, number: int, row: Any, patterns: list[re.Pattern[str]]) -> str:
    text = row["text"]
    first = min((m.start() for p in patterns for m in [p.search(text)] if m), default=0)
    start = max(0, first - CANDIDATE_CHARS // 3)
    piece = re.sub(r"\s+", " ", text[start:start + CANDIDATE_CHARS])
    who = row["name"] or row["role"] if scope == "chats" else row["role"]
    return f"[{number}] chat «{row['title'][:60]}», {who}: {piece}"


def _verified(ctx: AppContext, scope: str, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Read every kept message from the database again; what is gone is dropped."""
    if not found:
        return []
    table, col = ("messages", "id") if scope == "chats" else ("assistant_messages", "id")
    ids = [h["id"] for h in found]
    marks = ",".join("?" * len(ids))
    alive = {r[col] for r in ctx.db.fetchall(f"SELECT {col} FROM {table} WHERE {col} IN ({marks})", ids)}
    return [h for h in found if h["id"] in alive]


async def ask(ctx: AppContext, scope: Any, question: Any, raw_filters: dict[str, Any] | None = None,
              token: str = "") -> dict[str, Any]:
    """Run the model search. With a `token`, stop(token) cancels it and what was already verified is returned."""
    if scope not in SCOPES:
        raise BadSearch("Искать можно в «chats» или «assistant»")
    question = question.strip() if isinstance(question, str) else ""
    if len(question) < 5:
        raise BadSearch("Опишите словами, что ищете (хотя бы несколько слов)")
    if len(question) > 600:
        raise BadSearch("Описание слишком длинное")
    filters = Filters.make(raw_filters or {})
    spending.guard(ctx)
    from tavern.services.chat import resolve_connection
    connection = resolve_connection(ctx, {"meta": {}})
    state: dict[str, Any] = {"found": [], "requests": 0, "candidates": 0, "words": []}
    task = asyncio.ensure_future(_ask(ctx, scope, question, filters, connection, state))
    if token:
        _ASKS[token] = task
    try:
        await task
        stopped = False
    except asyncio.CancelledError:
        if not task.cancelled():
            raise  # the caller itself was cancelled
        stopped = True
    except ProviderError as exc:
        raise Conflict(f"Модель не ответила: {exc}", code="model_failed") from exc
    finally:
        if token:
            _ASKS.pop(token, None)
    found = _verified(ctx, scope, state["found"])
    return {"hits": found, "stopped": stopped, "requests": state["requests"], "candidates": state["candidates"],
            "words": state["words"]}


async def _ask(ctx: AppContext, scope: str, question: str, filters: Filters, connection: dict[str, Any],
               state: dict[str, Any]) -> None:
    provider = ctx.providers.create(connection)
    try:
        llm = LLMClient(provider, model=connection["model"], meter=spending.meter(ctx, connection, purpose="search"))
        model_words: Any = []
        try:
            state["requests"] += 1
            value = await llm.generate_json([ChatMessage("user", WORDS_PROMPT.format(question=question))],
                                            schema=WORDS_SCHEMA)
            model_words = value.get("words") if isinstance(value, dict) else []
        except ProviderError as exc:
            if getattr(exc, "reason", "") in ("auth", "network", "limit", "server"):
                raise
            log.warning("Search words by the model failed: %s", exc)  # the question's own words go on
        words = _words_from(question, model_words)
        state["words"] = words
        ranked = await asyncio.to_thread(_candidates, ctx, scope, words, filters)
        state["candidates"] = len(ranked)
        if not ranked:
            return
        patterns = [p for w in words if (p := keyword_pattern(w)) is not None]
        for start in range(0, len(ranked), BATCH):
            batch = ranked[start:start + BATCH]
            numbered = {i + 1: row for i, (row, _) in enumerate(batch)}
            items = "\n".join(_describe(scope, n, row, patterns) for n, row in numbered.items())
            state["requests"] += 1
            value = await llm.generate_json(
                [ChatMessage("user", JUDGE_PROMPT.format(question=question, items=items))], schema=JUDGE_SCHEMA)
            taken = {h["id"] for h in state["found"]}
            for match in (value.get("matches") if isinstance(value, dict) else None) or []:
                n = match.get("n") if isinstance(match, dict) else None
                row = numbered.get(n) if isinstance(n, int) and not isinstance(n, bool) else None
                if row is None or row["id"] in taken:
                    continue  # a number we never gave: dropped
                taken.add(row["id"])
                hit = _hit(scope, row, patterns, _on_path(ctx, [row]) if scope == "chats" else set())
                hit["reason"] = str(match.get("reason") or "").strip()[:MAX_REASON]
                state["found"].append(hit)
            if len(state["found"]) >= MAX_FOUND:
                break
    finally:
        await provider.aclose()


def stop(token: str) -> bool:
    """Cancel a running model search; True when there was one."""
    task = _ASKS.get(token)
    if task is None or task.done():
        return False
    task.cancel()
    return True
