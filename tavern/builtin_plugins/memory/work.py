"""The memory's background job for one chat (core/background.py): keep pieces right, vectorize, cut chapters.

Todo-driven (ADR 0019 section 4.2): every pass asks the tables "what is next?", so a restart, a crash, a
rate limit or a user's request that cancelled a step loses nothing.

  1. Check: cut the branch's messages into pieces and make the table match (new, changed, gone); chapters whose
     text changed under them are marked stale. No model, no network.
  2. Vectorize: pieces of the branch without a current vector, EXCEPT those of the latest message of each speaker
     (owner's rule). One batch per step, `job.step(...)`, so a reply or an assistant request cancels it at once and
     it is redone afterwards (never a request beside a reply). A 429 is waited out with `job.rate_wait`.
     Without an embedding connection nothing is vectorized (keywords and chapters still work).
  3. Chapters (M2): the part of the branch after the last chapter ("the open part") is looked at by `borders.py`;
     when it is longer than N messages and a good border is in it (or it is longer than 3N), a chapter closes.
     One chapter per step (also a step of `job.step`: never beside a reply), then the pass starts again, so a long
     old chat is cut chapter by chapter. With «Границы глав определяет модель» on, a small model has the last word
     (one request per step); a refused or failed request falls back to the script and is noted.
"""

from __future__ import annotations

import logging
from array import array
from typing import Any

from tavern.core.llm import StructuredOutputError
from tavern.errors import AppError, NotFound
from tavern.providers.base import ProviderError
from tavern.services import cast, embeddings, lorebooks

from . import borders, chapters, pieces, store

log = logging.getLogger(__name__)

JUDGE_QUIET = 4  # messages that must arrive before the model is asked again about the same open part


def todo_list(db: Any, chat_id: str, path: list[dict[str, Any]], model: str) -> tuple[list[tuple[str, str, str]], int]:
    """(pieces needing a vector as (piece id, text, hash), how many pieces are to be vectorized in all)."""
    latest = pieces.latest_ids(path)
    rows = {(p["message_id"], p["part"]): p for p in store.pieces(db, chat_id)}
    stored = store.current_vector_ids(db, chat_id, model)
    todo: list[tuple[str, str, str]] = []
    total = 0
    for spec in pieces.specs([m for m in path if m["id"] not in latest]):
        piece = rows.get((spec.message_id, spec.part))
        if piece is None:
            continue
        total += 1
        if stored.get(piece["id"]) != spec.hash:
            todo.append((piece["id"], spec.text, spec.hash))
    return todo, total


def message_vectors(db: Any, cache: store.VectorCache, chat_id: str, model: str,
                    window: list[dict[str, Any]]) -> list[array | None]:
    """One vector per message: the mean of its pieces' current vectors (None where there is none)."""
    if not model:
        return [None] * len(window)
    vectors = cache.get(db, chat_id, model)
    by_message: dict[str, list[array]] = {}
    for piece in store.pieces(db, chat_id):
        stored = vectors.get(piece["id"])
        if stored is not None and stored[0] == piece["hash"]:
            by_message.setdefault(piece["message_id"], []).append(stored[1])
    out: list[array | None] = []
    for message in window:
        found = by_message.get(message["id"])
        out.append(borders._mean_vector(list(found)) if found else None)  # type: ignore[arg-type]
    return out


def known_names(api: Any, chat_id: str) -> frozenset[str]:
    """Names that tell who is on stage: the chat's members and the entries of the lorebooks working in the chat."""
    ctx = api.ctx
    members = ctx.members.list(chat_id)
    names = [m["name"] for m in members]
    try:
        book_ids = lorebooks.active_books(ctx, chat_id, [m["character_id"] for m in members])
        if book_ids:
            names.extend(b["title"] for blocks in ctx.blocks.for_owners(book_ids).values() for b in blocks
                         if b.get("title"))
    except NotFound:
        pass
    return borders.known_stems(names)


