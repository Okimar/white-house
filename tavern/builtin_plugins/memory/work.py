"""The memory's background job for one chat (core/background.py): keep pieces right, vectorize what needs it.

Todo-driven (ADR 0019 section 4.2): every pass asks the tables "what is next?", so a restart, a crash, a
rate limit or a user's request that cancelled a step loses nothing.

  1. Check: cut the branch's messages into pieces and make the table match (new, changed, gone). No model, no
     network.
  2. Vectorize: pieces of the branch without a current vector, EXCEPT those of the latest message of each speaker
     (owner's rule). One batch per step, `job.step(...)`, so a reply or an assistant request cancels it at once and
     it is redone afterwards (never a request beside a reply). A 429 is waited out with `job.rate_wait`.
     Without an embedding connection nothing is vectorized (keywords still work).
"""

from __future__ import annotations

import logging
from typing import Any

from tavern.errors import NotFound
from tavern.providers.base import ProviderError
from tavern.services import embeddings

from . import pieces, store

log = logging.getLogger(__name__)


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


async def run(api: Any, chat_id: str, job: Any) -> None:
    """The job's `work`: see the module docstring."""
    ctx = api.ctx
    while not job.stopped:
        if not store.is_enabled(ctx.db, chat_id):
            return
        try:
            ctx.chats.get_chat(chat_id)
        except NotFound:
            return
        path = ctx.chats.active_path(chat_id)
        store.sync(ctx.db, chat_id, pieces.specs(path), {m["id"] for m in path})
        model = api.vectors_model()
        if not model:
            job.progress(0, 0, "нет подключения для векторов")
            return
        todo, total = todo_list(ctx.db, chat_id, path, model)
        if not todo:
            job.progress(total, total, "")
            return
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
