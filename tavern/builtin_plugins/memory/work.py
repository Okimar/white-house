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
  4. Summaries (M3): the next chapter that needs one (`summaries.next_chapter`: raw / failed after the backoff /
     stale with the automatic checkbox / asked for by the button, never a locked one) gets one request through
     `job.step`; its summary and a title are kept after the request returned. A failed request marks the chapter and
     the job goes on; the plugin wakes the job again after the backoff (`run` returns the seconds).
  5. «Сюжет до сих пор» (M3): built from the summaries of the branch when it is missing or out of date.
  6. «Развитие» and «Сейчас:» lines (M4, `layers.step`): the next closed chapter of the branch in story order (after
     its summary, when summaries are on) gets one request through `job.step`: each participant's layer «how they are
     now» and a line for the lorebook bubbles the chapter mentions. A failure is retried after a backoff.
  Summaries get their own vectors (`s:<chapter id>`) in step 2, so a summary is found by meaning too.
Model requests go through ONE client per (connection, model) per run, made with `background=True` (ADR 0019 point H):
«спрашивать каждый раз» asks once for the run, and with no page open the run waits («ждёт разрешения»).
"""

from __future__ import annotations

import asyncio
import json
import logging
from array import array
from datetime import datetime, timezone
from typing import Any

from tavern.core.llm import StructuredOutputError
from tavern.errors import AppError, NotFound
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ProviderError
from tavern.services import cast, embeddings, lorebooks
from tavern.util.ids import now_iso

from . import borders, chapters, layers, pieces, store, summaries

log = logging.getLogger(__name__)

JUDGE_QUIET = 4  # messages that must arrive before the model is asked again about the same open part


def summary_items(db: Any, chat_id: str, path: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """The summaries of the branch's chapters as (vector id «s:<chapter id>», text, hash): they get vectors too."""
    sums = store.current_summaries(db, chat_id)
    rows = chapters.placed(store.chapter_rows(db, chat_id), path)
    return [("s:" + p.row["id"], sums[p.row["id"]]["text"], pieces.text_hash(sums[p.row["id"]]["text"]))
            for p in rows if p.row["state"] != "deleted" and p.row["id"] in sums]


def todo_list(db: Any, chat_id: str, path: list[dict[str, Any]], model: str) -> tuple[list[tuple[str, str, str]], int]:
    """(items needing a vector as (id, text, hash), how many items are to be vectorized in all): the pieces of the
    branch, except each speaker's latest message, and the summaries of its chapters."""
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
    for item_id, text, digest in summary_items(db, chat_id, path):
        total += 1
        if stored.get(item_id) != digest:
            todo.append((item_id, text, digest))
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


class Clients:
    """The run's model clients, one per (connection, model), made on first use with `background=True` (so the
    «may it?» question is asked once for the run) and a note «ждёт разрешения» while no page is open."""

    def __init__(self, api: Any, chat_id: str, job: Any) -> None:
        self.api, self.chat_id, self.job = api, chat_id, job
        self._made: dict[tuple[str, str], Any] = {}

    def which(self, role: str) -> tuple[str, str]:
        """(connection id, model) of a role: its own settings, else the plugin's «Модель для памяти», else the chat's."""
        settings = self.api.settings
        for prefix in (role, "memory"):
            connection = str(settings.get(f"{prefix}_connection") or "")
            model = str(settings.get(f"{prefix}_model_name") or "").strip()
            if connection or model:
                return connection, model
        return "", ""

    def get(self, role: str) -> Any:
        key = self.which(role)
        if key not in self._made:
            def waiting(on: bool) -> None:
                self.job.progress(0, 0, "ждёт разрешения" if on else "")

            self._made[key] = self.api.llm(connection_id=key[0], model=key[1], chat_id=self.chat_id,
                                           background=True, on_wait=waiting)
        return self._made[key]


