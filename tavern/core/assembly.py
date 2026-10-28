"""Prompt assembly from a preset (stage 4а): blocks in the user's order, chat members, budget.

`assemble(PromptInput) -> Prompt`. The result keeps both what is sent (`messages`) and how it was
made (`entries`: one per block piece, card, message, with a source and a label), so the
"what the model saw" view can show and archive every part separately.

Author's notes (stage 4в): `notes` fired for this reply go `depth` messages from the end; notes
attached to history messages (`message["notes"]`, saved ones) follow their message and are dropped
with it. `Prompt.placed` says after which message each fired note landed, so the caller can attach
it there.

Budget (when `max_context_tokens` > 0), in this order until the prompt fits:
  1. drop the oldest chat messages, keeping at least MIN_HISTORY;
  2. drop member cards from the least important (highest importance number) up; the main
     character and the user's own character are never dropped;
  3. drop more old messages, down to the newest one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tavern.core.prompt import DEFAULT_CHAR_NAME, START_TURN, estimate_tokens, substitute
from tavern.providers.base import ChatMessage

MIN_HISTORY = 6
CARD_SECTIONS = (("Description", "description"), ("Personality", "personality"),
                 ("Scenario", "scenario"))


@dataclass
class Member:
    character_id: str
    name: str
    card: dict[str, Any]
    depth: int | None = None
    importance: int = 5
    is_me: bool = False
    model_may_write: bool = True
    primary: bool = False
    # Blocks of this character's tree chosen for this reply (core/retrieval.py FoundBlock).
    blocks: list[Any] = field(default_factory=list)


@dataclass
class PromptInput:
    preset: dict[str, Any]
    preset_id: str
    members: list[Member]
    history: list[dict[str, Any]]
    user_name: str = "User"
    impersonate: bool = False  # the model writes the user's message
    max_context_tokens: int = 0
    notes: list[NoteInput] = field(default_factory=list)


@dataclass
class NoteInput:
    """An author's note fired for this reply (services/notes.py decides which)."""
    id: str
    text: str
    role: str = "system"
    depth: int = 4
    mode: str = "once"  # 'once' (not kept) | 'hidden' | 'visible' (kept in the history)
    title: str = ""


@dataclass
class Entry:
    role: str
    text: str
    source: str = ""
    label: str = ""
    kind: str = ""  # block kind, "message", or "prompt" for built-in instructions
    msg: int = 0  # index of the sent message this entry ended up in
    owner: str = ""  # character id whose card this is (for budget trimming)
    follow: list[Entry] = field(default_factory=list)  # notes attached to this message

    def as_dict(self) -> dict[str, Any]:
        return {"role": self.role, "text": self.text, "source": self.source,
                "label": self.label, "kind": self.kind, "msg": self.msg}


@dataclass
class Prompt:
    entries: list[Entry]
    messages: list[ChatMessage]
    tokens: int
    dropped: dict[str, Any] = field(default_factory=dict)
    # Fired notes: {note_id, mode, message_id (None = before the first message), text, role, title}
    placed: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Injection:
    depth: int  # how many chat messages from the end
    entry: Entry
    note: NoteInput | None = None


def card_text(card: dict[str, Any], prompts: dict[str, Any] | None = None) -> str:
    """Description, personality and scenario of a card.

    With the preset's ST-style templates (personality_format / scenario_format, e.g.
    "[{{char}}'s personality: {{personality}}]") the parts are written the way that preset expects;
    otherwise as titled sections.
    """
    prompts = prompts or {}
    personality_format = prompts.get("personality_format") or ""
    scenario_format = prompts.get("scenario_format") or ""
    if personality_format or scenario_format:
        description = (card.get("description") or "").strip()
        personality = (card.get("personality") or "").strip()
        scenario = (card.get("scenario") or "").strip()
        parts = [
            description,
            (personality_format or "{{personality}}").replace("{{personality}}", personality) if personality else "",
            (scenario_format or "{{scenario}}").replace("{{scenario}}", scenario) if scenario else "",
        ]
        return "\n\n".join(p for p in parts if p.strip())
    parts = []
    for title, key in CARD_SECTIONS:
        body = (card.get(key) or "").strip()
        if body:
            parts.append(f"{title}:\n{body}")
    return "\n\n".join(parts)


def _examples(card: dict[str, Any]) -> str:
    text = (card.get("mes_example") or "").strip()
    lines = [line for line in text.splitlines() if line.strip().upper() != "<START>"]
    return "\n".join(lines).strip()


def _cost(entry: Entry) -> int:
    return estimate_tokens(entry.text) + sum(estimate_tokens(f.text) for f in entry.follow)


def _tokens(entries: list[Entry]) -> int:
    return sum(_cost(e) for e in entries)


