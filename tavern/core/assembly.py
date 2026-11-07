"""Prompt assembly from a preset (stage 4а): blocks in the user's order, chat members, budget.

`assemble(PromptInput) -> Prompt`. The result keeps both what is sent (`messages`) and how it was
made (`entries`: one per block piece, card, message, with a source and a label), so the
"what the model saw" view can show and archive every part separately.

Author's notes (stage 4в): `notes` fired for this reply go `depth` messages from the end; notes
attached to history messages (`message["notes"]`, saved ones) follow their message and are dropped
with it. `Prompt.placed` says after which message each fired note landed, so the caller can attach
it there.

Lorebooks (stage 5): `lore` entries go into the preset's "Лор (до карточек)" block, or "после
карточек", next to the author's note (top/bottom), around the example dialogues, or at their own
depth, as their `rules.place` / `depth` say. A character's bubble with a `place` goes there too.
Whatever finds no block of its place in the preset goes just before the chat.

Budget (when `max_context_tokens` > 0), in this order until the prompt fits:
  1. drop the oldest chat messages, keeping at least MIN_HISTORY;
  2. drop member cards from the least important (highest importance number) up; the main
     character and the user's own character are never dropped;
  3. drop more old messages, down to the newest one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tavern.core import thinking
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
    # Lorebook entries chosen for this reply (core/retrieval.py FoundBlock), and the titles of those
    # that did not fit the lorebook budget (shown in "what the model saw").
    lore: list[Any] = field(default_factory=list)
    lore_dropped: list[str] = field(default_factory=list)
    # name -> (media type, base64) for pictures attached to messages; None when it cannot be read.
    load_image: Any = None
    # Two or more characters in the chat (stage 7): the primary member is the one speaking now.
    # Replies of the others go with their names and the model is told whom it writes for. `scene`
    # names the characters whose cards it does not get (name, short description or "").
    several: bool = False
    scene: list[tuple[str, str]] = field(default_factory=list)
    # Regex rules for the model (stage 11): the text the model gets instead of a history message's own, by
    # message id, and for lorebook entries by block id. The rewritten text goes through the same steps as
    # the original (the speaker's name in front, the old thinking around it, macros).
    rewritten: dict[str, str] = field(default_factory=dict)
    lore_rewritten: dict[str, str] = field(default_factory=dict)


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
    images: list[tuple[str, str]] = field(default_factory=list)  # pictures the model sees with it

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
    scene = ""
    if data.scene:
        scene = "Also in the scene:\n" + "\n".join(
            f"- {name}" + (f": {about}" if about else "") for name, about in data.scene)

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

    def found_entry(found: Any, kind: str, owner: str = "") -> Entry:
        item = found.block
        content = data.lore_rewritten.get(item["id"], item["content"]) if kind == "lore" else item["content"]
        return Entry(item["role"], sub(f"[{found.path}]\n{content}"), f"block:{item['id']}",
                     found.path, kind, owner=owner)

    def own_place(found: Any) -> str | None:
        return (found.block.get("rules") or {}).get("place") if found.block["depth"] is None else None

    # Entries with a place of their own (lorebook entries, and bubbles given a place), by place.
    # "card_before" is the preset's lore_before block, "card_after" its lore_after block.
    placed_at: dict[str, list[Entry]] = {}
    for member in data.members:
        for found in member.blocks:
            if own_place(found):
                placed_at.setdefault(own_place(found), []).append(found_entry(found, "block", member.character_id))
    for found in data.lore:
        entry = found_entry(found, "lore")
        if found.block["depth"] is not None:
            if entry.text.strip():
                injections.append(_Injection(found.block["depth"], entry))
        else:
            placed_at.setdefault(own_place(found) or "card_before", []).append(entry)

    def place_lore(where: str, block: dict[str, Any]) -> None:
        for entry in placed_at.pop(where, []):
            place(entry, block)

    def place_blocks(member: Member, block: dict[str, Any]) -> None:
        """The member's chosen tree blocks: next to its card, or at their own depth."""
        blocks_placed.add(member.character_id)
        for found in member.blocks:
            item = found.block
            if own_place(found):
                continue  # it went to its own place above
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
        elif kind == "thinking" and not data.impersonate and block["text"].strip():
            place(Entry(role, sub(thinking.frame(block["text"])), preset_source(block), block["name"], kind), block)
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
            if scene:
                place(Entry(role, sub(scene), "prompt:scene", "Остальные в сцене", "prompt"), block)
                scene = ""
        elif kind == "examples":
            place_lore("examples_top", block)
            for member in others:
                text = _examples(member.card)
                if text:
                    text = f"Example dialogue of {member.name}:\n{text}"
                place(Entry(role, sub(text), f"character:{member.character_id}:examples",
                            f"{block['name']}: {member.name}", kind, owner=member.character_id), block)
            place_lore("examples_bottom", block)
        elif kind == "lore_before":
            place_lore("card_before", block)
        elif kind == "lore_after":
            place_lore("card_after", block)

    # Presets that switch the card blocks off still get the character's tree blocks, just before
    # the chat (blocks with their own depth go inside it as usual).
    if scene:  # the preset has no character block switched on
        before.append(Entry("system", sub(scene), "prompt:scene", "Остальные в сцене", "prompt"))
    for member in data.members:
        if member.character_id not in blocks_placed:
            history_seen_before, history_seen = history_seen, False
            place_blocks(member, {"position": "top", "depth": 0})
            history_seen = history_seen_before
    note_depth = data.notes[0].depth if data.notes else 4  # where the author's note goes (ST's default)
    for entry in placed_at.pop("an_top", []):
        injections.append(_Injection(note_depth, entry))
    an_bottom = placed_at.pop("an_bottom", [])
    for left in placed_at.values():  # their block is not in the preset, or it is switched off
        before.extend(e for e in left if e.text.strip())
    placed_at.clear()

    history_block = next(b for b in preset["blocks"] if b["kind"] == "history")
    history = [m for m in data.history if not m.get("exclude_from_prompt")] if history_block["enabled"] else []
    options = history_block["history"]
    if options["mode"] == "last":
        history = history[-options["count"]:]
    saved_sources: dict[str, set[str]] = {}  # note id -> its attached copies in the history
    # The last N replies go with their thinking, each without the lines an earlier one already said.
    thought_rows = [m for m in history if m["role"] == "assistant"
                    and ((m.get("swipe_meta") or {}).get("reasoning") or "").strip()]
    thought_rows = thought_rows[-options["thoughts"]:] if options["thoughts"] else []
    past_thoughts = dict(zip((m["id"] for m in thought_rows),
                             thinking.compact([m["swipe_meta"]["reasoning"] for m in thought_rows])))

    def message_entry(m: dict[str, Any]) -> Entry:
        text = data.rewritten.get(m["id"], m["content"])
        together = (m.get("extra") or {}).get("together")  # a combined round names everyone inside
        if data.several and m["role"] == "assistant" and m.get("name") and m["name"] != char and not together:
            text = f"{m['name']}: {text}"  # another character's line, so the model does not take it as its own
        if past_thoughts.get(m["id"]):
            text = thinking.wrap(past_thoughts[m["id"]], text)
        entry = Entry(m["role"], sub(text), f"message:{m['id']}",
                      f"№{m['depth'] + 1} {m.get('name') or m['role']}", "message")
        if data.load_image is not None:
            for name in (m.get("swipe_meta") or {}).get("images") or []:
                picture = data.load_image(name)
                if picture is not None:
                    entry.images.append(picture)
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
    for entry in an_bottom:
        injections.append(_Injection(note_depth, entry))

    tail: list[Entry] = []
    if me is not None and not me.model_may_write and not data.impersonate:
        tail.append(Entry("system", sub(preset["prompts"]["no_write_for_me"]), "prompt:no_write_for_me",
                          f"Запрет: не писать за «{me.name}»", "prompt"))
    if data.several and not data.impersonate and (preset["prompts"].get("speaker_reply") or "").strip():
        tail.append(Entry("system", sub(preset["prompts"]["speaker_reply"]), "prompt:speaker_reply",
                          f"Отвечает: {char}", "prompt"))
    if data.impersonate:
        tail.append(Entry("user", sub(preset["prompts"]["impersonate"]), "prompt:impersonate",
                          "Написать за меня", "prompt"))
    elif (preset["prompts"].get("prefill") or "").strip():
        tail.append(Entry("assistant", sub(preset["prompts"]["prefill"]), "prompt:prefill",
                          "Начало ответа (prefill)", "prompt"))

    dropped: dict[str, Any] = {"messages": 0, "members": [], "lore": list(data.lore_dropped)}
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
            sent.append(ChatMessage(entry.role, entry.text, tuple(entry.images)))
        entry.msg = len(sent) - 1
        previous = entry
    return Prompt(entries=entries, messages=sent, tokens=_tokens(entries), dropped=dropped,
                  placed=placed)
