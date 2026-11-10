"""Texts for the small-model tasks of the memory (pure: no database, no network). ADR 0019 sections 4.2 and 8 (R4).

Tasks answered as JSON through `LLMClient.generate_json` (core/structured.py checks the shape):
  * a chapter's summary: `{"title": str, "summary": str}`. In the language most of the chapter is written in (Q11),
    past tense, plain narration, no meta talk, about `summary_tokens` tokens;
  * «Сюжет до сих пор»: `{"plot": str}`, 300-500 tokens, built from the previous plot and the summaries after it
    (or from the summaries alone), in story order;
  * M4: «Развитие» layers and «Сейчас:» lines (`LAYER_SCHEMA`), and the self-compression of a too long layer;
  * M5: «Слово и нити» of a closed chapter (`THREAD_SCHEMA`): new records with exact quotes, status changes.

The prompts themselves are English (small models follow them best); the answer's language is decided by the text
it retells, and `problem()` checks it (R4: a summary in the wrong script is refused, asked once more, and then the
chapter is marked as failed).
"""

from __future__ import annotations

import re
from typing import Any

from tavern.core.prompt import CHARS_PER_TOKEN, estimate_tokens
from tavern.providers.base import ChatMessage

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
    "required": ["title", "summary"],
    "additionalProperties": False,
}
PLOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"plot": {"type": "string"}},
    "required": ["plot"],
    "additionalProperties": False,
}

PLOT_MIN_TOKENS, PLOT_MAX_TOKENS = 300, 500
MAX_INPUT_TOKENS = 16000  # a chapter longer than this is shortened message by message (start and end are kept)
MIN_SUMMARY_CHARS = 40
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

RULES = """Rules:
- Write in the language most of the text below is written in{hint}.
- Past tense, plain narration in the third person. Name the characters by name.
- No meta talk: never mention "the chapter", "the summary", "the user", "the AI", "the roleplay", the model or \
out-of-character remarks, and give no advice or evaluation.
- Keep what matters later: who did what and to whom, names, places, numbers, objects, injuries, promises, secrets, \
debts, agreements, and who knows what. Leave out filler and repeated descriptions.
- Do not invent anything that is not in the text. No markdown, no headings, no lists: running prose."""


def cyrillic_share(text: str) -> float:
    """The share of Cyrillic among the letters of `text` (0 for a text with no letters)."""
    letters = _LETTER.findall(text)
    return len(_CYRILLIC.findall(text)) / len(letters) if letters else 0.0


def language_hint(text: str) -> str:
    """What to add to the language rule: «(here: Russian)» for a mostly Cyrillic text, else nothing."""
    return " (here: Russian)" if cyrillic_share(text) > 0.5 else ""


def problem(summary: str, source: str, target_tokens: int) -> str:
    """Why a model's text is not usable ('' when it is): empty or tiny, far too long, or in the wrong script."""
    summary = summary.strip()
    if len(summary) < MIN_SUMMARY_CHARS:
        return "the text is too short"
    if estimate_tokens(summary) > max(3 * target_tokens, 200):
        return "the text is much longer than asked"
    if cyrillic_share(source) > 0.5 and cyrillic_share(summary) < 0.5:
        return "the text must be in Russian, the language of the source"
    if cyrillic_share(source) < 0.1 and cyrillic_share(summary) > 0.5:
        return "the text must be in the language of the source, not in Russian"
    return ""


def shorten(text: str, tokens: int) -> str:
    """`text` cut to about `tokens` tokens, keeping its start and end."""
    size = tokens * CHARS_PER_TOKEN
    if len(text) <= size:
        return text
    head = size * 2 // 3
    return text[:head].rstrip() + " [...] " + text[len(text) - (size - head):].lstrip()


