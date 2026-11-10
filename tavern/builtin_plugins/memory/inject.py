"""What the memory adds to a reply (`chat.before_generate`), and when it asks for the query vectors.

No model call and no embedding request of its own: it uses the reply's ONE meaning request
(`job.meaning`, services/character_tree.Meaning: the query vectors the bubbles and lorebooks already paid for).
The filter event `meaning.wanted` makes that request happen even for a chat without lorebooks.

HISTORY FIRST: the plugin only offers candidates, each an `Extra` whose `covers` is the range of messages it stands
for; the assembler (core/assembly.py) sends one only when all those messages were really left out of the prompt, and
only while the memory's own budget (`memory_reserve`) lasts. The plugin RANKS the offers (best first: they get the
budget) and gives each its place in the story (`Extra.order`), so the prompt tells them in story order. The live tail
(`tail_percent` of the window, at least `tail_min` messages, newest first) is never cut before older messages:
`tail_from`. With no window known (`max_context_tokens` 0) the whole history goes as it always did and only
`{{summary}}` is filled.

What is offered, in this ranking (ADR 0019 4.1):
  1. «Сюжет до сих пор» of this branch (`if_dropped`: whenever anything was left out); also `{{summary}}`;
  2. chapters of this branch found by the reply's query, two levels: the summary's score S and the best piece's P.
     P > S + margin → the best piece with one neighbour each side («Ранее в истории: Глава N», covers = their
     range); otherwise the summary (covers = the whole chapter). «Вся глава» → the full text. Each chapter's forms
     are alternatives (`Extra.group`): when the summary cannot go (part of the chapter is still in the window, or
     it does not fit) its pieces may;
  3. chapters found in one of the last `sticky` replies and not found now, in the form they went then;
  4. pieces of old messages that are in no chapter yet (the job lags, e.g. right after an import).
Unconditional (M4, `layers.contribute`, whatever the window): each member's «Развитие» right after its card, and the
«Сейчас:» line of each chosen lorebook bubble appended to its text for this reply (`lore_rewritten`); M5,
`threads.contribute`: the speaker's vows after their card, and the «Слово и нити» records relevant to this reply
that the speaker may know (scored with the same one meaning request).
The budget (`budget`): `memory_percent` of the window (15 %), never less than `memory_tokens` (1500).
Group chats: a chapter goes only to a speaking member who was in it, or one with «Помнит всё»; the plot goes to
everyone; the user's own turn (impersonation) sees everything.
Which chapters really went is learned in `prompt.built` (`record_hits`) and kept for the sticky rule.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from tavern.core.assembly import Extra
from tavern.core.prompt import estimate_tokens
from tavern.services import character_tree

from . import chapters, layers, pieces, search, store, threads

MAX_OFFERS = 40  # chapter / piece offers per reply at most (the assembler sends only what was left out and fits)
SCAN_CHARS = 4000
STICKY_SCORE = search.MIN_SCORE  # a chapter kept only by the sticky rule ranks after everything found now


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


def budget(settings: dict[str, Any], max_context: int) -> int:
    """The memory's own budget: `memory_percent` of the window, but never less than `memory_tokens` (M4: a fixed 1500
    fitted only one summary and the plot in a large window)."""
    floor = int(settings.get("memory_tokens") or 0)
    percent = float(settings.get("memory_percent") or 0)
    return max(floor, int(max_context * percent / 100))


def scan_texts(history: list[dict[str, Any]], count: int) -> list[str]:
    """The last `count` conversation messages' text, for the keyword part of the query."""
    chosen = [m for m in history if pieces.eligible(m)]
    return [m["content"][-SCAN_CHARS:] for m in chosen[-count:]]


def wants_meaning(api: Any, value: bool = False, chat: dict[str, Any] | None = None) -> bool | None:
    """Filter `meaning.wanted`: yes when memory is on for the chat and some of its items have a current vector."""
    if not chat or not store.is_enabled(api.ctx.db, chat["id"]):
        return None
    model = api.vectors_model()
    return True if model and store.vector_count(api.ctx.db, chat["id"], model) > 0 else None


@lru_cache(maxsize=4096)
def summary_keywords(text: str) -> tuple[str, ...]:
    return tuple(pieces.keywords(text))


