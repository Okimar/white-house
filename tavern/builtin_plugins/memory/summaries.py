"""Summaries of chapters and «Сюжет до сих пор»: what the job does next, one model request each (ADR 0019 4.2, steps 4-5).

Todo-driven like the rest of the job: every pass asks "which chapter needs a summary?" and "is the plot up to date?",
so a cancelled step (a reply started), a crash or a restart loses nothing. A result is kept only after its request
returned.

Which chapter is next (`next_chapter`), in story order (the previous summary is the context of the next):
  * a chapter the user asked for by the button (`forced`), even when summaries are off and even when stale;
  * with «Пересказы глав» on: `raw` chapters, `failed` ones whose retry time has come (backoff 2 min, 4, 8 ... up to
    6 h), and `stale` ones only with «Пересказывать устаревшие сами» on;
  * never a chapter whose current summary is LOCKED (a hand edit), and never a deleted chapter.
A failed request marks the chapter (`error`, `tries`) and the job goes on with the next one; it is tried again after
the backoff (`retry_in` tells the plugin when to wake the job).

The plot: the newest summarized chapters in story order give a list of summary ids; the branch's plot (newest one
anchored on the branch) remembers the ids it was built from. Same list: up to date. The list grew at the end: the old
plot plus the new summaries (incremental). Anything else (a middle summary was redone, a chapter was deleted): built
again from the summaries alone, in batches. A locked plot is never replaced.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from tavern.core.llm import StructuredOutputError
from tavern.core.prompt import estimate_tokens
from tavern.errors import AppError
from tavern.providers.base import ProviderError

from . import chapters, prompts, store

BACKOFF_BASE = 120.0  # seconds before the second try; doubles each time
BACKOFF_CAP = 6 * 3600.0
PLOT_BATCH_TOKENS = 6000  # summaries folded into the plot in one request, at most (from scratch)


class Refused(AppError):
    """The model's text was not usable twice in a row (wrong language, empty, far too long)."""

    code = "memory_refused"


def retry_delay(tries: int) -> float:
    return min(BACKOFF_BASE * 2 ** max(0, tries - 1), BACKOFF_CAP)


def _seconds_since(stamp: str, now: datetime) -> float:
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return float("inf")
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds()


def retry_in(row: dict[str, Any], now: datetime) -> float:
    """Seconds until a failed chapter may be tried again (0 when it may, or when it did not fail)."""
    if not row.get("failed_at"):
        return 0.0
    return max(0.0, retry_delay(int(row.get("tries") or 1)) - _seconds_since(row["failed_at"], now))


def next_chapter(rows: list[chapters.Placed], summaries: dict[str, dict[str, Any]], *, auto: bool, auto_stale: bool,
                 forced: set[str], now: datetime) -> tuple[chapters.Placed | None, float | None]:
    """(the chapter to summarize now, or None; seconds until the nearest failed chapter may be retried, or None)."""
    wait: float | None = None
    for p in rows:
        row = p.row
        if row["state"] == "deleted":
            continue
        summary = summaries.get(row["id"])
        if summary is not None and summary["locked"]:
            continue
        if row["id"] in forced:
            return p, wait
        if not auto:
            continue
        if row["state"] == "raw":
            return p, wait
        if row["state"] == "failed":
            left = retry_in(row, now)
            if left <= 0:
                return p, wait
            wait = left if wait is None else min(wait, left)
        elif row["state"] == "stale" and auto_stale:
            return p, wait
    return None, wait


def chapter_messages(path: list[dict[str, Any]], p: chapters.Placed) -> list[dict[str, Any]]:
    return chapters.eligible_in(path, p.lo, p.hi)


def previous_summary(rows: list[chapters.Placed], summaries: dict[str, dict[str, Any]], p: chapters.Placed) -> str:
    """The summary of the closest earlier chapter that has one (context for the next, not to be retold)."""
    for q in reversed([q for q in rows if q.hi < p.lo and q.row["state"] != "deleted"]):
        if q.row["id"] in summaries:
            return summaries[q.row["id"]]["text"]
    return ""