def chapter_lines(messages: list[dict[str, Any]]) -> list[str]:
    """«Name: text» for each message, shortened evenly when the whole would pass MAX_INPUT_TOKENS."""
    lines = [(m.get("name") or m["role"], " ".join((m.get("content") or "").split())) for m in messages]
    total = sum(estimate_tokens(text) for _, text in lines)
    if total > MAX_INPUT_TOKENS and lines:
        each = max(40, MAX_INPUT_TOKENS // len(lines))
        lines = [(name, shorten(text, each)) for name, text in lines]
    return [f"{name}: {text}" for name, text in lines]


def summary_messages(messages: list[dict[str, Any]], previous: str, target_tokens: int, note: str = "",
                     ) -> tuple[list[ChatMessage], str]:
    """The request for one chapter's summary, and the text it retells (for `problem`)."""
    lines = chapter_lines(messages)
    source = "\n".join(lines)
    system = (f"You write the memory of a long roleplay chat. Retell the scene below as a summary of about "
              f"{target_tokens} tokens that a reader will use later instead of the full text. Give the chapter a "
              f"short title of two to six words in the same language.\n{RULES.format(hint=language_hint(source))}")
    context = f"The story just before this scene (for context only, do not retell it):\n{previous}\n\n" if previous else ""
    user = f"{context}The scene:\n{source}"
    if note:
        user += f"\n\nYour previous answer was refused: {note}. Answer again."
    return [ChatMessage("system", system), ChatMessage("user", user)], source


def plot_messages(old: str, summaries: list[tuple[str, str]], note: str = "") -> tuple[list[ChatMessage], str]:
    """The request for «Сюжет до сих пор»: the old plot (may be empty) and the chapter summaries after it, as
    (heading, text) in story order. Returns the request and the text it is built from (for `problem`)."""
    source = "\n\n".join(f"{head}\n{text}" for head, text in summaries)
    system = (f"You keep the running plot of a long roleplay chat. Write \"the story so far\" of {PLOT_MIN_TOKENS} to "
              f"{PLOT_MAX_TOKENS} tokens: the main line of events in story order, who the main characters are and "
              f"where things stand now (relationships, open goals and promises). Compress older parts more than "
              f"newer ones.\n{RULES.format(hint=language_hint(source or old))}")
    parts = []
    if old:
        parts.append(f"The story so far, as it was written before:\n{old}")
    parts.append("New chapters to work in, in story order:\n" + source)
    user = "\n\n".join(parts)
    if note:
        user += f"\n\nYour previous answer was refused: {note}. Answer again."
    return [ChatMessage("system", system), ChatMessage("user", user)], source + old


# -- M4: «Развитие персонажа» and «Сейчас:» lines -------------------------------------------------

LAYER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "layers": {"type": "array", "items": {
            "type": "object", "properties": {"who": {"type": "string"}, "text": {"type": "string"}},
            "required": ["who", "text"]}},
        "now_lines": {"type": "array", "items": {
            "type": "object", "properties": {"entry": {"type": "string"}, "line": {"type": "string"}},
            "required": ["entry", "line"]}},
    },
    "required": ["layers", "now_lines"],
}
COMPRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
LAYER_RULES = """Rules:
- Write in the language most of the scene is written in{hint}.
- Present tense: how things stand NOW. Name the characters by name. No meta talk (never "the chapter", "the layer", \
"the user", "the AI", "the roleplay"), no advice, no markdown, no lists: short running prose.
- Do not invent anything that is not in the scene or in the old text."""
NOW_LINE_MAX_CHARS = 300