@dataclass
class Offer:
    """One thing the memory may send: its rank score, its story position, and the extras (alternatives)."""

    score: float
    order: int
    extras: list[Extra]
    hits: dict[str, tuple[str, str]] = field(default_factory=dict)  # extra source -> (chapter id, form) to remember


async def contribute(api: Any, cache: store.VectorCache, job: Any, pending: dict[str, Any] | None = None) -> None:
    """The `chat.before_generate` handler: tail, candidates, scoring, extras."""
    ctx = api.ctx
    db = ctx.db
    chat_id = job.chat["id"]
    prompt = job.prompt_input
    if prompt is None or not store.is_enabled(db, chat_id):
        return
    settings = api.settings
    history = list(job.history)
    plot = store.plot_for_path(db, chat_id, {m["id"] for m in history})
    if plot is not None and plot["text"].strip():
        prompt.values["summary"] = plot["text"]  # {{summary}} in a preset, whatever the window
    layers.contribute(api, job, history)  # M4: development after the cards, «Сейчас:» lines; whatever the window
    meaning_messages = int(character_tree.retrieval_settings(ctx).get("meaning_messages") or 3)
    threads.contribute(api, cache, job, history, scan_texts(history, meaning_messages))  # M5: the speaker's word
    if not job.max_context_tokens:
        return
    tail_from, tail_ids = tail(history, job.max_context_tokens, float(settings["tail_percent"]),
                               int(settings["tail_min"]))
    if tail_from is None:
        return
    prompt.tail_from = tail_from
    prompt.memory_reserve = budget(settings, job.max_context_tokens)
    generation = store.next_generation(db, chat_id)
    first: list[Offer] = []
    if plot is not None and plot["text"].strip():  # the plot is first in the ranking and first in the block
        first.append(Offer(2.0, -1, [Extra(text=f"[Сюжет до сих пор]\n{plot['text'].strip()}",
                                           source="plugin:memory:plot", label="Сюжет до сих пор", if_dropped=True)]))
    offers = _chapter_offers(api, cache, job, history, tail_ids, generation)
    offers.sort(key=lambda o: (-o.score, o.order))
    remember: dict[str, tuple[str, str]] = {}
    for offer in [*first, *offers[:MAX_OFFERS]]:
        for extra in offer.extras:
            extra.order = offer.order
            prompt.extras.append(extra)
        remember.update(offer.hits)
    if pending is not None and remember:
        if len(pending) > 50:  # replies that never reached prompt.built (a failure before it)
            pending.clear()
        pending[job.message_id or chat_id] = {"chat_id": chat_id, "generation": generation, "hits": remember}


def record_hits(api: Any, job: Any, pending: dict[str, Any]) -> None:
    """`prompt.built`: the chapters that really went into this reply are remembered for the sticky rule."""
    found = pending.pop(job.message_id or job.chat["id"], None)
    if not found or job.prompt is None:
        return
    sent = {entry.source for entry in job.prompt.entries}
    items = [(chapter_id, found["generation"], what) for source, (chapter_id, what) in found["hits"].items()
             if source in sent]
    if items:
        store.put_hits(api.ctx.db, found["chat_id"], items)


def _speaker(prompt: Any) -> tuple[str, bool]:
    """(the speaking member's character id, several characters in the chat). '' = the user's own turn."""
    if getattr(prompt, "impersonate", False):
        return "", bool(prompt.several)
    primary = next((m for m in prompt.members if m.primary), None)
    return (primary.character_id if primary else ""), bool(prompt.several)


