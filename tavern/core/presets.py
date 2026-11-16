"""Prompt presets: the ordered list of blocks a prompt is built from, plus model parameters.

A preset is plain JSON so it can be stored, exported and edited by agents:

    {
      "blocks": [ {id, kind, name, enabled, role, text, position, depth, group, history?}, ... ],
      "params": {temperature, top_p, top_k, max_tokens, presence_penalty, frequency_penalty, stop},
      "max_context_tokens": 0,          # 0 = use the connection's limit (0 there = no limit)
      "prompts": {"prefill", "impersonation_prefill", "personality_format", "scenario_format", "new_chat",
                  "new_group_chat", "new_example_chat", "send_if_empty"},   # DEFAULT_PROMPTS (SillyTavern's)
      "prefer_card_prompts": true,      # a card's own system prompt / post-history text wins
      "st": {...},                      # SillyTavern keys we do not use, kept for export
      "origin": "" | "sillytavern",     # imported from SillyTavern: card, persona and examples go its way (bare)
      "texts_version": 2                # behaviour blocks' untouched defaults follow SillyTavern's texts
    }

Block kinds (the prompt manager shows them as a list the user reorders):
  main           the main system prompt (text)
  persona        the card of the member marked "me"
  cast           cards of the other members (one entry per member, so each can be dropped)
  examples       example dialogues of the members
  lore_before    world info before / after the cards (filled by lorebooks, stage 5)
  lore_after
  history        the chat itself; exactly one per preset. Options: all / last N / token cap
  post_history   instructions after the chat (a card's post_history_instructions)
  text           any text the user writes. With `when` set it is a behaviour block: sent only while
                 that behaviour is on (see WHEN below), after the chat, never in the normal flow
  thinking       «Размышление»: a template the model fills in before the reply. Sent framed as
                 "think inside <thinking>…</thinking>" (core/thinking.py); the thinking is cut
                 out of the reply. At most one per preset.

`group`: blocks with the same group are alternatives ("Длина": short / medium / long); at most one
of them is on. The history block's `thoughts` = how many past replies are sent with their thinking
(0 = none, the default), repeated lines dropped (core/thinking.compact).

Blocks before `history` go before the chat, blocks after it go after. A block with
position "depth" is instead inserted `depth` messages from the end of the chat; its `order` (SillyTavern's
in-chat «order», default 100) decides how it stands among other texts at the same depth: texts of one depth,
order and role become one message (core/assembly.py).

`squash_system` (default true; SillyTavern's «Squash system messages», taken from an imported file): neighbouring
system texts outside the chat messages are joined into one message with one line break. Off: each stays its own.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from tavern.errors import AppError

ROLES = ("system", "user", "assistant")
KINDS = ("main", "persona", "cast", "examples", "lore_before", "lore_after", "history",
         "post_history", "text", "thinking", "memory")
# Kinds that have text of their own in the preset (the others are filled from chat data).
TEXT_KINDS = ("main", "post_history", "text", "thinking")
MAX_GROUP = 40
PARAM_TYPES: dict[str, tuple[type, ...]] = {
    "temperature": (int, float),
    "top_p": (int, float),
    "top_k": (int,),
    "max_tokens": (int,),
    "presence_penalty": (int, float),
    "frequency_penalty": (int, float),
    "reasoning_effort": (str,),  # how hard a thinking model (Gemini 3, o-series) thinks
}
REASONING_EFFORTS = ("minimal", "low", "medium", "high")
HISTORY_MODES = ("all", "last")
MAX_BLOCKS = 200
MAX_TEXT = 200_000
DEFAULT_DEPTH_ORDER = 100
MAX_ORDER = 1_000_000

# Texts that are not instructions but formats and service lines (the others live in blocks, see WHEN).
# The defaults are SillyTavern's (the phone has no SillyTavern folder to take them from).
DEFAULT_PROMPTS = {
    # The start of the model's own reply (a last assistant message). Sent only to Claude connections and to the
    # text mode, as SillyTavern does; trailing spaces cut. Empty = none.
    "prefill": "",
    # The same when the model writes for the user («Написать за меня»), Claude / text mode only.
    "impersonation_prefill": "",
    # How card parts are written. Empty = "Personality: ..." sections in our own presets (a preset from
    # SillyTavern sends the bare field); ST-style templates use {{personality}} / {{scenario}}.
    "personality_format": "",
    "scenario_format": "",
    # System line right before the first chat message (never squashed); in a chat of several characters the
    # group line ({{group}} = their names). Empty = none.
    "new_chat": "[Start a new Chat]",
    "new_group_chat": "[Start a new group chat. Group members: {{group}}]",
    # System line before each <START> block of a card's example dialogue (presets from SillyTavern).
    "new_example_chat": "[Example Chat]",
    # A user turn with this text when the chat's last message is the model's (e.g. «пусть поговорят»). Empty = none.
    "send_if_empty": "",
    # «Дописать» (continue the last message, wave 5): the system line after the message being continued (unless
    # `continue_prefill` sends the message as the start of the reply), and what joins the old text and the new.
    "continue_nudge": "[Continue your last message without repeating its original content.]",
    "continue_postfix": " ",
}
# Kinds of generation a preset block may be limited to (SillyTavern's `injection_trigger`; none = always).
GENERATION_KINDS = ("normal", "continue", "impersonate", "swipe", "regenerate", "quiet")
# Our prompts key -> the SillyTavern preset key with the same text (an older import kept them only in `st`).
PROMPT_ST_KEYS = {
    "prefill": "assistant_prefill",
    "impersonation_prefill": "assistant_impersonation",
    "personality_format": "personality_format",
    "scenario_format": "scenario_format",
    "new_chat": "new_chat_prompt",
    "new_group_chat": "new_group_chat_prompt",
    "new_example_chat": "new_example_chat_prompt",
    "send_if_empty": "send_if_empty",
    "continue_nudge": "continue_nudge_prompt",
    "continue_postfix": "continue_postfix",
}
# The examples block (wave 3, SillyTavern's «example messages behaviour»): room = sent only while there is room after
# the chat history (ST's default: in a long chat the history wins), always = never cut, never = not sent.
EXAMPLE_MODES = ("room", "always", "never")
ORIGIN_ST = "sillytavern"  # preset["origin"]: imported from SillyTavern, so its texts go bare (no headers of ours)
TEXTS_VERSION = 2  # preset["texts_version"]: 2 = behaviour blocks follow SillyTavern's default texts and roles

# Behaviour blocks: kind "text" with `when`. key -> (block name, role, default text).
#   no_write_for_me  "me" is a member the model may not write for (and the reply is not "write for me")
#   speaker_reply    two or more characters in the chat: whom the model writes for now ({{char}})
#   impersonate      the «Написать за меня» button
#   narrator         the narrator after an all-at-once round (stage 7б); sent to the narrator's request
WHEN: dict[str, tuple[str, str, str]] = {
    "no_write_for_me": (
        "Не писать за меня", "system",
        "[{{user}} is played by the user. Never write {{user}}'s words, thoughts or actions; "
        "stop and let the user continue.]"),
    "speaker_reply": (
        "Кто отвечает сейчас (несколько персонажей)", "system",
        "[Write the next reply only as {{char}}.]"),
    "impersonate": (
        "Написать за меня (кнопка)", "system",
        "[Write your next reply from the point of view of {{user}}, using the chat history so far as a guideline "
        "for the writing style of {{user}}. Don't write as {{char}} or system. Don't describe actions of {{char}}.]"),
    "narrator": (
        "Рассказчик после хода «все сразу»", "system",
        "You are the narrator of this roleplay. The characters below acted at the same time. In the third "
        "person and briefly, write what came of all their actions together: who was first, what clashed, "
        "what the scene looks like now. Do not write new lines for the characters or for {{user}}."),
}


# Our own default wording before the texts became SillyTavern's (texts_version 1): (role, text). A behaviour block
# still holding it exactly is moved to the new default; a person's own text stays.
WHEN_V1: dict[str, tuple[str, str]] = {
    "speaker_reply": (
        "system", "[Write the next reply only as {{char}}. Do not write for the other characters or {{user}}.]"),
    "impersonate": (
        "user", "[Write the next message of {{user}} in the first person, as {{user}} would. "
                "Only {{user}}'s words and actions, no reply from {{char}}.]"),
}
# The wording the fields had before they became blocks: told apart from a person's own text by migration.
DEFAULT_PROMPTS_OLD = {key: WHEN_V1[key][1] if key in WHEN_V1 else WHEN[key][2] for key in WHEN}


def when_block(key: str, text: str | None = None, enabled: bool = True) -> dict[str, Any]:
    name, role, default = WHEN[key]
    return {"id": f"when-{key}", "kind": "text", "name": name, "enabled": enabled, "role": role,
            "text": default if text is None else text, "position": "top", "depth": 4, "group": "", "when": key}


def when_texts(preset: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """The switched-on behaviour blocks with text for `key`, in the preset's order."""
    return [b for b in preset["blocks"] if b.get("when") == key and b["enabled"] and b["text"].strip()]