async def judge(api: Any, chat_id: str, window: list[dict[str, Any]], gaps: list[borders.Gap], size: int,
                job: Any, clients: Clients | None = None) -> tuple[str, borders.Cut | None, str]:
    """Ask the small model whether the first scene of the open part is over. Returns (verdict, cut, note):
    "cut" (the model found the end), "no" (not finished, or asked recently), "error" (fall back to the script)."""
    storage = api.storage(chat_id)
    asked = storage.get("judge") or {}
    if asked.get("first") == window[0]["id"] and len(window) - int(asked.get("count", 0)) < JUDGE_QUIET:
        return "no", None, ""
    hints = borders.hint_gaps(gaps, borders.min_length(size), len(window) - 1)
    messages = borders.judge_messages(window, hints)
    try:
        llm = (clients or Clients(api, chat_id, job)).get("border")
        value = await job.step(lambda: llm.generate_json(messages, schema=borders.JUDGE_SCHEMA))
    except (AppError, ProviderError, StructuredOutputError) as exc:
        message = getattr(exc, "message", None) or str(exc)
        return "error", None, f"модель границ не ответила ({message}): режу по правилам"
    storage.set("judge", {"first": window[0]["id"], "count": len(window)})
    cut = borders.judge_answer(value, window, size)
    return ("cut", cut, "") if cut else ("no", None, "")


async def close_chapter(api: Any, chat_id: str, path: list[dict[str, Any]], cache: store.VectorCache, job: Any,
                        model: str, clients: Clients | None = None) -> tuple[bool, str]:
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
        verdict, judged, note = await judge(api, chat_id, window, gaps, size, job, clients)
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def summary_step(api: Any, chat_id: str, path: list[dict[str, Any]], job: Any, clients: Clients,
                       ) -> tuple[str, float | None, str]:
    """Step 4: summarize the next chapter that needs it. Returns (what happened, seconds until a failed chapter may
    be retried, a note): "done" (one chapter handled, ask again), "idle" (nothing to do), "blocked" (no access to the
    model or no connection: stop for now, nothing marked failed), "rate" (a 429 was waited out, ask again)."""
    ctx = api.ctx
    settings = api.settings
    storage = api.storage(chat_id)
    forced = set(storage.get("force", []))
    rows = chapters.placed(store.chapter_rows(ctx.db, chat_id), path)
    sums = store.current_summaries(ctx.db, chat_id)
    live = [p for p in rows if p.row["state"] != "deleted"]
    job.progress(sum(1 for p in live if p.row["id"] in sums), len(live), "пересказы")
    chosen, wait = summaries.next_chapter(rows, sums, auto=bool(settings.get("summaries")),
                                          auto_stale=bool(settings.get("auto_resummarize")), forced=forced,
                                          now=_now())
    if chosen is None:
        if forced:
            storage.set("force", [])  # the asked-for chapters are locked or gone: nothing more to wait for
        return "idle", wait, ""
    row = chosen.row
    digest = chapters.content_hash(path[chosen.lo:chosen.hi + 1])
    previous = summaries.previous_summary(rows, sums, chosen)
    target = int(settings["summary_tokens"])
    try:
        client = clients.get("summary")
        result = await job.step(lambda: summaries.summarize(client, path, chosen, previous, target))
    except PluginRefused as exc:
        return "blocked", wait, exc.message
    except ProviderError as exc:  # before AppError: it is one
        if exc.upstream_status == 429:
            await job.rate_wait(embeddings.RATE_WAIT)
            return "rate", wait, ""
        return _failed(ctx, storage, forced, row, exc, wait)
    except AppError as exc:
        if exc.code == "no_connection":
            return "blocked", wait, "нет подключения к модели для пересказов"
        return _failed(ctx, storage, forced, row, exc, wait)
    store.put_summary(ctx.db, chat_id, row["id"], result["summary"], digest, by="auto")
    title = row["title"] or result["title"][:120]  # the model names a chapter only when it has no name yet
    store.put_chapters(ctx.db, [{**row, "title": title, "content_hash": digest, "state": "summarized", "tries": 0,
                                 "error": "", "failed_at": "", "updated_at": now_iso()}])
    if row["id"] in forced:
        storage.set("force", [i for i in forced if i != row["id"]])
    api.notify("chapters", chat_id=chat_id)
    return "done", wait, ""


def _failed(ctx: Any, storage: Any, forced: set[str], row: dict[str, Any], exc: Exception,
            wait: float | None) -> tuple[str, float | None, str]:
    """A summary request failed (or was refused twice): the chapter stays raw with a mark, retried after the backoff."""
    store.mark_failed(ctx.db, row["id"], summaries.failure_text(exc))
    if row["id"] in forced:
        storage.set("force", [i for i in forced if i != row["id"]])
    left = summaries.retry_delay(int(row.get("tries") or 0) + 1)
    return "done", left if wait is None else min(wait, left), ""


