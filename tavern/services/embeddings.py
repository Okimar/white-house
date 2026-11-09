"""Embeddings, shared: the vector connection, batching, and one metered request (ADR 0019 section 2.2-G).

`character_tree` (bubble vectors) and plugins (`api.embed`, the memory plugin's chat vectors) use the same
global embedding connection (Settings → «Промпт»). Batches stay under a small request so that a free account
(Voyage: 3 requests and 10 000 tokens a minute) is never overrun. This module holds what does not depend on who
asks; waiting out a 429 and the «paused for a reply» rule belong to the caller (character_tree's vectorize, a
background job's `rate_wait`).
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from tavern.context import AppContext
from tavern.core.prompt import estimate_tokens
from tavern.errors import AppError, NotFound
from tavern.providers.base import ProviderError

log = logging.getLogger(__name__)

T = TypeVar("T")

BATCH_ITEMS = 64
BATCH_TOKENS = 3000  # estimated; a free Voyage account takes only 10K tokens a minute and 3 requests a minute
RATE_WAIT = 21.0  # seconds to wait after a 429 (3 requests a minute = one every 20 s)


class NoEmbeddingConnection(AppError):
    code = "no_embedding_connection"


def embedding_connection(ctx: AppContext) -> dict[str, Any] | None:
    """The connection chosen for vectors, or None."""
    connection_id = ctx.settings.get("embedding_connection")
    if not connection_id:
        return None
    try:
        return ctx.connections.get(connection_id)
    except NotFound:
        return None


def batches(todo: list[tuple[Any, ...]], max_items: int = BATCH_ITEMS, max_tokens: int = BATCH_TOKENS,
            *, text_at: int = 1) -> list[list[tuple[Any, ...]]]:
    """Requests of at most `max_items` texts and about `max_tokens` tokens. `text_at`: where the text sits in an item."""
    out: list[list[tuple[Any, ...]]] = []
    size = 0
    for item in todo:
        tokens = estimate_tokens(item[text_at])
        if out and (len(out[-1]) >= max_items or size + tokens > max_tokens):
            out.append([])
            size = 0
        elif not out:
            out.append([])
        out[-1].append(item)
        size += tokens
    return out


async def embed(ctx: AppContext, texts: list[str], *, purpose: str = "vectors", chat_id: str | None = None,
                ) -> tuple[list[list[float]], str]:
    """One request for all `texts` on the embedding connection: (vectors, model name). The request is recorded as
    spending under `purpose`. Raises NoEmbeddingConnection, or ProviderError (the caller decides about a 429)."""
    connection = embedding_connection(ctx)
    if connection is None:
        raise NoEmbeddingConnection("Выберите подключение для векторов в настройках → «Промпт»")
    model = connection["model"]
    provider = ctx.providers.create(connection)
    try:
        vectors = await provider.embed(texts, model=model)
    finally:
        await provider.aclose()
    from tavern.services import spending  # services import the context module

    spending.record_call(ctx, connection, purpose, model=model, tokens=sum(estimate_tokens(t) for t in texts))
    if len(vectors) != len(texts):
        raise ProviderError("The service returned fewer vectors than texts", reason="request")
    return vectors, model
