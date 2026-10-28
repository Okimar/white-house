"""SillyTavern Chat Completion presets <-> Tavern presets. Converted once at import/export time.

Written from the shape of exported preset files, not from SillyTavern code. A preset file has
model settings (`temperature`, `openai_max_context`, ...), a `prompts` list
(`identifier`, `name`, `role`, `content`, `marker`, `injection_position`, `injection_depth`) and
`prompt_order` (per character id, the order and on/off state of the prompts). Built-in places
are "markers": chatHistory, dialogueExamples, charDescription, ...

Keys we do not use are kept in the preset's `st` object and written back on export, so a
round trip does not lose them.
"""

from __future__ import annotations

import re
from typing import Any

from tavern.core.presets import (
    DEFAULT_BLOCK_NAMES,
    DEFAULT_PROMPTS,
    REASONING_EFFORTS,
    PresetError,
    normalize_preset,
)

# ST prompt identifier -> our block kind. The three card parts become one "cast" block.
MARKER_KINDS = {
    "main": "main",
    "jailbreak": "post_history",
    "chatHistory": "history",
    "dialogueExamples": "examples",
    "charDescription": "cast",
    "charPersonality": "cast",
    "scenario": "cast",
    "personaDescription": "persona",
    "worldInfoBefore": "lore_before",
    "worldInfoAfter": "lore_after",
}
KIND_IDENTIFIERS = {
    "main": "main",
    "post_history": "jailbreak",
    "history": "chatHistory",
    "examples": "dialogueExamples",
    "persona": "personaDescription",
    "lore_before": "worldInfoBefore",
    "lore_after": "worldInfoAfter",
}
CAST_IDENTIFIERS = ("charDescription", "charPersonality", "scenario")
PARAMS = {  # ST key -> our param
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "frequency_penalty": "frequency_penalty",
    "presence_penalty": "presence_penalty",
    "openai_max_tokens": "max_tokens",
}
# In ST these values mean "off"; sending them would change nothing or be rejected by Gemini.
OFF_VALUES = {"top_k": 0, "frequency_penalty": 0, "presence_penalty": 0}
GLOBAL_ORDER_ID = 100001
# ST key -> our preset["prompts"] key (texts, kept as written)
TEXTS = {
    "impersonation_prompt": "impersonate",
    "assistant_prefill": "prefill",
    "personality_format": "personality_format",
    "scenario_format": "scenario_format",
}
CONSUMED = {"prompts", "prompt_order", "openai_max_context", "reasoning_effort", *PARAMS, *TEXTS}


def _block_id(identifier: str, index: int) -> str:
    cleaned = re.sub(r"[^\w.-]", "-", identifier)[:64]
    return cleaned or f"block-{index}"


def _order(obj: dict[str, Any]) -> list[dict[str, Any]]:
    orders = obj.get("prompt_order")
    if isinstance(orders, list) and orders:
        chosen = next((o for o in orders if isinstance(o, dict) and o.get("character_id") == GLOBAL_ORDER_ID),
                      None) or next((o for o in orders if isinstance(o, dict)), None)
        if chosen and isinstance(chosen.get("order"), list):
            return [o for o in chosen["order"] if isinstance(o, dict) and isinstance(o.get("identifier"), str)]
    prompts = obj.get("prompts") or []
    return [{"identifier": p["identifier"], "enabled": p.get("enabled", True)}
            for p in prompts if isinstance(p, dict) and isinstance(p.get("identifier"), str)]


