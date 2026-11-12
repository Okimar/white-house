"""Who speaks next in a chat, and what the model knows about the characters who do not.

Any chat may have any number of characters: that is the normal state, not a mode. With one
character it simply answers; the rules below matter once there are two or more. Characters join
and leave through services/cast.py (the UI, plugins and agents alike: an agent may bring a new
character into the scene with `cast.add_member` and let it speak with `speaker=`).

Settings of a chat (`chats.meta["speakers"]`):
    {"order": "natural" | "turns" | "model" | "manual" | "all",
     "others": "none" | "names" | "blurbs" | "full",
     "talk_limit": 4,                                  replies of «пусть поговорят» (1..50)
     "together": "finished" | "list" | "one",          how an all-at-once round is shown
     "narrator": {"enabled": false, "name": "Рассказчик", "connection_id": "", "model": ""}}

order (who answers the user's message):
    natural  the member named in the last message; nobody named: drawn by talkativeness
    turns    the members one after another, in their list order
    model    a short extra request asks the model who speaks (natural when it fails)
    manual   nobody answers by themselves; the user picks ("ответь ты")
    all      everyone who may speak answers at once (a round, stage 7б): each sees the chat up to the
             round, not the others' replies of the same round
together (an all-at-once round): separate messages in the order they finished, separate messages
    in the list order, or one message with every reply under its name. With the narrator on, one
    more request turns the round into one scene (a system message under the narrator's name), by
    its own connection and model when set (empty = the chat's).
others (what the model sees, besides the speaker's card and "me"):
    none     nothing; names: their names; blurbs: names with the member's short description;
    full     every card, placed by depth and trimmed by importance (stage 4а)

A muted member never speaks by themselves and is left out of the prompt. "Ответь ты" (an explicit
speaker) works in every order, for a muted member too.
"""

from __future__ import annotations

import random
import re
from typing import Any

from tavern.context import AppContext
from tavern.core.llm import LLMClient
from tavern.core.retrieval import stem
from tavern.errors import AppError, Conflict
from tavern.providers.base import ChatMessage

KEY = "speakers"
ORDERS = ("natural", "turns", "model", "manual", "all")
OTHERS = ("none", "names", "blurbs", "full")
TOGETHER = ("finished", "list", "one")
MAX_TALK = 50
NARRATOR_NAME = "Рассказчик"
RECENT = 12  # messages the model reads when it chooses the speaker
RECENT_CHARS = 600


def defaults() -> dict[str, Any]:
    return {"order": "natural", "others": "none", "talk_limit": 4, "together": "finished",
            "narrator": {"enabled": False, "name": NARRATOR_NAME, "connection_id": "", "model": ""}}