async def plot_step(api: Any, chat_id: str, path: list[dict[str, Any]], job: Any, clients: Clients,
                    ) -> tuple[str, str]:
    """Step 5: build «Сюжет до сих пор» when it is missing or out of date. Returns (what happened, a note) with the
    same words as `summary_step`."""
    ctx = api.ctx
    storage = api.storage(chat_id)
    forced = bool(storage.get("plot_force"))
    if not (api.settings.get("plot") or forced):
        return "idle", ""
    rows = chapters.placed(store.chapter_rows(ctx.db, chat_id), path)
    sums = store.current_summaries(ctx.db, chat_id)
    path_ids = {m["id"] for m in path}
    plot = store.plot_for_path(ctx.db, chat_id, path_ids)
    if forced and plot is not None and plot["locked"]:
        storage.set("plot_force", False)
        forced = False
    kind, ids = summaries.plot_state(rows, sums, plot)
    if forced and kind == "fresh":
        kind = "rebuild"
        ids = [s["id"] for _, s in summaries.summarized_chapters(rows, sums)]
    if kind in ("none", "fresh"):
        if forced:
            storage.set("plot_force", False)
        return "idle", ""
    items = summaries.summarized_chapters(rows, sums)
    all_ids = [s["id"] for _, s in items]
    use = [(summaries.heading(rows, p), s["text"]) for p, s in items if kind == "rebuild" or s["id"] in ids]
    old = plot["text"] if kind == "append" and plot is not None else ""
    try:
        client = clients.get("plot")
        text = await job.step(lambda: summaries.build_plot(client, old, use))
    except PluginRefused as exc:
        return "blocked", exc.message
    except ProviderError as exc:  # before AppError: it is one
        if exc.upstream_status == 429:
            await job.rate_wait(embeddings.RATE_WAIT)
            return "rate", ""
        storage.set("plot_force", False)
        return "failed", summaries.failure_text(exc)
    except AppError as exc:
        if exc.code == "no_connection":
            return "blocked", "нет подключения к модели для сюжета"
        storage.set("plot_force", False)
        return "failed", summaries.failure_text(exc)
    store.put_plot(ctx.db, chat_id, text, items[-1][0].row["last_id"], json.dumps(all_ids), by="auto")
    storage.set("plot_force", False)
    api.notify("chapters", chat_id=chat_id)
    return "done", ""


async def run(api: Any, chat_id: str, job: Any, cache: store.VectorCache | None = None) -> float | None:
    """The job's `work`: see the module docstring. Returns the seconds after which the plugin should wake the job
    again (a failed summary waits for its backoff), or None."""
    ctx = api.ctx
    cache = cache or store.VectorCache()
    clients = Clients(api, chat_id, job)
    note = ""
    retry: float | None = None
    while not job.stopped:
        if not store.is_enabled(ctx.db, chat_id):
            return None
        try:
            ctx.chats.get_chat(chat_id)
        except NotFound:
            return None
        path = ctx.chats.active_path(chat_id)
        # cutting a long branch the first time is pure CPU: off the event loop, so a reply never stalls on it
        specs = await asyncio.to_thread(pieces.specs, path)
        store.sync(ctx.db, chat_id, specs, {m["id"] for m in path})
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
        made, step_note = await close_chapter(api, chat_id, path, cache, job, model, clients)
        note = step_note or note
        if made:
            job.progress(total, total, "главы")
            continue
        outcome, wait, step_note = await summary_step(api, chat_id, path, job, clients)
        if wait is not None:
            retry = wait if retry is None else min(retry, wait)
        if outcome in ("done", "rate"):
            continue
        if outcome == "blocked":
            note = step_note or note
        else:
            outcome, step_note = await plot_step(api, chat_id, path, job, clients)
            if outcome in ("done", "rate"):
                continue
            note = step_note or note
            outcome, wait, step_note = await layers.step(api, chat_id, path, job, clients)
            if wait is not None:
                retry = wait if retry is None else min(retry, wait)
            if outcome in ("done", "rate"):
                continue
            note = step_note or note
        job.progress(total, total, note if note else ("" if model else "нет подключения для векторов"))
        return retry
    return retry