def _chapter_offers(api: Any, cache: store.VectorCache, job: Any, history: list[dict[str, Any]],
                    tail_ids: set[str], generation: int) -> list[Offer]:
    ctx = api.ctx
    db = ctx.db
    chat_id = job.chat["id"]
    prompt = job.prompt_input
    settings = api.settings
    index = {m["id"]: i for i, m in enumerate(history)}
    tail_start = min((index[i] for i in tail_ids if i in index), default=len(history))
    placed = [p for p in chapters.placed(store.chapter_rows(db, chat_id), history) if p.row["state"] != "deleted"]
    numbers = {p.row["id"]: n for n, p in enumerate(placed, 1)}
    speaker, several = _speaker(prompt)
    flags = store.member_flags(db, chat_id)
    remembers_all = bool(flags.get(speaker, {}).get("remembers_all"))
    allowed = {p.row["id"] for p in placed
               if search.member_allowed(p.row["participants"], speaker, several=several, remembers_all=remembers_all)}
    placed = [p for p in placed if p.row["id"] in allowed]
    home_of: dict[int, chapters.Placed] = {}
    for p in placed:
        for i in range(p.lo, p.hi + 1):
            home_of[i] = p
    closed_by_others = {i for p in chapters.placed(store.chapter_rows(db, chat_id), history)
                        if p.row["id"] not in allowed and p.row["state"] != "deleted" for i in range(p.lo, p.hi + 1)}
    candidates = [search.Candidate(p["id"], p["message_id"], index[p["message_id"]], p["hash"], p["tokens"],
                                   tuple(p["keywords"]))
                  for p in store.pieces(db, chat_id)
                  if p["message_id"] in index and p["message_id"] not in tail_ids
                  and index[p["message_id"]] not in closed_by_others]
    summaries = store.current_summaries(db, chat_id)
    summary_of: dict[str, dict[str, Any]] = {}
    for p in placed:
        summary = summaries.get(p.row["id"])
        if summary is None or not summary["text"].strip() or p.hi >= tail_start:
            continue  # a chapter reaching into the live tail can never be left out whole
        summary_of[p.row["id"]] = summary
        text = summary["text"]
        candidates.append(search.Candidate("s:" + p.row["id"], p.row["last_id"], p.lo, pieces.text_hash(text),
                                           estimate_tokens(text), summary_keywords(text)))
    hits = {cid: hit for cid, hit in store.hits(db, chat_id).items()
            if search.sticky_live(hit[0], generation, int(settings.get("sticky") or 0))}
    if not candidates:
        return []
    model = api.vectors_model()
    vectors = cache.get(db, chat_id, model) if model else {}
    meaning = getattr(job, "meaning", None)
    queries: list[array] = []
    if meaning is not None and meaning.queries and not meaning.error and meaning.model == model:
        queries = [search.unit(q) for q in meaning.queries]
    retrieval = character_tree.retrieval_settings(ctx)
    texts = scan_texts(history, int(retrieval.get("meaning_messages") or 3))
    stems = search.query_stems(texts, [m.name for m in prompt.members])
    scored = search.score(candidates, vectors, queries, stems, float(retrieval["threshold"]))
    piece_rows = [c for c in candidates if not c.piece_id.startswith("s:")]

    summary_score: dict[str, float] = {}
    by_chapter: dict[str, list[search.Scored]] = {}
    loose: list[search.Scored] = []
    for item in scored:
        if item.candidate.piece_id.startswith("s:"):
            summary_score[item.candidate.piece_id[2:]] = item.score
            continue
        home = home_of.get(item.candidate.index)
        if home is not None:
            by_chapter.setdefault(home.row["id"], []).append(item)
        else:
            loose.append(item)

    def head(p: chapters.Placed) -> str:
        title = p.row["title"]
        return f"Глава {numbers[p.row['id']]}" + (f". {title}" if title else "")

    def pieces_extra(p: chapters.Placed, group: list[search.Candidate]) -> Extra | None:
        parts = [(c, _text(history, c)) for c in group]
        parts = [(c, t) for c, t in parts if t is not None]
        if not parts:
            return None
        body = "\n".join(f"{_label(history, c.index)}: {t}" for c, t in parts)
        first, last = history[parts[0][0].index]["id"], history[parts[-1][0].index]["id"]
        return Extra(text=f"[Ранее в истории: {head(p)}]\n{body}", source=f"plugin:memory:chapter:{p.row['id']}",
                     label=f"Ранее в истории: {head(p)} (отрывки)", covers=(first, last), group=p.row["id"])

    def neighbours(p: chapters.Placed, best_id: str) -> list[search.Candidate]:
        near = sorted((c for c in piece_rows if p.lo <= c.index <= p.hi), key=lambda c: (c.index, c.piece_id))
        at = next((i for i, c in enumerate(near) if c.piece_id == best_id), None)
        return [] if at is None else near[max(0, at - 1):at + 2]

    def summary_extra(p: chapters.Placed) -> Extra:
        text = summary_of[p.row["id"]]["text"].strip()
        return Extra(text=f"[Ранее в истории: {head(p)}]\n{text}", source=f"plugin:memory:summary:{p.row['id']}",
                     label=f"Ранее в истории: {head(p)} (пересказ)",
                     covers=(history[p.lo]["id"], history[p.hi]["id"]), group=p.row["id"])

    def whole_extra(p: chapters.Placed) -> Extra:
        lines = [f"{m.get('name') or m['role']}: {m['content']}" for m in history[p.lo:p.hi + 1] if pieces.eligible(m)]
        return Extra(text=f"[Ранее в истории: {head(p)}]\n" + "\n".join(lines),
                     source=f"plugin:memory:whole:{p.row['id']}", label=f"Ранее в истории: {head(p)} (вся)",
                     covers=(history[p.lo]["id"], history[p.hi]["id"]), group=p.row["id"])

    def offer(p: chapters.Placed, form: str, score: float, group: list[search.Candidate]) -> Offer | None:
        """The chapter in `form` first, then its pieces as the alternative for when that form cannot go."""
        extras: list[Extra] = []
        hits: dict[str, tuple[str, str]] = {}
        if form == "whole":
            extra = whole_extra(p)
            extras.append(extra)
            hits[extra.source] = (p.row["id"], "whole")
        elif form == "summary" and p.row["id"] in summary_of:
            extra = summary_extra(p)
            extras.append(extra)
            hits[extra.source] = (p.row["id"], "summary")
        piece_extra = pieces_extra(p, group) if group else None
        if piece_extra is not None:
            extras.append(piece_extra)
            hits[piece_extra.source] = (p.row["id"], "pieces:" + ",".join(c.piece_id for c in group))
        return Offer(score, p.lo, extras, hits) if extras else None

    out: list[Offer] = []
    for p in placed:
        cid = p.row["id"]
        found = by_chapter.get(cid, [])
        s_score = summary_score.get(cid)
        best = found[0] if found else None
        p_score = best.score if best else 0.0
        if best is None and s_score is None:
            hit = hits.get(cid)
            if hit is None:
                continue
            what = hit[1]
            if what.startswith("pieces:"):
                wanted = set(what[len("pieces:"):].split(","))
                group = [c for c in piece_rows if c.piece_id in wanted and p.lo <= c.index <= p.hi]
                made = offer(p, "pieces", STICKY_SCORE, sorted(group, key=lambda c: (c.index, c.piece_id)))
            else:
                made = offer(p, "whole" if what == "whole" and p.row["whole"] else "summary", STICKY_SCORE, [])
            if made is not None:
                made.hits = {}  # kept by the sticky rule only: not a new hit, so the window ends on time
                out.append(made)
            continue
        group = neighbours(p, best.candidate.piece_id) if best else []
        if p.row["whole"]:
            form = "whole"
        else:
            form = search.choose_form(s_score if cid in summary_of else None, p_score)
        made = offer(p, form, max(p_score, s_score or 0.0), group)
        if made is not None:
            out.append(made)
    for item in loose:
        candidate = item.candidate
        text = _text(history, candidate)
        if text is None:
            continue
        who = _label(history, candidate.index)
        out.append(Offer(item.score, candidate.index, [Extra(
            text=f"[Ранее в истории (отрывок), {who}]\n{text}", source=f"plugin:memory:piece:{candidate.piece_id}",
            label=f"Ранее в истории (отрывок): {who}",
            covers=(history[candidate.index]["id"], history[candidate.index]["id"]))]))
    return out


def _label(history: list[dict[str, Any]], index: int) -> str:
    message = history[index]
    return f"№{message['depth'] + 1} {message.get('name') or message['role']}"


def _text(history: list[dict[str, Any]], candidate: search.Candidate) -> str | None:
    message = history[candidate.index]
    spec = {s.hash: s for s in pieces.specs_for(message)}.get(candidate.hash)
    return None if spec is None else spec.text  # None: the message changed since the piece was cut