def check(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AppError("Speaker settings must be an object", code="bad_speakers")
    out = defaults()
    for key, allowed in (("order", ORDERS), ("others", OTHERS), ("together", TOGETHER)):
        value = raw.get(key, out[key])
        if value not in allowed:
            raise AppError(f"{key}: one of {', '.join(allowed)}", code="bad_speakers")
        out[key] = value
    limit = raw.get("talk_limit", out["talk_limit"])
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_TALK:
        raise AppError(f"talk_limit: 1..{MAX_TALK}", code="bad_speakers")
    out["talk_limit"] = limit
    narrator = {**out["narrator"], **(raw.get("narrator") or {})}
    if not isinstance(raw.get("narrator", {}), dict) or set(narrator) != set(out["narrator"]):
        raise AppError("narrator: {enabled, name, connection_id, model}", code="bad_speakers")
    for key in ("name", "connection_id", "model"):
        if not isinstance(narrator[key], str) or len(narrator[key]) > 200:
            raise AppError(f"narrator.{key} must be text", code="bad_speakers")
    out["narrator"] = {"enabled": bool(narrator["enabled"]), "name": narrator["name"].strip() or NARRATOR_NAME,
                       "connection_id": narrator["connection_id"], "model": narrator["model"].strip()}
    return out


def get_settings(chat: dict[str, Any]) -> dict[str, Any]:
    try:
        return check(chat["meta"].get(KEY) or {})
    except AppError:
        return defaults()


def save_settings(ctx: AppContext, chat_id: str, raw: Any) -> dict[str, Any]:
    """Change some of the settings (missing keys keep their value)."""
    chat = ctx.chats.get_chat(chat_id)
    if not isinstance(raw, dict):
        raise AppError("Speaker settings must be an object", code="bad_speakers")
    current = get_settings(chat)
    if isinstance(raw.get("narrator"), dict):
        raw = {**raw, "narrator": {**current["narrator"], **raw["narrator"]}}
    settings = check({**current, **raw})
    ctx.chats.update_meta(chat_id, {**chat["meta"], KEY: settings})
    return settings


def characters(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [m for m in members if not m["is_me"] and m.get("character_id")]  # an empty place never speaks


def several(members: list[dict[str, Any]]) -> bool:
    """Two or more characters (not \"me\"): replies go with names and the model is told whom it writes for."""
    return len(characters(members)) > 1


def may_speak(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Who may speak by themselves: characters that are not muted."""
    return [m for m in characters(members) if not m["muted"]]


def author(message: dict[str, Any], members: list[dict[str, Any]]) -> str | None:
    """The member who wrote a reply: its character_id, or by name for older or imported messages."""
    if message["role"] != "assistant":
        return None
    cast = characters(members)
    if message.get("character_id") in {m["character_id"] for m in cast}:
        return message["character_id"]
    return next((m["character_id"] for m in cast if m["name"] == message.get("name")), None)


def last_speaker(history: list[dict[str, Any]], members: list[dict[str, Any]]) -> str | None:
    for message in reversed(history):
        found = author(message, members)
        if found:
            return found
    return None


# Case endings a name takes in a sentence (Russian; English plural/possessive "s"). A name is matched by
# itself or its stem with one of these, not by the stem with anything after it: «Ник» must not be found
# in «никто», «Вера» in «верно», «Ли» in the particle «ли». Longest first so the alternation is greedy.
NAME_ENDINGS = sorted({"а", "я", "у", "ю", "е", "ы", "и", "о", "ь", "ой", "ей", "ою", "ею", "ом", "ем", "ём",
                       "ам", "ям", "ами", "ями", "ах", "ях", "ов", "ев", "s"}, key=len, reverse=True)


def name_pattern(word: str) -> re.Pattern[str] | None:
    """How a name's word is found in a message: the first letter as written (a capital stays a capital,
    so common words that share the stem do not count), the rest in any case, then one case ending."""
    words = re.findall(r"\w+", word)
    if not words:
        return None
    word = words[0]
    first = re.escape(word[0])
    if word[0].isalpha() and word[0].lower() != word[0].upper():
        first = re.escape(word[0]) if word[0].isupper() else f"[{re.escape(word[0])}{re.escape(word[0].upper())}]"
    base = word[: max(len(stem(word)), 1)]
    endings = "|".join(re.escape(e) for e in NAME_ENDINGS)
    tail = f"(?:{re.escape(word[1:])}|{re.escape(base[1:])}(?:{endings})?)"
    return re.compile(rf"(?<!\w){first}(?i:{tail})(?!\w)")


def named(text: str, pool: list[dict[str, Any]]) -> list[str]:
    """Members named in `text` (first word of the name with a case ending), in the order they appear."""
    found: list[tuple[int, str]] = []
    for member in pool:
        word = (member["name"].split() or [""])[0]
        pattern = name_pattern(word) if word else None
        match = pattern.search(text) if pattern else None
        if match:
            found.append((match.start(), member["character_id"]))
    return [character_id for _, character_id in sorted(found)]


def choose(
    settings: dict[str, Any], members: list[dict[str, Any]], history: list[dict[str, Any]],
    rng: random.Random | None = None,
) -> str | None:
    """Who answers now by the chat's order ("model" and "manual" are decided by the caller and
    land here as natural). None: every character is muted."""
    rng = rng or random.Random()
    pool = may_speak(members)
    if len(pool) <= 1:
        return pool[0]["character_id"] if pool else None
    if settings["order"] == "turns":
        ids = [m["character_id"] for m in characters(members)]  # muted ones too: they are skipped over
        last = last_speaker(history, members)
        start = ids.index(last) + 1 if last in ids else 0
        allowed = {m["character_id"] for m in pool}
        return next(i for i in ids[start:] + ids[:start] if i in allowed)
    last_message = history[-1] if history else None
    wrote_last = author(last_message, members) if last_message else None
    mentioned = [i for i in named(last_message["content"], pool) if i != wrote_last] if last_message else []
    if mentioned:
        return mentioned[0]
    others = [m for m in pool if m["character_id"] != wrote_last] or pool
    willing = [m for m in others if rng.random() * 100 < m["talkativeness"]]
    if willing:
        return rng.choice(willing)["character_id"]
    most = max(m["talkativeness"] for m in others)
    return rng.choice([m for m in others if m["talkativeness"] == most])["character_id"]


def require(ctx: AppContext, chat_id: str, speaker: str) -> dict[str, Any]:
    """The member asked to answer ("ответь ты"), checked."""
    member = next((m for m in ctx.members.list(chat_id) if m["character_id"] == speaker), None)
    if member is None or member["is_me"]:
        raise AppError("That character is not in this chat (or is you)", code="bad_speaker")
    return member


def nobody() -> Conflict:
    return Conflict("Все персонажи этого чата молчат: включите кого-нибудь в «Участниках».",
                    code="nobody_to_answer")


async def ask_model(llm: LLMClient, members: list[dict[str, Any]], history: list[dict[str, Any]]) -> str | None:
    """One short request: which member speaks next. None when the answer is not one of them."""
    pool = may_speak(members)
    if len(pool) < 2:
        return pool[0]["character_id"] if pool else None
    names = list(dict.fromkeys(m["name"] for m in pool))
    cast = "\n".join(f"- {m['name']}" + (f": {m['blurb']}" if m["blurb"] else "") for m in pool)
    talk = "\n".join(f"{m.get('name') or m['role']}: {m['content'][:RECENT_CHARS]}"
                     for m in history[-RECENT:] if not m.get("exclude_from_prompt"))
    prompt = (f"Characters in this roleplay scene:\n{cast}\n\nThe conversation so far:\n{talk or '(nothing yet)'}"
              "\n\nWhich of the characters should speak next, so that the scene goes on naturally?")
    value = await llm.generate_json(
        [ChatMessage("user", prompt)],
        schema={"type": "object", "properties": {"name": {"enum": names}}, "required": ["name"]},
    )
    return next((m["character_id"] for m in pool if m["name"] == value.get("name")), None)


def scene(members: list[dict[str, Any]], speaker: str | None, others: str) -> list[tuple[str, str]]:
    """(name, short description) of the other characters the model is told about."""
    if others in ("none", "full"):
        return []
    return [(m["name"], m["blurb"] if others == "blurbs" else "")
            for m in may_speak(members) if m["character_id"] != speaker]


def narrator_prompt(
    instruction: str, members: list[dict[str, Any]], history: list[dict[str, Any]], round_: list[dict[str, Any]],
) -> list[ChatMessage]:
    """What the narrator gets after an all-at-once round: who is in the scene, the recent chat, the
    replies of the round, and the preset's narrator instruction."""
    cast = "\n".join(f"- {m['name']}" + (f": {m['blurb']}" if m["blurb"] else "") for m in may_speak(members))
    talk = "\n".join(f"{m.get('name') or m['role']}: {m['content'][:RECENT_CHARS]}"
                     for m in history[-RECENT:] if not m.get("exclude_from_prompt"))
    acts = "\n\n".join(f"{m['name']}: {m['content']}" for m in round_)
    return [*([ChatMessage("system", instruction)] if instruction.strip() else []),
            ChatMessage("user", f"Characters in the scene:\n{cast}\n\nBefore this round:\n{talk or '(nothing yet)'}"
                                f"\n\nThis round, all at the same time:\n{acts}")]