async def ask(llm: Any, build: Callable[[str], tuple[list[Any], str]], schema: dict[str, Any], key: str,
              target: int) -> dict[str, Any]:
    """One request, one more with a note when the text is refused (`prompts.problem`). `build(note)` gives the
    request and the text it retells. Returns the JSON object. ProviderError and friends pass through; two
    refusals raise Refused."""
    note = ""
    for _ in range(2):
        messages, source = build(note)
        value = await llm.generate_json(messages, schema=schema)
        note = prompts.problem(str(value.get(key) or ""), source, target)
        if not note:
            return value
    raise Refused(f"Модель дважды ответила неподходяще: {note}")


async def summarize(llm: Any, path: list[dict[str, Any]], p: chapters.Placed, previous: str, target: int,
                    ) -> dict[str, str]:
    """One chapter's summary: {"title", "summary"}. Raises Refused / ProviderError / StructuredOutputError."""
    messages = chapter_messages(path, p)
    value = await ask(llm, lambda note: prompts.summary_messages(messages, previous, target, note),
                      prompts.SUMMARY_SCHEMA, "summary", target)
    return {"title": str(value.get("title") or "").strip(), "summary": str(value["summary"]).strip()}


FAILURES = (AppError, ProviderError, StructuredOutputError)


def failure_text(exc: Exception) -> str:
    return getattr(exc, "message", None) or str(exc) or type(exc).__name__


# -- the plot --------------------------------------------------------------------------------------


def summarized_chapters(rows: list[chapters.Placed], summaries: dict[str, dict[str, Any]],
                        ) -> list[tuple[chapters.Placed, dict[str, Any]]]:
    """Chapters of the branch (not deleted) that have a summary, in story order."""
    return [(p, summaries[p.row["id"]]) for p in rows if p.row["state"] != "deleted" and p.row["id"] in summaries]


def plot_state(rows: list[chapters.Placed], summaries: dict[str, dict[str, Any]], plot: dict[str, Any] | None,
               ) -> tuple[str, list[str]]:
    """What the plot needs: ("none", []) nothing to tell yet, ("fresh", ids) up to date, ("append", new ids) the old
    plot plus these summaries, ("rebuild", all ids) from the summaries alone. A locked plot is always «fresh»."""
    items = summarized_chapters(rows, summaries)
    ids = [s["id"] for _, s in items]
    if not ids:
        return "none", []
    if plot is None:
        return "rebuild", ids
    if plot["locked"]:
        return "fresh", ids
    covered = json.loads(plot["made_from"] or "[]")
    if covered == ids:
        return "fresh", ids
    if ids[:len(covered)] == covered and covered:
        return "append", ids[len(covered):]
    return "rebuild", ids


def heading(rows: list[chapters.Placed], p: chapters.Placed) -> str:
    number = [q for q in rows if q.row["state"] != "deleted"].index(p) + 1
    title = p.row["title"]
    return f"Chapter {number}" + (f": {title}" if title else "")


def batches(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """(heading, text) pairs in groups of about PLOT_BATCH_TOKENS (one request each when folded from scratch)."""
    out: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    used = 0
    for head, text in items:
        cost = estimate_tokens(text)
        if current and used + cost > PLOT_BATCH_TOKENS:
            out.append(current)
            current, used = [], 0
        current.append((head, text))
        used += cost
    if current:
        out.append(current)
    return out


async def build_plot(llm: Any, old: str, items: list[tuple[str, str]]) -> str:
    """The plot from `old` (may be '') and the summaries `items` (heading, text). Folds in batches when many."""
    plot = old
    for group in batches(items):
        value = await ask(llm, lambda note, base=plot, part=group: prompts.plot_messages(base, part, note),
                          prompts.PLOT_SCHEMA, "plot", prompts.PLOT_MAX_TOKENS)
        plot = str(value["plot"]).strip()
    return plot
