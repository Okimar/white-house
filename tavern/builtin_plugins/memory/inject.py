"""What the memory adds to a reply (`chat.before_generate`), and when it asks for the query vectors.

No model call and no embedding request of its own: it uses the reply's ONE meaning request
(`job.meaning`, services/character_tree.Meaning: the query vectors the bubbles and lorebooks already paid for).
The filter event `meaning.wanted` makes that request happen even for a chat without lorebooks.

Found pieces of messages that no longer fit the window go in as «Ранее в истории (отрывок)». M2 (chapters, no
summaries yet): a piece inside a chapter of this branch comes as «Ранее в истории: Глава N», the best piece with one
neighbour each side (one entry, covers = their range); a chapter with «Вся глава» brings its whole text; pieces of
messages not in any chapter yet are still found one by one. HISTORY FIRST: the plugin only offers candidates, each an `Extra` whose `covers` is
its message; the assembler (core/assembly.py) sends one only when that message was really left out of the prompt.
The live tail (`tail_percent` of the window, at least `tail_min` messages, newest first) is never cut before older
messages: the plugin gives the assembler `tail_from`, and `memory_reserve` = its own budget in tokens.
With no window known (`max_context_tokens` 0) the whole history goes as it always did and nothing is added.
"""

from __future__ import annotations

from array import array
from typing import Any

from tavern.core.assembly import Extra
from tavern.core.prompt import estimate_tokens
from tavern.services import character_tree

from . import chapters, pieces, search, store

MAX_PIECES = 12
SCAN_CHARS = 4000


def tail(history: list[dict[str, Any]], max_context: int, percent: float, minimum: int) -> tuple[str | None, set[str]]:
    """(id of the oldest message of the live tail, ids of the tail). Walks back from the newest message, skipping
    messages hidden from the model, until `percent` of the window and at least `minimum` messages are taken."""
    budget = max_context * percent / 100
    used = 0
    taken: list[str] = []
    for message in reversed(history):
        if message.get("exclude_from_prompt"):
            continue
        if len(taken) >= minimum and used >= budget:
            break
        taken.append(message["id"])
        used += estimate_tokens(message.get("content") or "")
    if not taken:
        return None, set()
    return taken[-1], set(taken)


def scan_texts(history: list[dict[str, Any]], count: int) -> list[str]:
    """The last `count` conversation messages' text, for the keyword part of the query."""
    chosen = [m for m in history if pieces.eligible(m)]
    return [m["content"][-SCAN_CHARS:] for m in chosen[-count:]]


def wants_meaning(api: Any, value: bool = False, chat: dict[str, Any] | None = None) -> bool | None:
    """Filter `meaning.wanted`: yes when memory is on for the chat and some of its pieces have a current vector."""
    if not chat or not store.is_enabled(api.ctx.db, chat["id"]):
        return None
    model = api.vectors_model()
    return True if model and store.vector_count(api.ctx.db, chat["id"], model) > 0 else None


