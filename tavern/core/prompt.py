"""Prompt assembly (stage 1: simple). Turns a character, persona and chat history into messages.

Stage 4 replaces the body with the full prompt manager (ordered blocks, injections by depth,
lorebooks, instruct templates). The signature of `build_prompt` is the contract plugins use via
the `prompt.built` filter, so keep it stable: the filter receives and returns a `list[ChatMessage]`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tavern.providers.base import ChatMessage

CHARS_PER_TOKEN = 4  # rough estimate; real counts come from providers where available


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


def substitute(text: str, *, char: str, user: str) -> str:
    """Minimal macro support for stage 1: {{char}} and {{user}}. Stage 2 adds the full set."""
    return text.replace("{{char}}", char).replace("{{user}}", user)


def system_text(card: dict[str, Any], *, user: str) -> str:
    char = card.get("name", "")
    sections = [
        ("", card.get("system_prompt", "")),
        ("Description", card.get("description", "")),
        ("Personality", card.get("personality", "")),
        ("Scenario", card.get("scenario", "")),
        ("Example dialogue", card.get("mes_example", "")),
    ]
    parts = []
    for title, body in sections:
        body = substitute((body or "").strip(), char=char, user=user)
        if body:
            parts.append(f"{title}:\n{body}" if title else body)
    return "\n\n".join(parts)


def build_prompt(
    card: dict[str, Any] | None,
    history: Sequence[dict[str, Any]],
    *,
    user_name: str = "User",
    max_context_tokens: int = 0,
) -> list[ChatMessage]:
    """System message from the card, then history (oldest first), trimmed to the budget.

    History items are message dicts from ChatRepo; ones with `exclude_from_prompt` are skipped.
    With a budget, the oldest messages are dropped first; the newest message is always kept.
    """
    char = (card or {}).get("name", "")
    messages: list[ChatMessage] = []
    system = system_text(card, user=user_name) if card else ""
    if system:
        messages.append(ChatMessage("system", system))

    turns = [
        ChatMessage(m["role"], substitute(m["content"], char=char, user=user_name))
        for m in history
        if not m.get("exclude_from_prompt")
    ]
    if max_context_tokens:
        budget = max_context_tokens - sum(estimate_tokens(m.content) for m in messages)
        kept: list[ChatMessage] = []
        for turn in reversed(turns):
            cost = estimate_tokens(turn.content)
            if kept and cost > budget:
                break
            kept.append(turn)
            budget -= cost
        turns = list(reversed(kept))
    return [*messages, *turns]
