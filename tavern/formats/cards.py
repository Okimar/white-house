"""Character card JSON (V1 flat, V2 and V3 wrappers) -> the `data` object Tavern stores.

Stage 1 handles JSON only; stage 2 adds PNG chunks and export. Unknown fields are kept as they are
so a card survives an import/export round trip.

Spec shapes (public character card specs):
    V1: {"name": ..., "description": ..., "first_mes": ...}                 (flat)
    V2: {"spec": "chara_card_v2", "spec_version": "2.0", "data": {...}}
    V3: {"spec": "chara_card_v3", "spec_version": "3.0", "data": {...}}
"""

from __future__ import annotations

from typing import Any

from tavern.errors import AppError

TEXT_FIELDS = (
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "system_prompt",
    "post_history_instructions",
    "creator_notes",
    "creator",
    "character_version",
)
SPECS = ("chara_card_v2", "chara_card_v3")


class CardError(AppError):
    code = "invalid_card"


def parse_card(obj: Any) -> tuple[str, dict[str, Any]]:
    """Return (spec, data). `data` always has a non-empty name and all text fields as str."""
    if not isinstance(obj, dict):
        raise CardError("A character card must be a JSON object")
    spec = obj.get("spec")
    if spec in SPECS:
        data = obj.get("data")
        if not isinstance(data, dict):
            raise CardError(f"{spec} card has no 'data' object")
    else:
        spec, data = "chara_card_v2", obj  # V1 / bare data object
    data = dict(data)

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CardError("Character needs a name")
    data["name"] = name.strip()
    for key in TEXT_FIELDS:
        value = data.get(key, "")
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise CardError(f"Field {key!r} must be text")
        data[key] = value
    greetings = data.get("alternate_greetings", [])
    if not isinstance(greetings, list) or not all(isinstance(g, str) for g in greetings):
        raise CardError("alternate_greetings must be a list of text")
    data["alternate_greetings"] = greetings
    tags = data.get("tags", [])
    data["tags"] = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    return spec, data


def greetings(data: dict[str, Any]) -> list[str]:
    """Opening messages in display order: first_mes, then alternates. Empty ones are dropped."""
    return [g for g in [data.get("first_mes", ""), *data.get("alternate_greetings", [])] if g.strip()]