def assemble(data: PromptInput) -> Prompt:
    preset = data.preset
    me = next((m for m in data.members if m.is_me), None)
    others = [m for m in data.members if not m.is_me]
    primary = next((m for m in others if m.primary), others[0] if others else None)
    char = primary.name if primary else DEFAULT_CHAR_NAME
    user = me.name if me else data.user_name
    several = len(others) > 1
    prefer_card = preset.get("prefer_card_prompts", True)

    # Macros presets use to pull cards into their own blocks (ST-style), and {{setvar}} storage
    # shared by every text of this prompt in order.
    primary_card = primary.card if primary else {}
    values = {
        "description": (primary_card.get("description") or "").strip(),
        "personality": (primary_card.get("personality") or "").strip(),
        "scenario": (primary_card.get("scenario") or "").strip(),
        "mesExamples": _examples(primary_card),
        "persona": (me.card.get("description") or "").strip() if me else "",
        "summary": "",  # filled by summarization (stage 11)
    }
    variables: dict[str, str] = {}

    def sub(text: str) -> str:
        return substitute(text, char=char, user=user, values=values, variables=variables)

    def preset_source(block: dict[str, Any]) -> str:
        return f"preset:{data.preset_id}:{block['id']}"

    before: list[Entry] = []
    after: list[Entry] = []
    injections: list[_Injection] = []
    history_seen = False

    def place(entry: Entry, block: dict[str, Any], member_depth: int | None = None) -> None:
        if not entry.text.strip():
            return
        if member_depth is not None:
            injections.append(_Injection(member_depth, entry))
        elif block["position"] == "depth":
            injections.append(_Injection(block["depth"], entry))
        else:
            (after if history_seen else before).append(entry)

    blocks_placed: set[str] = set()

    def place_blocks(member: Member, block: dict[str, Any]) -> None:
        """The member's chosen tree blocks: next to its card, or at their own depth."""
        blocks_placed.add(member.character_id)
        for found in member.blocks:
            item = found.block
            entry = Entry(item["role"], sub(f"[{found.path}]\n{item['content']}"), f"block:{item['id']}",
                          found.path, "block", owner=member.character_id)
            if item["depth"] is not None:
                if entry.text.strip():
                    injections.append(_Injection(item["depth"], entry))
            else:
                place(entry, block, member.depth)

    def card_with_override(block: dict[str, Any], key: str) -> tuple[str, str]:
        own = (primary.card.get(key) or "").strip() if primary else ""
        if prefer_card and own:
            return own.replace("{{original}}", block["text"]), f"character:{primary.character_id}:{key}"
        return block["text"], preset_source(block)

    for block in preset["blocks"]:
        if not block["enabled"]:
            if block["kind"] == "history":
                history_seen = True
            continue
        kind, role = block["kind"], block["role"]
        if kind == "history":
            history_seen = True
        elif kind in ("main", "post_history"):
            text, source = card_with_override(block, "system_prompt" if kind == "main" else
                                              "post_history_instructions")
            place(Entry(role, sub(text), source, block["name"], kind), block)
        elif kind == "text":
            place(Entry(role, sub(block["text"]), preset_source(block), block["name"], kind), block)
        elif kind == "persona" and me is not None:
            text = card_text(me.card, preset["prompts"])
            if text:
                text = f"{user} (played by the user):\n{text}"
            place(Entry(role, sub(text), f"character:{me.character_id}:card",
                        f"{block['name']}: {me.name}", kind, owner=me.character_id), block, me.depth)
            place_blocks(me, block)
        elif kind == "cast":
            for member in sorted(others, key=lambda m: m.importance):
                text = card_text(member.card, preset["prompts"])
                if several and text:
                    text = f"### {member.name}\n{text}"
                place(Entry(role, sub(text), f"character:{member.character_id}:card",
                            member.name, kind, owner=member.character_id), block, member.depth)
                place_blocks(member, block)
        elif kind == "examples":
            for member in others:
                text = _examples(member.card)
                if text:
                    text = f"Example dialogue of {member.name}:\n{text}"
                place(Entry(role, sub(text), f"character:{member.character_id}:examples",
                            f"{block['name']}: {member.name}", kind, owner=member.character_id), block)
        # lore_before / lore_after are filled by lorebooks (stage 5)

    # Presets that switch the card blocks off still get the character's tree blocks, just before
    # the chat (blocks with their own depth go inside it as usual).
    for member in data.members:
        if member.character_id not in blocks_placed:
            history_seen_before, history_seen = history_seen, False
            place_blocks(member, {"position": "top", "depth": 0})
            history_seen = history_seen_before

    history_block = next(b for b in preset["blocks"] if b["kind"] == "history")
    history = [m for m in data.history if not m.get("exclude_from_prompt")] if history_block["enabled"] else []
    options = history_block["history"]
    if options["mode"] == "last":
        history = history[-options["count"]:]
    saved_sources: dict[str, set[str]] = {}  # note id -> its attached copies in the history

    def message_entry(m: dict[str, Any]) -> Entry:
        entry = Entry(m["role"], sub(m["content"]), f"message:{m['id']}",
                      f"№{m['depth'] + 1} {m.get('name') or m['role']}", "message")
        for n in m.get("notes") or []:
            if not n["text"].strip():
                continue
            source = f"message_note:{n['id']}"
            if n.get("note_id"):
                saved_sources.setdefault(n["note_id"], set()).add(source)
            entry.follow.append(Entry(
                n["role"], sub(n["text"]), source,
                f"Заметка у №{m['depth'] + 1}" + (f": {n['title']}" if n.get("title") else ""), "note"))
        return entry

    messages = [message_entry(m) for m in history]
    if options["max_tokens"]:
        kept: list[Entry] = []
        left = options["max_tokens"]
        for entry in reversed(messages):
            cost = _cost(entry)
            if kept and cost > left:
                break
            kept.append(entry)
            left -= cost
        messages = list(reversed(kept))

    for note in data.notes:
        entry = Entry(note.role, sub(note.text), f"note:{note.id}",
                      "Заметка" + (f": {note.title}" if note.title else ""), "note")
        if entry.text.strip():
            injections.append(_Injection(note.depth, entry, note))

    tail: list[Entry] = []
    if me is not None and not me.model_may_write and not data.impersonate:
        tail.append(Entry("system", sub(preset["prompts"]["no_write_for_me"]), "prompt:no_write_for_me",
                          f"Запрет: не писать за «{me.name}»", "prompt"))
    if data.impersonate:
        tail.append(Entry("user", sub(preset["prompts"]["impersonate"]), "prompt:impersonate",
                          "Написать за меня", "prompt"))
    elif (preset["prompts"].get("prefill") or "").strip():
        tail.append(Entry("assistant", sub(preset["prompts"]["prefill"]), "prompt:prefill",
                          "Начало ответа (prefill)", "prompt"))

    dropped: dict[str, Any] = {"messages": 0, "members": []}
    if data.max_context_tokens:
        def total() -> int:
            return (_tokens(before) + _tokens(after) + _tokens(messages) + _tokens(tail)
                    + sum(_cost(i.entry) for i in injections))

        def drop_oldest(keep: int) -> None:
            while len(messages) > keep and total() > data.max_context_tokens:
                messages.pop(0)
                dropped["messages"] += 1

        drop_oldest(MIN_HISTORY)
        protected = {m.character_id for m in data.members if m.is_me or m is primary}
        for member in sorted(others, key=lambda m: -m.importance):
            if total() <= data.max_context_tokens:
                break
            if member.character_id in protected:
                continue
            before[:] = [e for e in before if e.owner != member.character_id]
            after[:] = [e for e in after if e.owner != member.character_id]
            injections[:] = [i for i in injections if i.entry.owner != member.character_id]
            dropped["members"].append(member.name)
        drop_oldest(1)

    # Depth d goes before the last d messages; several at one place keep their block order.
    # A fired note lands after a message; a saved note already attached there (the reply is being
    # regenerated) is not sent twice.
    slots: list[list[Entry]] = [[] for _ in range(len(messages) + 1)]
    placed: list[dict[str, Any]] = []
    for injection in injections:
        index = max(0, len(messages) - injection.depth)
        note = injection.note
        if note is not None:
            after_message = messages[index - 1] if index > 0 else None
            message_id = after_message.source.removeprefix("message:") if after_message else None
            if note.mode != "once" and after_message is not None and any(
                f.source in saved_sources.get(note.id, ()) for f in after_message.follow
            ):
                continue
            placed.append({"note_id": note.id, "mode": note.mode, "message_id": message_id,
                           "text": injection.entry.text, "role": note.role, "title": note.title})
        slots[index].append(injection.entry)
    chat_part: list[Entry] = []
    for index, slot in enumerate(slots):
        chat_part.extend(slot)
        if index < len(messages):
            chat_part.append(messages[index])
            chat_part.extend(messages[index].follow)

    entries = [*before, *chat_part, *after, *tail]
    if entries and not any(e.role == "user" for e in entries):
        first_chat = len(before)
        entries.insert(first_chat, Entry("user", START_TURN, "prompt:start", "Начало", "prompt"))

    sent: list[ChatMessage] = []
    previous: Entry | None = None
    for entry in entries:
        mergeable = (previous is not None and entry.role == "system" and previous.role == "system"
                     and entry.kind != "message" and previous.kind != "message")
        if mergeable:
            last = sent[-1]
            sent[-1] = ChatMessage("system", f"{last.content}\n\n{entry.text}")
        else:
            sent.append(ChatMessage(entry.role, entry.text))
        entry.msg = len(sent) - 1
        previous = entry
    return Prompt(entries=entries, messages=sent, tokens=_tokens(entries), dropped=dropped,
                  placed=placed)