def from_st(obj: Any, name: str = "") -> tuple[str, dict[str, Any]]:
    """Return (name, preset data). Raises PresetError for anything that is not a CC preset."""
    if not isinstance(obj, dict):
        raise PresetError("The file is not a preset (expected a JSON object)")
    if not isinstance(obj.get("prompts"), list):
        hint = " It looks like a Text Completion preset; those come with stage 6." if (
            "rep_pen" in obj or "temp" in obj or "input_sequence" in obj) else ""
        raise PresetError("Not a SillyTavern Chat Completion preset: it has no prompt list." + hint)
    prompts = {p["identifier"]: p for p in obj["prompts"]
               if isinstance(p, dict) and isinstance(p.get("identifier"), str)}
    blocks: list[dict[str, Any]] = []
    seen_kinds: set[str] = set()
    seen_ids: set[str] = set()
    for index, item in enumerate(_order(obj)):
        identifier = item["identifier"]
        prompt = prompts.get(identifier, {})
        kind = MARKER_KINDS.get(identifier)
        if kind is None:
            if prompt.get("marker"):
                continue  # a marker we do not know: nothing to fill it with
            kind = "text"
        if kind != "text" and kind in seen_kinds:
            continue  # charPersonality / scenario after charDescription, or duplicates
        block_id = kind if kind in ("history", "cast") else _block_id(identifier, index)
        if block_id in seen_ids:
            continue
        seen_kinds.add(kind)
        seen_ids.add(block_id)
        role = prompt.get("role") if prompt.get("role") in ("system", "user", "assistant") else "system"
        in_chat = prompt.get("injection_position") == 1 and kind != "history"
        depth = prompt.get("injection_depth", 4)
        blocks.append({
            "id": block_id,
            "kind": kind,
            "name": str(prompt.get("name") or DEFAULT_BLOCK_NAMES[kind])[:100],
            "enabled": bool(item.get("enabled", True)),
            "role": role,
            "text": prompt.get("content") or "" if isinstance(prompt.get("content"), str) else "",
            "position": "depth" if in_chat else "top",
            "depth": depth if isinstance(depth, int) and 0 <= depth <= 1000 else 4,
        })
    params: dict[str, Any] = {}
    for st_key, key in PARAMS.items():
        value = obj.get(st_key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if key in ("top_k", "max_tokens"):
            value = int(value)
        if OFF_VALUES.get(key) == value:
            continue
        params[key] = value
    effort = obj.get("reasoning_effort")
    if effort in REASONING_EFFORTS:  # "auto" and unknown values: let the model decide
        params["reasoning_effort"] = effort
    context = obj.get("openai_max_context")
    prompts_out = dict(DEFAULT_PROMPTS)
    for st_key, key in TEXTS.items():
        value = obj.get(st_key)
        if isinstance(value, str) and (value.strip() or key != "impersonate"):
            prompts_out[key] = value
    data = normalize_preset({
        "blocks": blocks,
        "params": params,
        "max_context_tokens": int(context) if isinstance(context, (int, float)) and 0 < context <= 10_000_000 else 0,
        "prompts": prompts_out,
        "st": {k: v for k, v in obj.items() if k not in CONSUMED},
    })
    return (name or "Импорт из SillyTavern").strip()[:100], data


def to_st(data: dict[str, Any]) -> dict[str, Any]:
    """A SillyTavern Chat Completion preset with our blocks and parameters."""
    prompts: list[dict[str, Any]] = []
    order: list[dict[str, Any]] = []

    def add(identifier: str, block: dict[str, Any], *, marker: bool, content: str = "") -> None:
        prompts.append({
            "identifier": identifier,
            "name": block["name"],
            "system_prompt": identifier in KIND_IDENTIFIERS.values() or identifier in CAST_IDENTIFIERS
            or identifier == "nsfw",
            "role": block["role"],
            "content": content,
            "marker": marker,
            "injection_position": 1 if block["position"] == "depth" else 0,
            "injection_depth": block["depth"],
        })
        order.append({"identifier": identifier, "enabled": block["enabled"]})

    for block in data["blocks"]:
        kind = block["kind"]
        if kind == "cast":
            for identifier in CAST_IDENTIFIERS:
                add(identifier, block, marker=True)
        elif kind in ("main", "post_history"):
            add(KIND_IDENTIFIERS[kind], block, marker=False, content=block["text"])
        elif kind == "text":
            add(block["id"], block, marker=False, content=block["text"])
        else:
            add(KIND_IDENTIFIERS[kind], block, marker=True)

    result: dict[str, Any] = dict(data.get("st") or {})
    for st_key, key in PARAMS.items():
        if key in data["params"]:
            result[st_key] = data["params"][key]
    if data.get("max_context_tokens"):
        result["openai_max_context"] = data["max_context_tokens"]
    for st_key, key in TEXTS.items():
        result[st_key] = data["prompts"][key]
    if "reasoning_effort" in data["params"]:
        result["reasoning_effort"] = data["params"]["reasoning_effort"]
    result["prompts"] = prompts
    result["prompt_order"] = [{"character_id": GLOBAL_ORDER_ID, "order": order}]
    return result
