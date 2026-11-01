"""Prompt presets: the ordered list of blocks a prompt is built from, plus model parameters.

A preset is plain JSON so it can be stored, exported and edited by agents:

    {
      "blocks": [ {id, kind, name, enabled, role, text, position, depth, history?}, ... ],
      "params": {temperature, top_p, top_k, max_tokens, presence_penalty, frequency_penalty, stop},
      "max_context_tokens": 0,          # 0 = use the connection's limit (0 there = no limit)
      "prompts": {"impersonate": ..., "no_write_for_me": ...},
      "prefer_card_prompts": true,      # a card's own system prompt / post-history text wins
      "st": {...}                       # SillyTavern keys we do not use, kept for export
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
  text           any text the user writes

Blocks before `history` go before the chat, blocks after it go after. A block with
position "depth" is instead inserted `depth` messages from the end of the chat.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from tavern.errors import AppError

ROLES = ("system", "user", "assistant")
KINDS = ("main", "persona", "cast", "examples", "lore_before", "lore_after", "history",
         "post_history", "text")
# Kinds that have text of their own in the preset (the others are filled from chat data).
TEXT_KINDS = ("main", "post_history", "text")
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

DEFAULT_PROMPTS = {
    "impersonate": (
        "[Write the next message of {{user}} in the first person, as {{user}} would. "
        "Only {{user}}'s words and actions, no reply from {{char}}.]"
    ),
    "no_write_for_me": (
        "[{{user}} is played by the user. Never write {{user}}'s words, thoughts or actions; "
        "stop and let the user continue.]"
    ),
    # Two or more characters: whom the model writes for now ({{char}} is the speaker); empty = not said.
    "speaker_reply": "[Write the next reply only as {{char}}. Do not write for the other characters or {{user}}.]",
    # The narrator after an all-at-once round (stage 7б): turns the replies into one scene.
    "narrator": (
        "You are the narrator of this roleplay. The characters below acted at the same time. In the third "
        "person and briefly, write what came of all their actions together: who was first, what clashed, "
        "what the scene looks like now. Do not write new lines for the characters or for {{user}}."
    ),
    # Sent as the start of the model's own reply (last assistant message); empty = none.
    "prefill": "",
    # How card parts are written. Empty = "Personality:\n..." sections; ST-style templates use
    # {{personality}} / {{scenario}} (imported presets bring their own).
    "personality_format": "",
    "scenario_format": "",
}

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
}


class PresetError(AppError):
    status = 400
    code = "bad_preset"


def _block(kind: str, text: str = "", **extra: Any) -> dict[str, Any]:
    block = {
        "id": kind, "kind": kind, "name": DEFAULT_BLOCK_NAMES[kind], "enabled": True,
        "role": "system", "text": text, "position": "top", "depth": 4,
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
        _block("examples"),
        _block("history", history={"mode": "all", "count": 50, "max_tokens": 0}),
        _block("post_history"),
    ],
    "params": {},
    "max_context_tokens": 0,
    "prompts": dict(DEFAULT_PROMPTS),
    "prefer_card_prompts": True,
    "st": {},
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
        }
    return block


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
    for key in DEFAULT_PROMPTS:
        value = prompts_raw.get(key, DEFAULT_PROMPTS[key])
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise PresetError(f"Prompt {key} must be text")
        preset["prompts"][key] = value
    preset["prefer_card_prompts"] = bool(raw.get("prefer_card_prompts", True))
    st = raw.get("st", {})
    preset["st"] = st if isinstance(st, dict) else {}
    return preset
