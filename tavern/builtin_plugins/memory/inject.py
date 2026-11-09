"""What the memory adds to a reply (`chat.before_generate`), and when it asks for the query vectors.

No model call and no embedding request of its own: it uses the reply's ONE meaning request
(`job.meaning`, services/character_tree.Meaning: the query vectors the bubbles and lorebooks already paid for).
The filter event `meaning.wanted` makes that request happen even for a chat without lorebooks.

M1 does pieces only (ADR 0019 stage M1): found pieces of messages that no longer fit the window go in as
«Ранее в истории (отрывок)». HISTORY FIRST: the plugin only offers candidates, each an `Extra` whose `covers` is
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

from . import pieces, search, store

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
    chosen: list[search.Scored] = []
    used = 0
    for item in scored:
        if len(chosen) >= MAX_PIECES:
            break
        if used + item.candidate.tokens > budget:
            continue
        chosen.append(item)
        used += item.candidate.tokens
    chosen.sort(key=lambda s: (s.candidate.index, s.candidate.piece_id))
    for item in chosen:
        message = history[item.candidate.index]
        made = {spec.hash: spec for spec in pieces.specs_for(message)}
        spec = made.get(item.candidate.hash)
        if spec is None:  # the message changed since the piece was cut: the job will redo it
            continue
        who = f"№{message['depth'] + 1} {message.get('name') or message['role']}"
        prompt.extras.append(Extra(
            text=f"[Ранее в истории (отрывок), {who}]\n{spec.text}",
            source=f"plugin:memory:piece:{item.candidate.piece_id}",
            label=f"Ранее в истории (отрывок): {who}",
            covers=(message["id"], message["id"])))
