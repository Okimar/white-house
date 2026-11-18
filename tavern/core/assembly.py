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
An entry at a depth, or one from a SillyTavern file, goes bare (no "[Book › Title]" line); texts at one depth are
joined SillyTavern's way (`_Injection`, CAT_*), and neighbouring system texts only when the preset's
`squash_system` is on. A SillyTavern "outlet" entry is never sent.

Plugin pieces (ADR 0019): `PromptInput.extras` are `Extra`s a plugin adds in `chat.before_generate` (the memory:
a plot so far, found fragments of old messages, a character's «Развитие»). Where each goes is its `place`; the
preset block kind `memory` marks the spot of place "memory" (without such a block: just before the chat; a
switched-off block means no memory). HISTORY FIRST, memory for the rest: an Extra with `covers` (first and last
message id) or `if_dropped` is *conditional*: it is sent only when the history messages it stands for were really
left out of the prompt, and only while it fits `memory_reserve` tokens kept for that. When the whole history fits,
no conditional extra is sent and the prompt is what it was without the plugin. A conditional extra always goes to
the memory spot.

Budget (when `max_context_tokens` > 0), in this order until the prompt fits:
  1. drop the oldest chat messages, keeping at least MIN_HISTORY and the live tail (`tail_from`: the message the
     protected tail starts at), and leaving `memory_reserve` tokens free when conditional extras wait;
  2. drop unconditional extras by `drop_rank` (lowest first);
  3. drop member cards from the least important (highest importance number) up; the main
     character and the user's own character are never dropped;
  4. drop more old messages, down to the newest one;
  then the conditional extras are added while they fit: they are CONSIDERED in the plugin's order (its ranking: the
  best first, so the reserve goes to them), and PLACED by `order` when given (story order), else in that order.
  Extras sharing a non-empty `group` are alternatives: only the first one that qualifies and fits is sent (a
  chapter's summary when the whole chapter was left out, else its pieces).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tavern.core import thinking
from tavern.core.prompt import DEFAULT_CHAR_NAME, START_TURN, estimate_tokens, substitute
from tavern.core.presets import DEFAULT_DEPTH_ORDER, ORIGIN_ST, when_texts
from tavern.core.retrieval import block_order, from_st
from tavern.providers.base import ChatMessage

MIN_HISTORY = 6
CARD_SECTIONS = (("Description", "description"), ("Personality", "personality"),
                 ("Scenario", "scenario"))
# Connection kinds the preset's prefill (a last assistant turn) goes to: Claude, as in SillyTavern, and the text
# mode, where it is the start of the reply. OpenAI-compatible and Gemini chats never get a trailing assistant turn.
PREFILL_KINDS = ("anthropic", "text")
START_RE = re.compile(r"^\s*<start>\s*$", re.IGNORECASE | re.MULTILINE)


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
class Extra:
    """A plugin's piece of the prompt (see the module docstring)."""

    text: str
    source: str  # archive key, "plugin:memory:chapter:<id>"; versions are kept like any source
    label: str  # shown in «Что видела модель»
    place: str = "memory"  # "memory" | "after_card" | "card_before" | "card_after" | "depth" | "an_top"
    owner: str = ""  # character id: for "after_card" (that member's card) and for trimming with that member
    depth: int = 0  # for place "depth"
    role: str = "system"
    kind: str = "memory"  # Entry.kind, for the viewer's grouping
    drop_rank: int = 50  # unconditional extras go under a short context in this order (lower first)
    covers: tuple[str, str] | None = None  # first, last message id: sent ONLY if all of them were left out
    if_dropped: bool = False  # sent only if any history message was left out
    order: int | None = None  # conditional extras: their place among the others (story order); None = list order
    group: str = ""  # conditional extras: alternatives of one thing; only the first that qualifies and fits goes


EXTRA_PLACES = ("memory", "after_card", "card_before", "card_after", "depth", "an_top")


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
    thoughts_rewritten: dict[str, str] = field(default_factory=dict)  # old reasoning, by message id
    # Plugin pieces (ADR 0019): extras, the labels a plugin left out itself (its own budget), the message the
    # protected live tail starts at, tokens kept free for conditional extras, and macro values ({{summary}}).
    extras: list[Extra] = field(default_factory=list)
    extras_dropped: list[str] = field(default_factory=list)
    tail_from: str | None = None
    memory_reserve: int = 0
    values: dict[str, str] = field(default_factory=dict)
    # The connection's kind ("openai", "gemini", "anthropic"), or "text" for the text mode: the prefill goes only to
    # PREFILL_KINDS. Empty (unknown) = no prefill.
    provider_kind: str = ""
    # Wave 5: the kind of generation (core/presets.GENERATION_KINDS) for blocks with `triggers`, and «Дописать»: the
    # id of the history message being continued (it goes last, followed by the preset's continue_nudge, or as the
    # start of the reply with `continue_prefill`).
    generation_kind: str = "normal"
    continue_id: str = ""


@dataclass
class NoteInput:
    """An author's note fired for this reply (services/notes.py decides which)."""
    id: str
    text: str
    role: str = "system"
    depth: int = 4
    mode: str = "once"  # 'once' (not kept) | 'hidden' | 'visible' (kept in the history)
    title: str = ""
    place: str = "depth"  # 'depth' (in the chat) | 'before_main' | 'after_main' (SillyTavern's positions)


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
    join: bool = False  # one message with the entry before it (same depth, order and role), one line break between
    alone: bool = False  # never squashed with a neighbour (the new-chat line, example separators, the group nudge)
    name: str = ""  # a named example line ("example_user"); never squashed
    speaker: str = ""  # who says it, for services without names (ChatMessage.speaker)

    def as_dict(self) -> dict[str, Any]:
        out = {"role": self.role, "text": self.text, "source": self.source,
               "label": self.label, "kind": self.kind, "msg": self.msg}
        if self.name:
            out["name"] = self.name
        return out


@dataclass
class Prompt:
    entries: list[Entry]
    messages: list[ChatMessage]
    tokens: int
    dropped: dict[str, Any] = field(default_factory=dict)
    # Fired notes: {note_id, mode, message_id (None = before the first message), text, role, title}
    placed: list[dict[str, Any]] = field(default_factory=list)
    # Who speaks: {user, char, group}, for the services' post-processing that writes names in front of lines.
    names: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Injection:
    depth: int  # how many chat messages from the end
    entry: Entry
    note: NoteInput | None = None
    order: int = DEFAULT_DEPTH_ORDER  # a preset block's in-chat order (SillyTavern's injection_order)
    cat: int = 0  # inside one message: CAT_PRESET, CAT_EXTRA, CAT_NOTE, CAT_CHAR_NOTE, CAT_CARD, CAT_LORE
    sub: int = 0  # inside one kind: the entry's «Order» (lore), or above / below the author's note


# Texts at one depth, the way SillyTavern builds them: one message per (order, role); messages by order (lower
# first), then assistant, user, system (system nearest the chat message that follows); inside one message the
# preset's texts, then a plugin's memory, then the author's note (with the lore put above / below it), then a
# character's note (card depth_prompt), then a card, then lorebook entries and bubbles by their order (lower first).
# Joined with one line break.
CAT_PRESET, CAT_EXTRA, CAT_NOTE, CAT_CHAR_NOTE, CAT_CARD, CAT_LORE = range(6)
ROLE_RANK = {"assistant": 0, "user": 1, "system": 2}


def character_note(card: dict[str, Any]) -> tuple[str, int, str] | None:
    """A card's «Character's Note» (`extensions.depth_prompt`: prompt, depth — 4 by default — and role — system by
    default) as (text, depth, role), or None when it has no text."""
    extensions = card.get("extensions") if isinstance(card.get("extensions"), dict) else {}
    note = extensions.get("depth_prompt") if isinstance(extensions.get("depth_prompt"), dict) else {}
    text = note.get("prompt") if isinstance(note.get("prompt"), str) else ""
    if not text.strip():
        return None
    depth = note.get("depth")
    depth = depth if isinstance(depth, int) and not isinstance(depth, bool) and 0 <= depth <= 1000 else 4
    role = note.get("role") if note.get("role") in ("system", "user", "assistant") else "system"
    return text, depth, role


def card_parts(card: dict[str, Any], prompts: dict[str, Any] | None = None, *, bare: bool = False) -> list[str]:
    """Description, personality and scenario of a card, the non-empty ones.

    With the preset's ST-style templates (personality_format / scenario_format, e.g.
    "[{{char}}'s personality: {{personality}}]"), or `bare` (a preset from SillyTavern, where an empty format means
    the field as it is), the parts are written that way; otherwise (our own presets) as titled sections.
    """
    prompts = prompts or {}
    personality_format = prompts.get("personality_format") or ""
    scenario_format = prompts.get("scenario_format") or ""
    if bare or personality_format or scenario_format:
        description = (card.get("description") or "").strip()
        personality = (card.get("personality") or "").strip()
        scenario = (card.get("scenario") or "").strip()
        parts = [
            description,
            (personality_format or "{{personality}}").replace("{{personality}}", personality) if personality else "",
            (scenario_format or "{{scenario}}").replace("{{scenario}}", scenario) if scenario else "",
        ]
        return [p for p in parts if p.strip()]
    parts = []
    for title, key in CARD_SECTIONS:
        body = (card.get(key) or "").strip()
        if body:
            parts.append(f"{title}:\n{body}")
    return parts


def card_text(card: dict[str, Any], prompts: dict[str, Any] | None = None) -> str:
    """The card's parts as one text (our own presets: one entry per member)."""
    return "\n\n".join(card_parts(card, prompts))


def _examples(card: dict[str, Any]) -> str:
    text = (card.get("mes_example") or "").strip()
    lines = [line for line in text.splitlines() if line.strip().upper() != "<START>"]
    return "\n".join(lines).strip()


def example_dialogues(text: str, user: str, char: str) -> list[list[tuple[str, str]]]:
    """A card's example dialogue SillyTavern's way: one list per `<START>` block, each a list of
    ("user" | "char", line text without the leading "Name:"). A line that starts with neither name continues the
    one before it (or starts a line of the character). `text` has its macros already filled in."""
    blocks: list[list[tuple[str, str]]] = []
    starts = {"user": f"{user}:".casefold(), "char": f"{char}:".casefold()}
    for raw in START_RE.split(text):
        lines: list[tuple[str, str]] = []
        for line in raw.strip().splitlines():
            who = next((k for k, start in starts.items() if line.casefold().startswith(start)), None)
            if who is not None:
                lines.append((who, line[len(starts[who]):].strip()))
            elif lines:
                lines[-1] = (lines[-1][0], f"{lines[-1][1]}\n{line}")
            elif line.strip():
                lines.append(("char", line.strip()))
        lines = [(who, said.strip()) for who, said in lines if said.strip()]
        if lines:
            blocks.append(lines)
    return blocks


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
    prompts = preset["prompts"]
    # A preset from SillyTavern gets its texts the way SillyTavern sends them: card parts, persona and examples
    # bare (no headers of ours), each its own message. Our own presets keep the titled sections.
    st_way = preset.get("origin") == ORIGIN_ST

    # Macros presets use to pull cards into their own blocks (ST-style), and {{setvar}} storage
    # shared by every text of this prompt in order.
    primary_card = primary.card if primary else {}
    values = {
        "description": (primary_card.get("description") or "").strip(),
        "personality": (primary_card.get("personality") or "").strip(),
        "scenario": (primary_card.get("scenario") or "").strip(),
        "mesExamples": _examples(primary_card),
        "persona": (me.card.get("description") or "").strip() if me else "",
        "summary": "",  # a plugin sets it (PromptInput.values), e.g. the memory's «Сюжет до сих пор»
        # {{group}}: the characters of the chat (cards sent or not), as in the group new-chat line
        "group": ", ".join(dict.fromkeys([*(m.name for m in others), *(name for name, _ in data.scene)])),
    }
    values.update(data.values)
    variables: dict[str, str] = {}

    def sub(text: str) -> str:
        # carriage returns (Windows line ends in cards and imported texts) are dropped, as SillyTavern does
        # (also from what the macros bring in: a card's {{description}} inside a preset text)
        return substitute(text, char=char, user=user, values=values, variables=variables).replace("\r", "")

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

    def place(entry: Entry, block: dict[str, Any], member_depth: int | None = None, *, cat: int = CAT_CARD,
              sub: int = 0) -> None:
        if not entry.text.strip():
            return
        if member_depth is not None:
            injections.append(_Injection(member_depth, entry, cat=cat, sub=sub))
        elif block["position"] == "depth":
            injections.append(_Injection(block["depth"], entry, order=block.get("order", DEFAULT_DEPTH_ORDER),
                                         cat=CAT_PRESET))
        else:
            (after if history_seen else before).append(entry)

    blocks_placed: set[str] = set()
    # Author's notes placed outside the chat go with the main prompt (a switched-off main: at the very top).
    def note_entry(note: NoteInput) -> Entry:
        return Entry(note.role, sub(note.text), f"note:{note.id}", "Заметка" + (f": {note.title}" if note.title else ""),
                     "note")

    outside_notes: dict[str, list[Entry]] = {"before_main": [], "after_main": []}
    for note in data.notes:
        if note.place in outside_notes:
            entry = note_entry(note)
            if entry.text.strip():
                outside_notes[note.place].append(entry)
    main_placed = False
    examples_mode = "room"  # the examples block's choice, read when it is placed (presets.EXAMPLE_MODES)
    slot = Entry("system", "", "", "", "_slot")  # where the preset's «memory» block stands
    slot_list: list[Entry] | None = None
    slot_run: list[Entry] = []  # what was put at the slot so far
    memory_off = False
    uncond = [e for e in data.extras if not (e.covers or e.if_dropped)]
    cond = [e for e in data.extras if e.covers or e.if_dropped]
    ranked: list[tuple[int, Entry]] = []  # unconditional extras that may be dropped, with their rank
    memory_unconditional: list[Entry] = []
    after_card: dict[str, list[Entry]] = {}

    def extra_entry(extra: Extra) -> Entry:
        return Entry(extra.role, sub(extra.text), extra.source, extra.label, extra.kind, owner=extra.owner)

    def found_entry(found: Any, kind: str, owner: str = "") -> Entry:
        """A lorebook entry or a bubble. Sent bare (as SillyTavern does) when it stands at a depth inside the chat
        or came from a SillyTavern file; otherwise with its path in front ("[Book › Title]") so the model knows
        what it is. The role counts only at a depth: elsewhere lore is system text."""
        item = found.block
        content = data.lore_rewritten.get(item["id"], item["content"]) if kind == "lore" else item["content"]
        at_depth = item["depth"] is not None
        text = content if at_depth or from_st(item) else f"[{found.path}]\n{content}"
        role = item["role"] if at_depth or kind != "lore" else "system"
        return Entry(role, sub(text), f"block:{item['id']}", found.path, kind, owner=owner)

    def example_entries(member: Member, role: str, source: str, label: str) -> list[Entry]:
        """SillyTavern's examples: per `<START>` block the separator line (`new_example_chat`, system), then each
        line as its own message named example_user / example_assistant, the "Name:" in front taken off. `{{char}}`
        in a member's examples is that member. None of them is ever squashed."""
        raw = re.sub(r"\{\{char\}\}", lambda _: member.name, member.card.get("mes_example") or "", flags=re.I)
        out: list[Entry] = []
        for lines in example_dialogues(sub(raw), user, member.name):
            separator = sub(prompts.get("new_example_chat") or "")
            if separator.strip():
                out.append(Entry("system", separator, "prompt:new_example_chat", "Пример диалога", "examples",
                                 owner=member.character_id, alone=True))
            for who, said in lines:
                out.append(Entry(role, said, source, label, "examples", owner=member.character_id,
                                 name="example_user" if who == "user" else "example_assistant",
                                 speaker=user if who == "user" else member.name))
        return out

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
        if own_place(found) == "outlet":
            continue  # a SillyTavern outlet: reachable there only through a macro, never sent by itself
        entry = found_entry(found, "lore")
        if found.block["depth"] is not None:
            if entry.text.strip():
                injections.append(_Injection(found.block["depth"], entry, cat=CAT_LORE, sub=block_order(found.block)))
        else:
            placed_at.setdefault(own_place(found) or "card_before", []).append(entry)

    for extra in uncond:
        entry = extra_entry(extra)
        if not entry.text.strip():
            continue
        ranked.append((extra.drop_rank, entry))
        if extra.place == "depth":
            injections.append(_Injection(extra.depth, entry, cat=CAT_EXTRA))
        elif extra.place == "after_card":
            after_card.setdefault(extra.owner, []).append(entry)
        elif extra.place in ("card_before", "card_after", "an_top"):
            placed_at.setdefault(extra.place, []).append(entry)
        else:
            memory_unconditional.append(entry)

    def place_lore(where: str, block: dict[str, Any]) -> None:
        entries_here = placed_at.pop(where, [])
        if st_way and where in ("card_before", "card_after"):
            # SillyTavern: the entries before / after the card are one text (one line break between), wrapped by the
            # preset's `wi_format` ("{0}" = as is; an empty one = bare). Only the first and last entries carry the wrapper.
            entries_here = [e for e in entries_here if e.text.strip()]
            for entry in entries_here[1:]:
                entry.join = True
            wrapper = (preset.get("st") or {}).get("wi_format", "{0}")
            if entries_here and isinstance(wrapper, str) and "{0}" in wrapper and wrapper.strip():
                opening, _, closing = wrapper.partition("{0}")
                entries_here[0].text = opening + entries_here[0].text
                entries_here[-1].text = entries_here[-1].text + closing
        for entry in entries_here:
            place(entry, block)

    def place_blocks(member: Member, block: dict[str, Any]) -> None:
        """The member's chosen tree blocks: next to its card, or at their own depth."""
        blocks_placed.add(member.character_id)
        for found in member.blocks:
            item = found.block
            if own_place(found):
                continue  # it went to its own place above
            entry = found_entry(found, "block", member.character_id)
            if item["depth"] is not None:
                if entry.text.strip():
                    injections.append(_Injection(item["depth"], entry, cat=CAT_LORE, sub=block_order(item)))
            else:
                place(entry, block, member.depth, cat=CAT_LORE, sub=block_order(item))
        for entry in after_card.pop(member.character_id, []):  # a plugin's text right after this member's card
            place(entry, block, member.depth, cat=CAT_EXTRA)

    def card_with_override(block: dict[str, Any], key: str) -> tuple[str, str]:
        own = (primary.card.get(key) or "").strip() if primary else ""
        if prefer_card and own:
            return own.replace("{{original}}", block["text"]), f"character:{primary.character_id}:{key}"
        return block["text"], preset_source(block)

    for block in preset["blocks"]:
        if block.get("triggers") and data.generation_kind not in block["triggers"]:
            continue  # SillyTavern's per-prompt trigger: this block is for other kinds of generation
        if not block["enabled"]:
            if block["kind"] == "history":
                history_seen = True
            elif block["kind"] == "memory":
                memory_off = True
            continue
        kind, role = block["kind"], block["role"]
        if kind == "memory":
            slot_list = after if history_seen else before
            slot_list.append(slot)
        elif kind == "history":
            history_seen = True
        elif kind in ("main", "post_history"):
            text, source = card_with_override(block, "system_prompt" if kind == "main" else
                                              "post_history_instructions")
            if kind == "main":  # SillyTavern's author's note positions «before / after the main prompt»
                for entry in outside_notes["before_main"]:
                    place(entry, block)
            place(Entry(role, sub(text), source, block["name"], kind), block)
            if kind == "main":
                for entry in outside_notes["after_main"]:
                    place(entry, block)
                main_placed = True
        elif kind == "text" and block.get("when"):
            continue  # a behaviour block: sent at the end, only while its behaviour is on
        elif kind == "text":
            place(Entry(role, sub(block["text"]), preset_source(block), block["name"], kind), block)
        elif kind == "thinking" and not data.impersonate and block["text"].strip():
            place(Entry(role, sub(thinking.frame(block["text"])), preset_source(block), block["name"], kind), block)
        elif kind == "persona" and me is not None:
            source, label = f"character:{me.character_id}:card", f"{block['name']}: {me.name}"
            if st_way:  # SillyTavern: the persona's description bare, at its place
                for part in card_parts(me.card, prompts, bare=True):
                    place(Entry(role, sub(part), source, label, kind, owner=me.character_id), block, me.depth)
            else:  # ours: the model is told whose card it is (the «me» member may be a full character)
                text = card_text(me.card, prompts)
                if text:
                    text = f"{user} (played by the user):\n{text}"
                place(Entry(role, sub(text), source, label, kind, owner=me.character_id), block, me.depth)
            place_blocks(me, block)
        elif kind == "cast":
            for member in sorted(others, key=lambda m: m.importance):
                source = f"character:{member.character_id}:card"
                if st_way:  # SillyTavern: description, personality, scenario each its own message, bare
                    parts = card_parts(member.card, prompts, bare=True)
                    if several and parts:
                        parts[0] = f"### {member.name}\n{parts[0]}"
                else:
                    parts = [card_text(member.card, prompts)]
                    if several and parts[0]:
                        parts[0] = f"### {member.name}\n{parts[0]}"
                for part in parts:
                    place(Entry(role, sub(part), source, member.name, kind, owner=member.character_id), block,
                          member.depth)
                place_blocks(member, block)
            if scene:
                place(Entry(role, sub(scene), "prompt:scene", "Остальные в сцене", "prompt"), block)
                scene = ""
        elif kind == "examples":
            place_lore("examples_top", block)
            examples_mode = block.get("examples_mode", "room")
            for member in others if examples_mode != "never" else ():
                source, label = f"character:{member.character_id}:examples", f"{block['name']}: {member.name}"
                if st_way:
                    for entry in example_entries(member, role, source, label):
                        place(entry, block)
                    continue
                text = _examples(member.card)
                if text:
                    text = f"Example dialogue of {member.name}:\n{text}"
                place(Entry(role, sub(text), source, label, kind, owner=member.character_id), block)
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
    if not main_placed:  # no main prompt switched on: notes «before / after main» open the prompt
        before[0:0] = outside_notes["before_main"] + outside_notes["after_main"]
    note_depth = data.notes[0].depth if data.notes else 4  # where the author's note goes (ST's default)
    note_role = data.notes[0].role if data.notes else "system"  # lore above / below the note rides in its message
    an_top = placed_at.pop("an_top", [])
    an_bottom = placed_at.pop("an_bottom", [])
    # Without a fired note they still go, at the note's default place (depth 4): SillyTavern's author's note fires by
    # its interval (every reply by default) even with an empty text, and carries these entries (the owner's exam,
    # 2026-11-17: Elves, Yuri, Klaus… at the note's top were sent with no note text).
    first_note = data.notes[0] if data.notes else None
    if first_note is not None and first_note.place != "depth":  # the lore rides with the note outside the chat
        anchor = next((e for e in before + after if e.source == f"note:{first_note.id}"), None)
        holder = before if anchor is not None and any(e is anchor for e in before) else after
        if anchor is None:
            before.extend(an_top + an_bottom)
        else:
            index = next(i for i, e in enumerate(holder) if e is anchor)
            for entry in an_top + an_bottom:
                entry.role = note_role
            holder[index + 1:index + 1] = an_bottom
            holder[index:index] = an_top
        an_top, an_bottom = [], []
    for entry in an_top:
        entry.role = note_role
        injections.append(_Injection(note_depth, entry, cat=CAT_NOTE, sub=-1))
    placed_at.pop("outlet", None)  # a SillyTavern outlet: never sent by itself
    for left in placed_at.values():  # their block is not in the preset, or it is switched off
        before.extend(e for e in left if e.text.strip())
    placed_at.clear()

    if slot_list is None and not memory_off:  # no «memory» block in the preset: just before the chat
        slot_list = before
        before.append(slot)

    def put_at_slot(entries: list[Entry]) -> None:
        """Entries go in order right after the slot (after what was put there before)."""
        if slot_list is None:
            return
        entries = [e for e in entries if e.text.strip()]
        index = slot_list.index(slot) + 1 + sum(1 for e in slot_run if any(e is x for x in slot_list))
        slot_list[index:index] = entries
        slot_run.extend(entries)

    put_at_slot(memory_unconditional)
    if slot_list is None:  # memory is switched off in this preset: its must-have pieces are not sent either
        ranked[:] = [r for r in ranked if not any(r[1] is e for e in memory_unconditional)]
        cond = []  # nor its conditional ones: no reserve is kept for them and none is named as sent

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
                             thinking.compact([data.thoughts_rewritten.get(m["id"], m["swipe_meta"]["reasoning"])
                                               for m in thought_rows])))

    def message_entry(m: dict[str, Any]) -> Entry:
        text = data.rewritten.get(m["id"], m["content"])
        together = (m.get("extra") or {}).get("together")  # a combined round names everyone inside
        if data.several and m["role"] == "assistant" and m.get("name") and m["name"] != char and not together:
            text = f"{m['name']}: {text}"  # another character's line, so the model does not take it as its own
        if past_thoughts.get(m["id"]):
            text = thinking.wrap(past_thoughts[m["id"]], text,
                                 (m.get("swipe_meta") or {}).get("reasoning_tag") or thinking.SEND_TAG)
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

    # The chat starts with SillyTavern's new-chat line (system, never squashed, its tokens kept first); a chat of
    # several characters gets the group line. Empty text = none.
    head: list[Entry] = []
    new_chat = prompts.get("new_group_chat" if data.several else "new_chat") or ""
    if history_block["enabled"] and new_chat.strip():
        head.append(Entry("system", sub(new_chat), "prompt:new_chat", "Начало чата", "prompt", alone=True))

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
        if note.place != "depth":
            continue  # went with the main prompt
        entry = note_entry(note)
        if entry.text.strip():
            injections.append(_Injection(note.depth, entry, note, cat=CAT_NOTE))
    for entry in an_bottom:
        entry.role = note_role
        injections.append(_Injection(note_depth, entry, cat=CAT_NOTE, sub=1))
    # The speaking character's «Character's Note» (card extensions.depth_prompt), every reply, at its depth and role,
    # after the author's note in one message (SillyTavern's behaviour; wave 6).
    if primary is not None:
        found_note = character_note(primary.card)
        if found_note is not None:
            text, depth, role = found_note
            entry = Entry(role, sub(text).strip(), f"character:{primary.character_id}:depth_prompt",
                          f"Заметка персонажа: {primary.name}", "note", owner=primary.character_id)
            if entry.text:
                injections.append(_Injection(depth, entry, cat=CAT_CHAR_NOTE))

    # send_if_empty: the chat ends with the model's message (another character spoke last, «пусть поговорят»): a user
    # turn with this text closes the chat. It counts as a chat message for depths, as in SillyTavern.
    filler: list[Entry] = []
    if_empty = prompts.get("send_if_empty") or ""
    if (not data.impersonate and not data.continue_id and messages and messages[-1].role == "assistant"
            and if_empty.strip()):
        filler.append(Entry("user", sub(if_empty), "prompt:send_if_empty", "Если чат кончается ответом модели",
                            "prompt"))

    def behaviour(key: str, label: str, to: list[Entry], *, alone: bool = False) -> None:
        for b in when_texts(preset, key):
            if b.get("triggers") and data.generation_kind not in b["triggers"]:
                continue
            to.append(Entry(b["role"], sub(b["text"]), f"prompt:{key}", label, "prompt", alone=alone))

    # The group nudge goes right after the last chat message, before the post-history blocks (never squashed).
    nudge: list[Entry] = []
    if data.several and not data.impersonate:
        behaviour("speaker_reply", f"Отвечает: {char}", nudge, alone=True)
    # The tail, after everything: our «не писать за меня», the impersonation instruction (system by default, as in
    # SillyTavern), then the prefill as the model's own start, only for Claude and the text mode.
    tail: list[Entry] = []
    if me is not None and not me.model_may_write and not data.impersonate:
        behaviour("no_write_for_me", f"Запрет: не писать за «{me.name}»", tail)
    # In the text mode SillyTavern sends no instruction for «написать за меня»: the text simply opens the user's turn
    # (core/templates.render turn="user"), so neither the instruction nor a prefill goes there.
    text_impersonation = data.impersonate and data.provider_kind == "text"
    if data.impersonate and not text_impersonation:
        behaviour("impersonate", "Написать за меня", tail)
    prefill = (prompts.get("impersonation_prefill" if data.impersonate else "prefill") or "").rstrip()
    if prefill.strip() and data.provider_kind in PREFILL_KINDS and not data.continue_id and not text_impersonation:
        tail.append(Entry("assistant", sub(prefill).rstrip(), "prompt:prefill",
                          "Начало ответа (prefill)", "prompt"))

    dropped: dict[str, Any] = {"messages": 0, "members": [], "lore": list(data.lore_dropped),
                               "extras": list(data.extras_dropped), "memory": [], "examples": []}
    history_ids = [m["id"] for m in data.history]  # the whole branch, in order

    def total() -> int:
        return (_tokens(before) + _tokens(after) + _tokens(messages) + _tokens(tail) + _tokens(head)
                + _tokens(filler) + _tokens(nudge) + sum(_cost(i.entry) for i in injections))

    if data.max_context_tokens:
        def drop_oldest(keep: int, limit: int) -> None:
            while len(messages) > keep and total() > limit:
                messages.pop(0)
                dropped["messages"] += 1

        # SillyTavern fills the window with the history first and adds example dialogues only while room is left:
        # in mode «room» a member's examples (with their separators) go, whole, before any chat message.
        if examples_mode == "room":
            for member in reversed(others):
                if total() <= data.max_context_tokens:
                    break
                mine = (lambda e, owner=member.character_id: e.kind == "examples" and e.owner == owner)
                if not any(mine(e) for e in (*before, *after)) and not any(mine(i.entry) for i in injections):
                    continue
                before[:] = [e for e in before if not mine(e)]
                after[:] = [e for e in after if not mine(e)]
                injections[:] = [i for i in injections if not mine(i.entry)]
                dropped["examples"].append(member.name)
        keep = MIN_HISTORY
        if data.tail_from and data.tail_from in history_ids:  # the live tail is not cut before older messages
            sent_now = {e.source for e in messages}
            tail_ids = history_ids[history_ids.index(data.tail_from):]
            keep = max(keep, sum(1 for i in tail_ids if f"message:{i}" in sent_now))
        reserve = data.memory_reserve if cond else 0
        drop_oldest(keep, data.max_context_tokens - reserve)
        for _, entry in sorted(ranked, key=lambda r: r[0]):  # a plugin's text that is not a must goes before cards
            if total() <= data.max_context_tokens:
                break
            for lst in (before, after):
                lst[:] = [e for e in lst if e is not entry]
            injections[:] = [i for i in injections if i.entry is not entry]
            dropped["extras"].append(entry.label)
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
        drop_oldest(1, data.max_context_tokens)

    # Conditional extras (the memory): only for history messages that were really left out, while they fit.
    if cond:
        sent_ids = {e.source.removeprefix("message:") for e in messages}
        left_out = {i for i in history_ids if i not in sent_ids}
        reserve_left = data.memory_reserve or None
        used = 0
        chosen: list[tuple[int, int, Entry]] = []  # (story order, list order, entry)
        groups_sent: set[str] = set()
        groups_missed: dict[str, str] = {}  # group -> the label of its first alternative that did not fit
        for position, extra in enumerate(cond):
            if extra.group and extra.group in groups_sent:
                continue
            if extra.covers:
                first, last = extra.covers
                if first not in history_ids or last not in history_ids:
                    continue
                low, high = sorted((history_ids.index(first), history_ids.index(last)))
                if not all(i in left_out for i in history_ids[low:high + 1]):
                    continue
            elif not left_out:
                continue
            entry = extra_entry(extra)
            if not entry.text.strip():
                continue
            cost = _cost(entry)
            over_reserve = reserve_left is not None and used + cost > reserve_left
            over_window = bool(data.max_context_tokens) and total() + used + cost > data.max_context_tokens
            if over_reserve or over_window:
                if extra.group:  # an alternative that does not fit leaves the place to the next one
                    groups_missed.setdefault(extra.group, extra.label)
                else:
                    dropped["extras"].append(extra.label)
                continue
            chosen.append((position if extra.order is None else extra.order, position, entry))
            used += cost
            if extra.group:
                groups_sent.add(extra.group)
            dropped["memory"].append(extra.label)
        dropped["extras"].extend(label for group, label in groups_missed.items() if group not in groups_sent)
        put_at_slot([entry for _, _, entry in sorted(chosen, key=lambda c: (c[0], c[1]))])
    if slot_list is not None and slot in slot_list:
        slot_list.remove(slot)
    for key in ("extras", "memory", "examples"):  # without a plugin the record is what it always was
        if not dropped[key]:
            del dropped[key]

    messages.extend(filler)  # the send_if_empty turn closes the chat and counts for depths

    # Depth d goes before the last d messages; several at one place go SillyTavern's way (CAT_* above).
    # A fired note lands after a message; a saved note already attached there (the reply is being
    # regenerated) is not sent twice.
    slots: list[list[_Injection]] = [[] for _ in range(len(messages) + 1)]
    placed: list[dict[str, Any]] = []
    for injection in injections:
        # «Дописать»: the continued message stays the last one, so what stands at depth 0 goes right before it
        index = max(0, len(messages) - (max(injection.depth, 1) if data.continue_id else injection.depth))
        note = injection.note
        if note is not None:
            after_message = messages[index - 1] if index > 0 else None
            stored = [m for m in messages[:index] if m.source.startswith("message:")]  # not the send_if_empty turn
            message_id = stored[-1].source.removeprefix("message:") if stored else None
            if note.mode != "once" and after_message is not None and any(
                f.source in saved_sources.get(note.id, ()) for f in after_message.follow
            ):
                continue
            placed.append({"note_id": note.id, "mode": note.mode, "message_id": message_id,
                           "text": injection.entry.text, "role": note.role, "title": note.title})
        slots[index].append(injection)
    chat_part: list[Entry] = list(head)
    for index, slot in enumerate(slots):
        group: tuple[int, str] | None = None
        for injection in sorted(slot, key=lambda i: (i.order, ROLE_RANK.get(i.entry.role, 2), i.cat, i.sub)):
            key = (injection.order, injection.entry.role)
            injection.entry.join = key == group
            group = key
            chat_part.append(injection.entry)
        if index < len(messages):
            chat_part.append(messages[index])
            chat_part.extend(messages[index].follow)
    chat_part.extend(nudge)

    # «Дописать» (SillyTavern's continue): the message being continued moves to the very end, after the post-history
    # blocks; then the preset's continue_nudge (its {{lastChatMessage}} = that text), or, with `continue_prefill`, no
    # line at all: the message is the start of the reply the model goes on with.
    ending: list[Entry] = []
    continued = next((e for e in chat_part if data.continue_id and e.source == f"message:{data.continue_id}"), None)
    if continued is not None:
        chat_part = [e for e in chat_part if e is not continued]
        ending.append(continued)
        nudge_text = prompts.get("continue_nudge") or ""
        if not preset.get("continue_prefill") and nudge_text.strip():
            ending.append(Entry("system", sub(nudge_text.replace("{{lastChatMessage}}", continued.text)),
                                "prompt:continue_nudge", "Дописать", "prompt", alone=True))

    entries = [*before, *chat_part, *after, *tail, *ending]
    # No user turn at all: our own presets without the new-chat line add a user "[Start]" so every service accepts
    # the request. SillyTavern never adds one, so neither does a preset from it, nor a prompt with the new-chat line.
    if entries and not head and not st_way and not any(e.role == "user" for e in entries):
        first_chat = len(before)
        entries.insert(first_chat, Entry("user", START_TURN, "prompt:start", "Начало", "prompt"))

    # Texts of one depth group are one message; neighbouring system texts outside the chat messages are joined
    # only when the preset says so (SillyTavern's «Squash system messages»), never a named example line or the
    # separators (`alone`). One line break, as SillyTavern.
    squash = preset.get("squash_system", True)
    sent: list[ChatMessage] = []
    previous: Entry | None = None

    def squashable(e: Entry) -> bool:
        return e.role == "system" and e.kind != "message" and not e.alone and not e.name

    for entry in entries:
        joined = entry.join and previous is not None and previous.role == entry.role and bool(sent)
        mergeable = joined or (squash and previous is not None and squashable(entry) and squashable(previous))
        if mergeable:
            last = sent[-1]
            sent[-1] = ChatMessage(last.role, f"{last.content}\n{entry.text}", last.images)
        else:
            sent.append(ChatMessage(entry.role, entry.text, tuple(entry.images), entry.name, entry.speaker))
        entry.msg = len(sent) - 1
        previous = entry
    return Prompt(entries=entries, messages=sent, tokens=_tokens(entries), dropped=dropped,
                  placed=placed,
                  names={"user": user, "char": primary.name if primary else "", "group": [m.name for m in others]})