async def judge(api: Any, chat_id: str, window: list[dict[str, Any]], gaps: list[borders.Gap], size: int,
                job: Any) -> tuple[str, borders.Cut | None, str]:
    """Ask the small model whether the first scene of the open part is over. Returns (verdict, cut, note):
    "cut" (the model found the end), "no" (not finished, or asked recently), "error" (fall back to the script)."""
    storage = api.storage(chat_id)
    asked = storage.get("judge") or {}
    if asked.get("first") == window[0]["id"] and len(window) - int(asked.get("count", 0)) < JUDGE_QUIET:
        return "no", None, ""
    settings = api.settings
    hints = borders.hint_gaps(gaps, borders.min_length(size), len(window) - 1)
    messages = borders.judge_messages(window, hints)
    try:
        llm = api.llm(connection_id=str(settings.get("border_connection") or ""),
                      model=str(settings.get("border_model_name") or "").strip(), chat_id=chat_id)
        value = await job.step(lambda: llm.generate_json(messages, schema=borders.JUDGE_SCHEMA))
    except (AppError, ProviderError, StructuredOutputError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        return "error", None, f"модель границ не ответила ({message}): режу по правилам"
    storage.set("judge", {"first": window[0]["id"], "count": len(window)})
    cut = borders.judge_answer(value, window, size)
    return ("cut", cut, "") if cut else ("no", None, "")


async def close_chapter(api: Any, chat_id: str, path: list[dict[str, Any]], cache: store.VectorCache, job: Any,
                        model: str) -> tuple[bool, str]:
    """Step 3: close the next chapter of the open part when it should close. Returns (made one, a note)."""
    ctx = api.ctx
    size = max(4, int(api.settings["chapter_size"]))
    rows = chapters.placed(store.chapter_rows(ctx.db, chat_id), path)
    start = chapters.open_start(rows)
    open_part = [m for m in path[start:] if pieces.eligible(m)]
    if len(open_part) <= size:
        return False, ""
    window = open_part[:3 * size + borders.WINDOW]
    note = ""

    async def score() -> tuple[list[borders.Gap], borders.Cut | None]:
        gaps = borders.score_gaps(window, known=known_names(api, chat_id),
                                  vectors=message_vectors(ctx.db, cache, chat_id, model, window))
        return gaps, borders.choose(gaps, len(open_part), size)

    gaps, script = await job.step(score)
    cut, by = script, "script"
    if api.settings.get("border_judge"):
        verdict, judged, note = await judge(api, chat_id, window, gaps, size, job)
        if verdict == "cut":
            cut, by = judged, "model"
        elif verdict == "no":
            cut = script if script is not None and script.forced else None
    if cut is None:
        return False, note
    index = chapters.index_of(path)
    first, last = window[0], window[cut.index - 1]
    span = path[index[first["id"]]:index[last["id"]] + 1]
    me = cast.me(ctx, chat_id)
    row = store.new_chapter(chat_id, first["id"], last["id"], chapters.content_hash(span),
                            chapters.participants(span, me["character_id"] if me else ""), by=by,
                            reason=cut.reason)
    store.put_chapters(ctx.db, [row])
    return True, note


async def run(api: Any, chat_id: str, job: Any, cache: store.VectorCache | None = None) -> None:
    """The job's `work`: see the module docstring."""
    ctx = api.ctx
    cache = cache or store.VectorCache()
    note = ""
    while not job.stopped:
        if not store.is_enabled(ctx.db, chat_id):
            return
        try:
            ctx.chats.get_chat(chat_id)
        except NotFound:
            return
        path = ctx.chats.active_path(chat_id)
        store.sync(ctx.db, chat_id, pieces.specs(path), {m["id"] for m in path})
        chapters.refresh(ctx.db, chat_id, path)
        model = api.vectors_model()
        total, todo = 0, []
        if model:
            todo, total = todo_list(ctx.db, chat_id, path, model)
        if todo:
            batch = embeddings.batches(todo)[0]
            texts = [text for _, text, _ in batch]
            try:
                vectors = await job.step(lambda: api.embed(texts, chat_id=chat_id))
            except ProviderError as exc:
                if exc.upstream_status != 429:
                    raise
                await job.rate_wait(embeddings.RATE_WAIT)
                continue
            if len(vectors) != len(batch):
                raise ProviderError("The service returned fewer vectors than texts", reason="request")
            store.save_vectors(ctx.db, chat_id, model, [(piece_id, digest, vector)
                                                        for (piece_id, _, digest), vector in zip(batch, vectors)])
            job.progress(total - len(todo) + len(batch), total, "")
            continue
        made, step_note = await close_chapter(api, chat_id, path, cache, job, model)
        note = step_note or note
        if made:
            job.progress(total, total, "главы")
            continue
        job.progress(total, total, note if note else ("" if model else "нет подключения для векторов"))
        return
