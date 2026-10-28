"""Prompt assembly (stage 1: simple). Turns a character, persona and chat history into messages.

Stage 4 replaces the body with the full prompt manager (ordered blocks, injections by depth,
lorebooks, instruct templates). The signature of `build_prompt` is the contract plugins use via
the `prompt.built` filter, so keep it stable: the filter receives and returns a `list[ChatMessage]`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import re
from typing import Any

from tavern.providers.base import ChatMessage

CHARS_PER_TOKEN = 4  # rough estimate; real counts come from providers where available
# Some backends (Gemini) reject a conversation without any user turn ("contents is not specified").
START_TURN = "[Start]"
DEFAULT_CHAR_NAME = "Assistant"  # {{char}} in a chat without a character


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


VALUE_MACRO = re.compile(r"\{\{([A-Za-z]+)\}\}")
# {{setvar::name::value}} (value may span lines) and {{getvar::name}}, read left to right.
VAR_MACRO = re.compile(r"\{\{setvar::([^:{}]+)::(.*?)\}\}|\{\{getvar::([^{}]+?)\}\}", re.S)


def substitute(
    text: str,
    *,
    char: str,
    user: str,
    rng: Callable[[int], int] | None = None,
    values: dict[str, str] | None = None,
    variables: dict[str, str] | None = None,
) -> str:
    """Expand safe macros; an injected RNG makes random macros testable.

    `values` fills simple macros such as {{description}} or {{persona}} (names compared without
    case). `variables` is the store for {{setvar::name::value}} / {{getvar::name}}: pass the same
    dict for every text of one prompt so a block can set what a later block reads. Unknown macros
    are left as they are.
    """
    result = text.replace("{{char}}", char).replace("{{user}}", user)
    choose = rng or __import__("random").randrange

    if values:
        lowered = {k.lower(): v for k, v in values.items()}
        result = VALUE_MACRO.sub(lambda m: lowered.get(m.group(1).lower(), m.group(0)), result)

    if variables is not None:
        def var(match: re.Match[str]) -> str:
            if match.group(1) is not None:
                variables[match.group(1).strip()] = match.group(2)
                return ""
            return variables.get(match.group(3).strip(), "")
        result = VAR_MACRO.sub(var, result)

    def random_choice(match: re.Match[str]) -> str:
        body = match.group(1)
        # {{random::a::b}} and the older {{random:a,b,c}}
        options = body[1:].split("::") if body.startswith(":") else body.split(",")
        return options[choose(len(options))] if options else ""

    def roll(match: re.Match[str]) -> str:
        count, sides = int(match.group(1) or 1), int(match.group(2))
        if count < 1 or sides < 1 or count > 100 or sides > 100000:
            return match.group(0)
        return str(sum(choose(sides) + 1 for _ in range(count)))

    result = re.sub(r"\{\{random:([^{}]+)\}\}", random_choice, result)
    return re.sub(r"\{\{roll::?(\d*)d(\d+)\}\}", roll, result)



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
    instruction: str = "",
) -> list[ChatMessage]:
    """System message from the card, then history (oldest first), trimmed to the budget.

    History items are message dicts from ChatRepo; ones with `exclude_from_prompt` are skipped.
    With a budget, the oldest messages are dropped first; the newest message is always kept.
    `instruction` (e.g. "write the user's next message") is appended as the last user turn.
    A history without any user turn gets START_TURN before it, so every backend accepts it.
    """
    char = (card or {}).get("name") or DEFAULT_CHAR_NAME
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
    if instruction:
        turns.append(ChatMessage("user", substitute(instruction, char=char, user=user_name)))
    if turns and not any(t.role == "user" for t in turns):
        turns.insert(0, ChatMessage("user", START_TURN))
    return [*messages, *turns]
