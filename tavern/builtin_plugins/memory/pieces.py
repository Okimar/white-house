"""Cutting chat messages into pieces for the memory (pure: no database, no network).

Which messages count (owner's rules, ADR 0019): only the text of the shown version (the active swipe) of a real
conversation message. Never the prompt, notes or thinking (`swipe_meta.reasoning` is not read here), never a
hidden message, one hidden from the model (`exclude_from_prompt`), or a plugin's service message (`owner`). Roles
`user` and `assistant` only.

Which pieces are vectorized: every piece EXCEPT those of the latest message of each speaker (each character's last
reply and the user's last message): such a message may still be regenerated or edited, and it is in the live tail
of the prompt anyway. `latest_ids` finds them.

A piece is one message, or a part of a long one: a text over MAX_TOKENS is cut along paragraphs, then sentences,
into parts of about TARGET_TOKENS (a single huge sentence is cut by size). The piece's `hash` covers its text, so a
changed message gives a changed hash and the stored vector goes stale.

Keywords of a piece: the stems (`core.retrieval.stem`, so Russian endings do not matter) of its words, without
stop words. Names (a capital letter inside a sentence) and numbers are marked with a leading `*`: details like
these are what a summary loses, and the search weighs them double.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from tavern.core.prompt import CHARS_PER_TOKEN, estimate_tokens
from tavern.core.retrieval import stem

TARGET_TOKENS = 300
MAX_TOKENS = 400
MAX_KEYWORDS = 40
MIN_WORD = 3
ROLES = ("user", "assistant")

STOP = frozenset("""
the and for are but not you your with that this have has had was were will would can could from they them their what
when where which who whom how why all any out about into over just than then there here also very
это как что так для при про все всё его ещё еще или уже она они оно мне меня тебя себя был была было были будет
если когда только чтобы потому который которая которое которые этот эта эти тот той там тут вот ему ней них
ним нас вас вам нам ибо надо можно нет да
""".split())
_WORD = re.compile(r"\w+", re.UNICODE)
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Spec:
    """A piece as it should be: which message, which part, its text, hash, tokens and keywords."""

    message_id: str
    part: int
    text: str
    hash: str
    tokens: int
    keywords: tuple[str, ...]


def eligible(message: dict[str, Any]) -> bool:
    """A message whose text may become a piece."""
    return (message.get("role") in ROLES and not message.get("hidden") and not message.get("exclude_from_prompt")
            and not message.get("owner") and bool((message.get("content") or "").strip()))


def speaker_key(message: dict[str, Any]) -> str:
    """Who spoke: the user, or a character (by id, else by name)."""
    if message["role"] == "user":
        return "user"
    return "char:" + str(message.get("character_id") or message.get("name") or "")


def latest_ids(messages: list[dict[str, Any]]) -> set[str]:
    """Ids of the latest eligible message of each speaker (messages in chat order, oldest first)."""
    last: dict[str, str] = {}
    for message in messages:
        if eligible(message):
            last[speaker_key(message)] = message["id"]
    return set(last.values())


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def cut(text: str) -> list[str]:
    """A message text as one or more parts (see the module docstring)."""
    text = text.strip()
    if not text:
        return []
    if estimate_tokens(text) <= MAX_TOKENS:
        return [text]
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n|\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        for sentence in _SENTENCE.split(paragraph):
            sentence = sentence.strip()
            size = MAX_TOKENS * CHARS_PER_TOKEN
            while len(sentence) > size:  # one huge sentence: cut by size at the last space
                at = sentence.rfind(" ", 0, size)
                at = at if at > size // 2 else size
                units.append(sentence[:at].strip())
                sentence = sentence[at:].strip()
            if sentence:
                units.append(sentence)
    parts: list[str] = []
    current: list[str] = []
    used = 0
    for unit in units:
        cost = estimate_tokens(unit)
        if current and used + cost > TARGET_TOKENS:
            parts.append(" ".join(current))
            current, used = [], 0
        current.append(unit)
        used += cost
    if current:
        parts.append(" ".join(current))
    return parts


def keywords(text: str) -> list[str]:
    """Stems of the text's words, most frequent first; `*stem` marks a name or a number (see the docstring)."""
    counts: Counter[str] = Counter()
    starred: set[str] = set()
    sentence_start = True
    for match in _WORD.finditer(text):
        word = match.group()
        between = text[:match.start()]
        # the first word of a sentence is capitalised anyway: only a capital inside a sentence marks a name
        previous = between.rstrip()[-1:] if between.strip() else ""
        sentence_start = not previous or previous in ".!?…\n\"«—-:"
        low = word.lower()
        if len(low) < MIN_WORD and not any(ch.isdigit() for ch in low):
            continue
        if low in STOP:
            continue
        key = low if any(ch.isdigit() for ch in low) else stem(low)
        counts[key] += 1
        if any(ch.isdigit() for ch in low) or (word[0].isupper() and not sentence_start and not word.isupper()):
            starred.add(key)
    ordered = [k for k, _ in counts.most_common(MAX_KEYWORDS)]
    return [("*" + k) if k in starred else k for k in ordered]


def specs_for(message: dict[str, Any]) -> list[Spec]:
    """The pieces of one message as they should be now ([] for a message that does not count)."""
    if not eligible(message):
        return []
    out = []
    for part, text in enumerate(cut(message["content"])):
        out.append(Spec(message["id"], part, text, text_hash(text), estimate_tokens(text), tuple(keywords(text))))
    return out


def specs(messages: list[dict[str, Any]]) -> list[Spec]:
    """Pieces for a list of messages, in chat order."""
    return [spec for message in messages for spec in specs_for(message)]