async def contribute(api: Any, cache: store.VectorCache, job: Any) -> None:
    """The `chat.before_generate` handler: tail, candidates, scoring, extras."""
    ctx = api.ctx
    chat_id = job.chat["id"]
    prompt = job.prompt_input
    if prompt is None or not store.is_enabled(ctx.db, chat_id) or not job.max_context_tokens:
        return
    settings = api.settings
    history = list(job.history)
    tail_from, tail_ids = tail(history, job.max_context_tokens, float(settings["tail_percent"]),
                               int(settings["tail_min"]))
    if tail_from is None:
        return
    prompt.tail_from = tail_from
    budget = int(settings["memory_tokens"])
    prompt.memory_reserve = budget
    index = {m["id"]: i for i, m in enumerate(history)}
    candidates = [search.Candidate(p["id"], p["message_id"], index[p["message_id"]], p["hash"], p["tokens"],
                                   tuple(p["keywords"]))
                  for p in store.pieces(ctx.db, chat_id) if p["message_id"] in index and p["message_id"] not in tail_ids]
    if not candidates:
        return
    model = api.vectors_model()
    vectors = cache.get(ctx.db, chat_id, model) if model else {}
    meaning = getattr(job, "meaning", None)
    queries: list[array] = []
    if meaning is not None and meaning.queries and not meaning.error and meaning.model == model:
        queries = [search.unit(q) for q in meaning.queries]
    retrieval = character_tree.retrieval_settings(ctx)
    texts = scan_texts(history, int(retrieval.get("meaning_messages") or 3))
    stems = search.query_stems(texts, [m.name for m in prompt.members])
    scored = search.score(candidates, vectors, queries, stems, float(retrieval["threshold"]))
    used = 0
    # chapters on this branch (ADR 3.1): a found chapter brings its best piece with a neighbour on each side
    placed = [p for p in chapters.placed(store.chapter_rows(ctx.db, chat_id), history) if p.row["state"] != "deleted"]
    numbers = {p.row["id"]: n for n, p in enumerate(placed, 1)}
    by_chapter: dict[str, list[search.Scored]] = {}
    loose: list[search.Scored] = []
    for item in scored:
        home = next((p for p in placed if p.lo <= item.candidate.index <= p.hi), None)
        (by_chapter.setdefault(home.row["id"], []) if home else loose).append(item)
    offers: list[tuple[float, int, str, list[search.Scored], Any]] = []  # score, tokens, kind, pieces, chapter
    for p in placed:
        found = by_chapter.get(p.row["id"])
        if not found:
            continue
        if p.row["whole"]:
            tokens = sum(c.tokens for c in candidates if p.lo <= c.index <= p.hi)
            offers.append((found[0].score, tokens, "whole", found, p))
            continue
        best = found[0]
        near = [c for c in candidates if p.lo <= c.index <= p.hi]
        near.sort(key=lambda c: (c.index, c.piece_id))
        at = next(i for i, c in enumerate(near) if c.piece_id == best.candidate.piece_id)
        group = [search.Scored(c, best.score, 0.0, 0.0) for c in near[max(0, at - 1):at + 2]]
        offers.append((best.score, sum(c.candidate.tokens for c in group), "pieces", group, p))
    for item in loose:
        offers.append((item.score, item.candidate.tokens, "piece", [item], None))
    offers.sort(key=lambda o: -o[0])
    taken: list[tuple[str, list[search.Scored], Any]] = []
    count = 0
    for score, tokens, kind, group, chapter in offers:
        if count >= MAX_PIECES:
            break
        if used + tokens > budget:
            continue
        taken.append((kind, group, chapter))
        used += tokens
        count += len(group)
    taken.sort(key=lambda t: min(g.candidate.index for g in t[1]) if t[0] != "whole" else t[2].lo)
    for kind, group, chapter in taken:
        if kind == "piece":
            add_piece(prompt, history, group[0].candidate)
        elif kind == "pieces":
            add_chapter_pieces(prompt, history, chapter, numbers[chapter.row["id"]], [g.candidate for g in group])
        else:
            add_whole_chapter(prompt, history, chapter, numbers[chapter.row["id"]])


def _label(history: list[dict[str, Any]], index: int) -> str:
    message = history[index]
    return f"№{message['depth'] + 1} {message.get('name') or message['role']}"


def _text(history: list[dict[str, Any]], candidate: search.Candidate) -> str | None:
    message = history[candidate.index]
    spec = {s.hash: s for s in pieces.specs_for(message)}.get(candidate.hash)
    return None if spec is None else spec.text  # None: the message changed since the piece was cut


def _head(placed: Any, number: int) -> str:
    title = placed.row["title"]
    return f"Глава {number}" + (f". {title}" if title else "")


def add_piece(prompt: Any, history: list[dict[str, Any]], candidate: search.Candidate) -> None:
    text = _text(history, candidate)
    if text is None:
        return
    who = _label(history, candidate.index)
    prompt.extras.append(Extra(
        text=f"[Ранее в истории (отрывок), {who}]\n{text}",
        source=f"plugin:memory:piece:{candidate.piece_id}",
        label=f"Ранее в истории (отрывок): {who}",
        covers=(history[candidate.index]["id"], history[candidate.index]["id"])))


def add_chapter_pieces(prompt: Any, history: list[dict[str, Any]], placed: Any, number: int,
                       group: list[search.Candidate]) -> None:
    """The found pieces of a chapter (best one and its neighbours) as one entry: «Ранее в истории: Глава N»."""
    parts = [(c, _text(history, c)) for c in group]
    parts = [(c, t) for c, t in parts if t is not None]
    if not parts:
        return
    body = "\n".join(f"{_label(history, c.index)}: {t}" for c, t in parts)
    head = _head(placed, number)
    first, last = history[parts[0][0].index]["id"], history[parts[-1][0].index]["id"]
    prompt.extras.append(Extra(
        text=f"[Ранее в истории: {head}]\n{body}", source=f"plugin:memory:chapter:{placed.row['id']}",
        label=f"Ранее в истории: {head}", covers=(first, last)))


def add_whole_chapter(prompt: Any, history: list[dict[str, Any]], placed: Any, number: int) -> None:
    """«Всю главу»: the full text of the chapter's messages."""
    lines = []
    for m in history[placed.lo:placed.hi + 1]:
        if pieces.eligible(m):
            lines.append(f"{m.get('name') or m['role']}: {m['content']}")
    head = _head(placed, number)
    prompt.extras.append(Extra(
        text=f"[Ранее в истории: {head}]\n" + "\n".join(lines), source=f"plugin:memory:chapter:{placed.row['id']}",
        label=f"Ранее в истории: {head} (вся)", covers=(history[placed.lo]["id"], history[placed.hi]["id"])))