# SillyTavern preset keys kept in preset["st"] that change the request itself -> the request option the providers
# read (providers/params.PRESET_EXTRAS). Texts only: an empty text means «not set».
ST_REQUEST_TEXTS = {
    "custom_prompt_post_processing": "prompt_post_processing",
    "custom_include_body": "include_body",
    "custom_exclude_body": "exclude_body",
    "custom_include_headers": "include_headers",
}


def provider_extras(preset: dict[str, Any]) -> dict[str, Any]:
    """Request options a preset from SillyTavern carries (docs/ST_PARITY.md §9, §11): whether the leading system
    texts go to the API's own system field of Claude / Gemini (`use_sysprompt`, off in SillyTavern when the file
    does not say), the prompt post-processing and the custom body fields and headers. Our own presets give none,
    so the providers keep their defaults."""
    st = preset.get("st") if isinstance(preset.get("st"), dict) else {}
    if preset.get("origin") != ORIGIN_ST:
        return {}
    extras: dict[str, Any] = {"use_sysprompt": st.get("use_sysprompt") is True}
    for key, option in ST_REQUEST_TEXTS.items():
        value = st.get(key)
        if isinstance(value, str) and value.strip():
            extras[option] = value.strip()
    return extras


DEFAULT_BLOCK_NAMES = {
    "main": "Главный промпт",
    "persona": "Мой персонаж",
    "cast": "Персонажи чата",
    "examples": "Примеры диалогов",
    "lore_before": "Лор (до карточек)",
    "lore_after": "Лор (после карточек)",
    "history": "История чата",
    "post_history": "После истории",
    "text": "Свой блок",
    "thinking": "Размышление",
    "memory": "Память (дополнение)",  # where a plugin's memory goes (ADR 0019): no text of its own
}