def layer_messages(scene: str, people: list[dict[str, str]], entries: list[dict[str, str]], target_tokens: int,
                   note: str = "") -> tuple[list[ChatMessage], str]:
    """The request of the development step for one closed chapter.

    `scene`: the chapter's summary, or its text when it has none. `people`: [{code, name, card, old}] the characters
    whose layer is to be updated (card = the card's short description, old = the layer as it is on this branch).
    `entries`: [{code, title, text, old}] lorebook bubbles the chapter mentions, for «Сейчас:» lines ([] = none
    asked). Returns the request and the scene (for the language check)."""
    hint = language_hint(scene)
    parts = ["You keep the memory of a long roleplay chat."]
    if people:
        parts.append(
            f"For each character listed, write their layer «how this character is NOW»: relationships with the others, "
            f"what has happened to them, new traits and habits, changes of state (wounds, possessions, position, "
            f"goals). The character card stays as it is written; the layer holds only what changed since the card or "
            f"is new, and where it contradicts the card, the layer is the truth now. Merge the old layer with this "
            f"scene: keep what still holds, drop what is no longer true or no longer matters, compress older details "
            f"more than new ones. Each layer at most {target_tokens} tokens. A character the scene did not change "
            f"keeps the old layer word for word.")
    if entries:
        parts.append(
            "For each world entry listed that this scene really changed (a place destroyed or taken, an object lost "
            "or found, a status or a ruler changed), one short line, one sentence, about how it is NOW. Leave out "
            "entries the scene did not change: an empty list is fine. A line replaces the entry's old line.")
    parts.append(LAYER_RULES.format(hint=hint))
    parts.append('Answer as JSON: {"layers": [{"who": "<code>", "text": "..."}], '
                 '"now_lines": [{"entry": "<code>", "line": "..."}]}.')
    user: list[str] = []
    if people:
        user.append("Characters:\n" + "\n\n".join(
            f"{p['code']}: {p['name']}\nCard: {shorten(p['card'], 300) or '(none)'}\nOld layer: {p['old'] or '(none yet)'}"
            for p in people))
    if entries:
        user.append("World entries:\n" + "\n\n".join(
            f"{e['code']}: {e['title']}\nText: {shorten(e['text'], 200)}\nOld line: {e['old'] or '(none)'}"
            for e in entries))
    user.append(f"The scene that just ended:\n{scene}")
    text = "\n\n".join(user)
    if note:
        text += f"\n\nYour previous answer was refused: {note}. Answer again."
    return [ChatMessage("system", "\n".join(parts)), ChatMessage("user", text)], scene


def compress_messages(name: str, text: str, target_tokens: int) -> list[ChatMessage]:
    """The self-compression request: a layer that came out too long, shortened to `target_tokens`."""
    system = (f"Shorten the text below (how the character {name} is now in a long roleplay) to at most {target_tokens} "
              f"tokens. Keep relationships, what happened to them and their present state; drop repetition and older "
              f'details first. Same language, present tense, no meta talk, running prose. Answer as JSON: {{"text": "..."}}.')
    return [ChatMessage("system", system), ChatMessage("user", text)]


def wrong_language(text: str, source: str) -> str:
    """Why a layer or a line is in the wrong script ('' when it is fine)."""
    if not text.strip():
        return ""
    if cyrillic_share(source) > 0.5 and cyrillic_share(text) < 0.5:
        return "the text must be in Russian, the language of the scene"
    if cyrillic_share(source) < 0.1 and cyrillic_share(text) > 0.5:
        return "the text must be in the language of the scene, not in Russian"
    return ""


def cut_to(text: str, tokens: int) -> str:
    """`text` cut at a sentence end to about `tokens` tokens (the last resort of the self-compression)."""
    size = tokens * CHARS_PER_TOKEN
    if len(text) <= size:
        return text
    head = text[:size]
    end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    return (head[:end + 1] if end > size // 2 else head.rstrip() + "…").strip()


# -- M5: «Слово и нити» ---------------------------------------------------------------------------

THREAD_KINDS = ("promise", "vow", "claim", "debt", "secret", "quest")
_NUMBER = {"type": ["integer", "string"]}
THREAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "new": {"type": "array", "items": {
            "type": "object",
            "properties": {"kind": {"type": "string"}, "who": {"type": "string"}, "to": {"type": "string"},
                           "quote": {"type": "string"}, "message_no": _NUMBER, "importance": _NUMBER,
                           "knows": {"type": "array", "items": {"type": "string"}}},
            "required": ["kind", "who", "quote", "message_no"]}},
        "changes": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "status": {"type": "string"}, "message_no": _NUMBER,
                           "note": {"type": "string"}, "told": {"type": "array", "items": {"type": "string"}}},
            "required": ["id", "status"]}},
    },
    "required": ["new", "changes"],
}
THREAD_KIND_RULES = {
    "promise": "promise: someone promised to do (or not to do) something, for someone or in general",
    "vow": "vow: a vow or a principle about oneself, a line they will never cross or always keep "
           "(«I will never do that!», «I never leave a friend behind»)",
    "claim": "claim: something a character stated about themselves or about the world that they or others may later "
             "be held to (who they are, what they own or can do, what is true)",
    "debt": "debt: a debt, a deal, a bargain or an agreement between characters (who owes what to whom)",
    "secret": "secret: a secret a character told someone; `knows` lists everyone who knows it now (the teller too)",
    "quest": "quest: a task or quest a character took on or was given",
}