class PresetError(AppError):
    status = 400
    code = "bad_preset"


def _block(kind: str, text: str = "", **extra: Any) -> dict[str, Any]:
    block = {
        "id": kind, "kind": kind, "name": DEFAULT_BLOCK_NAMES[kind], "enabled": True,
        "role": "system", "text": text, "position": "top", "depth": 4, "group": "",
    }
    block.update(extra)
    return block


DEFAULT_PRESET: dict[str, Any] = {
    "blocks": [
        _block("main", "Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}."),
        _block("lore_before"),
        _block("persona"),
        _block("cast"),
        _block("lore_after"),
        _block("examples", examples_mode="room"),
        _block("memory"),
        _block("history", history={"mode": "all", "count": 50, "max_tokens": 0, "thoughts": 0}),
        _block("post_history"),
        *(when_block(key) for key in WHEN),
    ],
    "params": {},
    "max_context_tokens": 0,
    "prompts": dict(DEFAULT_PROMPTS),
    "prefer_card_prompts": True,
    "st": {},
    "squash_system": True,
    "origin": "",
    "texts_version": TEXTS_VERSION,
}


def default_preset() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_PRESET)


def _int(value: Any, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PresetError(f"{name} must be a whole number from {low} to {high}")
    return value


def normalize_block(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PresetError(f"Block {index + 1} must be an object")
    kind = raw.get("kind", "text")
    if kind not in KINDS:
        raise PresetError(f"Block {index + 1} has unknown kind {kind!r}")
    block = _block(kind)
    block_id = raw.get("id") or f"{kind}-{index}"
    if not isinstance(block_id, str) or not re.fullmatch(r"[\w.-]{1,64}", block_id):
        raise PresetError(f"Block {index + 1} has a bad id")
    block["id"] = block_id
    name = raw.get("name", block["name"])
    if not isinstance(name, str):
        raise PresetError(f"Block {index + 1}: name must be text")
    block["name"] = name.strip()[:100] or DEFAULT_BLOCK_NAMES[kind]
    block["enabled"] = bool(raw.get("enabled", True))
    role = raw.get("role", "system")
    if role not in ROLES:
        raise PresetError(f"Block {index + 1}: role must be one of {', '.join(ROLES)}")
    block["role"] = role
    text = raw.get("text", "")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise PresetError(f"Block {index + 1}: text must be at most {MAX_TEXT} characters")
    block["text"] = text if kind in TEXT_KINDS else ""
    position = raw.get("position", "top")
    if position not in ("top", "depth") or (kind == "history" and position != "top"):
        raise PresetError(f"Block {index + 1}: position must be 'top' or 'depth'")
    block["position"] = position
    block["depth"] = _int(raw.get("depth", 4), f"Block {index + 1} depth", 0, 1000)
    order = raw.get("order", DEFAULT_DEPTH_ORDER)
    if order != DEFAULT_DEPTH_ORDER:
        block["order"] = _int(order, f"Block {index + 1} order", -MAX_ORDER, MAX_ORDER)
    when = raw.get("when") or ""
    if when and (kind != "text" or when not in WHEN):
        raise PresetError(f"Block {index + 1}: unknown behaviour {when!r}")
    if when:
        block["when"] = when
    group = raw.get("group") or ""
    if not isinstance(group, str):
        raise PresetError(f"Block {index + 1}: group must be text")
    block["group"] = " ".join(group.split())[:MAX_GROUP] if kind != "history" else ""
    triggers = raw.get("triggers") or []
    if (not isinstance(triggers, list) or any(t not in GENERATION_KINDS for t in triggers)
            or (triggers and kind == "history")):
        raise PresetError(f"Block {index + 1}: triggers must be a list of {', '.join(GENERATION_KINDS)}")
    if triggers:
        block["triggers"] = [k for k in GENERATION_KINDS if k in triggers]
    if kind == "examples":
        mode = raw.get("examples_mode", EXAMPLE_MODES[0])
        if mode not in EXAMPLE_MODES:
            raise PresetError(f"Block {index + 1}: examples_mode must be one of {', '.join(EXAMPLE_MODES)}")
        block["examples_mode"] = mode
    if kind == "history":
        options = raw.get("history") or {}
        if not isinstance(options, dict):
            raise PresetError("History options must be an object")
        mode = options.get("mode", "all")
        if mode not in HISTORY_MODES:
            raise PresetError("History mode must be 'all' or 'last'")
        block["history"] = {
            "mode": mode,
            "count": _int(options.get("count", 50), "History message count", 1, 100_000),
            "max_tokens": _int(options.get("max_tokens", 0), "History token limit", 0, 10_000_000),
            "thoughts": _int(options.get("thoughts", 0), "Past thinking count", 0, 1000),
        }
    return block


def _upgrade_texts(blocks: list[dict[str, Any]], *, from_st: bool) -> None:
    """Once per preset (texts_version 1 -> 2): behaviour blocks still holding our old default wording get
    SillyTavern's default text and role; «написать за меня» of a preset from SillyTavern goes as system, as there
    (the importer used to give it the user role)."""
    for block in blocks:
        key = block.get("when")
        if key in WHEN_V1 and (block["role"], block["text"]) == WHEN_V1[key]:
            block["role"], block["text"] = WHEN[key][1], WHEN[key][2]
        elif key == "impersonate" and from_st and block["role"] == "user":
            block["role"] = "system"


def normalize_preset(raw: Any) -> dict[str, Any]:
    """Validate a preset from the UI, an agent or an import; fill in what is missing."""
    if not isinstance(raw, dict):
        raise PresetError("A preset must be an object")
    preset = default_preset()
    blocks_raw = raw.get("blocks", preset["blocks"])
    if not isinstance(blocks_raw, list) or len(blocks_raw) > MAX_BLOCKS:
        raise PresetError(f"Blocks must be a list of at most {MAX_BLOCKS}")
    blocks = [normalize_block(b, i) for i, b in enumerate(blocks_raw)]
    ids = [b["id"] for b in blocks]
    if len(set(ids)) != len(ids):
        raise PresetError("Block ids must be unique")
    histories = [b for b in blocks if b["kind"] == "history"]
    if len(histories) > 1:
        raise PresetError("A preset has exactly one chat history block")
    if not histories:
        blocks.append(normalize_block({"kind": "history", "id": "history"}, len(blocks)))
    legacy = raw.get("prompts") if isinstance(raw.get("prompts"), dict) else {}
    for key in WHEN:  # an older preset kept these texts in "prompts": they become blocks, nothing is lost
        found = [b for b in blocks if b.get("when") == key]
        if len(found) > 1:
            raise PresetError(f"Behaviour «{key}» may have only one block")
        if found:
            continue
        old = legacy.get(key)
        block_id = f"when-{key}"
        if block_id in ids:
            block_id = f"when-{key}-{len(blocks)}"
        if isinstance(old, str) and len(old) <= MAX_TEXT:
            own = old.strip() and old != DEFAULT_PROMPTS_OLD[key]
            block = when_block(key, old if own else None, enabled=bool(old.strip()))
        else:
            block = when_block(key)
        block["id"] = block_id
        blocks.append(block)
        ids.append(block_id)
    if sum(b["kind"] == "thinking" for b in blocks) > 1:
        raise PresetError("A preset has at most one «Размышление» (thinking) block")
    on_in_group: dict[str, str] = {}
    for b in blocks:
        if b["group"] and b["enabled"]:
            if b["group"] in on_in_group:
                raise PresetError(f"Group «{b['group']}»: only one block may be on "
                                  f"(«{on_in_group[b['group']]}» and «{b['name']}» are both on)")
            on_in_group[b["group"]] = b["name"]
    preset["blocks"] = blocks

    params_raw = raw.get("params", {})
    if not isinstance(params_raw, dict):
        raise PresetError("params must be an object")
    params: dict[str, Any] = {}
    for key, value in params_raw.items():
        if value is None:
            continue
        if key == "stop":
            if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
                raise PresetError("stop must be a list of strings")
            if value:
                params["stop"] = value[:4]
            continue
        types = PARAM_TYPES.get(key)
        if types is None:
            raise PresetError(f"Unknown model parameter {key!r}")
        if key == "reasoning_effort":
            if value not in REASONING_EFFORTS:
                raise PresetError(f"reasoning_effort must be one of {', '.join(REASONING_EFFORTS)}")
        elif isinstance(value, bool) or not isinstance(value, types):
            raise PresetError(f"Model parameter {key} must be a number")
        params[key] = value
    preset["params"] = params
    preset["max_context_tokens"] = _int(raw.get("max_context_tokens", 0), "Context size", 0, 10_000_000)

    prompts_raw = raw.get("prompts", {})
    if not isinstance(prompts_raw, dict):
        raise PresetError("prompts must be an object")
    st = raw.get("st", {})
    preset["st"] = st if isinstance(st, dict) else {}
    for key in DEFAULT_PROMPTS:
        kept = preset["st"].get(PROMPT_ST_KEYS.get(key, ""))  # an older import kept the SillyTavern text only there
        value = prompts_raw.get(key, kept if isinstance(kept, str) else DEFAULT_PROMPTS[key])
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise PresetError(f"Prompt {key} must be text")
        preset["prompts"][key] = value
    preset["prefer_card_prompts"] = bool(raw.get("prefer_card_prompts", True))
    origin = raw.get("origin")
    if origin not in ("", ORIGIN_ST):  # a preset imported before the field existed: its kept SillyTavern keys say
        origin = ORIGIN_ST if preset["st"] else ""
    preset["origin"] = origin
    if raw.get("texts_version") != TEXTS_VERSION:
        _upgrade_texts(blocks, from_st=origin == ORIGIN_ST)
    preset["texts_version"] = TEXTS_VERSION
    squash = raw.get("squash_system")
    if not isinstance(squash, bool):  # a preset imported before the switch existed: its SillyTavern file said
        squash = preset["st"].get("squash_system_messages")
        squash = squash if isinstance(squash, bool) else True
    preset["squash_system"] = squash
    continue_prefill = raw.get("continue_prefill")
    if not isinstance(continue_prefill, bool):  # an older import kept SillyTavern's switch in `st`
        continue_prefill = preset["st"].get("continue_prefill") is True
    preset["continue_prefill"] = continue_prefill
    return preset