def thread_lines(messages: list[dict[str, Any]], user_name: str = "") -> list[str]:
    """«№N Name: text» for each message (N = the number the chat shows; the user's own messages under the persona's
    name, as in the list of people), shortened like `chapter_lines`."""
    lines = [(int(m["depth"]) + 1, m.get("name") or (user_name if m["role"] == "user" and user_name else m["role"]),
              " ".join((m.get("content") or "").split())) for m in messages]
    total = sum(estimate_tokens(text) for _, _, text in lines)
    if total > MAX_INPUT_TOKENS and lines:
        each = max(40, MAX_INPUT_TOKENS // len(lines))
        lines = [(n, name, shorten(text, each)) for n, name, text in lines]
    return [f"№{n} {name}: {text}" for n, name, text in lines]


def thread_messages(lines: list[str], people: list[dict[str, str]], open_threads: list[dict[str, str]], *,
                    quests: bool) -> list[ChatMessage]:
    """The request of the threads step for one closed chapter.

    `lines`: the scene, numbered (`thread_lines`). `people`: [{code, name}] the characters and the user's persona.
    `open_threads`: [{code, kind, who, to, quote, status}] the records still open on this branch, so the model can
    say which of them the scene kept, broke or dropped (and to whom a secret was told)."""
    kinds = [THREAD_KIND_RULES[k] for k in THREAD_KINDS if k != "quest" or quests]
    system = "\n".join([
        "You keep the memory of a long roleplay chat: what the characters gave their word about. Read the scene "
        "(each message has its number) and answer two things.",
        "1. New records: what someone in this scene promised, vowed, claimed, agreed or owes, or a secret they told. "
        "Kinds:\n- " + "\n- ".join(kinds),
        "For each record: `kind`; `who` gave their word (a code from the list of people); `to` whom (a code, or \"\" "
        "for everyone or the world); `quote`: the EXACT words from that message, copied character for character, one "
        "or two sentences, never retold; `message_no`: the number of that message; `importance`: 1 minor, 2 matters, "
        "3 key to the story; `knows`: for a secret, the codes of everyone who knows it now.",
        "2. Changes of the open records listed (by their code T1, T2...): `status` kept (it was fulfilled), broken (it "
        "was broken), dropped (it no longer stands: released, cancelled, made moot), or active (it stands again); "
        "`message_no` where it happened; `note`: a few words why. A secret told to someone more: its code with "
        "`told`: the codes of the new listeners, and `status` active.",
        "Only what the scene really says. Small talk, wishes, threats in the heat of the moment and plans with no "
        "word given are not records. Do not repeat an open record as new. Empty lists are fine. No meta talk.",
        'Answer as JSON: {"new": [{"kind": "...", "who": "C1", "to": "C2", "quote": "...", "message_no": 12, '
        '"importance": 2, "knows": []}], "changes": [{"id": "T1", "status": "kept", "message_no": 15, "note": "..."}]}.',
    ])
    user = ["People:\n" + "\n".join(f"{p['code']}: {p['name']}" for p in people)]
    if open_threads:
        user.append("Open records:\n" + "\n".join(
            f"{t['code']}: {t['kind']} of {t['who']}{' to ' + t['to'] if t['to'] else ''} ({t['status']}): "
            f"«{t['quote']}»" for t in open_threads))
    user.append("The scene:\n" + "\n".join(lines))
    return [ChatMessage("system", system), ChatMessage("user", "\n\n".join(user))]
