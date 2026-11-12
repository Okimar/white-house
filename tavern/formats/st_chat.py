"""SillyTavern chat files (.jsonl, sometimes saved as .json) -> the shape `import_chat_jsonl` returns.

Format as observed in exported files: line 1 is a header (`chat_metadata`, `user_name`, `character_name`,
the names often "unused"), every other line is one message: `name`, `is_user`, `is_system` (may be missing),
`mes`, optional `swipes` / `swipe_id` / `swipe_info[i].extra`, `extra.reasoning`. The chat is a straight
line. `mes` is what was shown (macros filled in), so it replaces the active swipe's text.
"""

from __future__ import annotations

import json
from typing import Any

_KEEP_META = ("reasoning", "model", "api")


def _rows(text: str) -> list[Any]:
    rows: list[Any] = []
    stripped = text.lstrip("﻿").strip()
    if stripped.startswith("["):  # a whole-file JSON array (some tools save it like that)
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return data
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            return []
    return rows


def _is_message(row: Any) -> bool:
    return isinstance(row, dict) and isinstance(row.get("mes"), str) and ("is_user" in row or "name" in row)


def is_st_chat(text: str) -> bool:
    """True when the text looks like a SillyTavern chat: a header line and / or `mes` lines, no `type` records."""
    rows = _rows(text)
    if not rows or not all(isinstance(row, dict) for row in rows):
        return False
    if any("type" in row and "value" in row for row in rows):
        return False
    first = rows[0]
    header = "chat_metadata" in first or "user_name" in first or "character_name" in first
    body = rows[1:] if header else rows
    return bool(body or header) and all(_is_message(row) for row in body)


def _meta(extra: Any) -> dict[str, Any]:
    if not isinstance(extra, dict):
        return {}
    return {key: extra[key] for key in _KEEP_META if isinstance(extra.get(key), str) and extra[key]}


def _name(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return "" if text.lower() == "unused" else text


def parse_st_chat(text: str) -> dict[str, Any]:
    """Messages with ids, parents, swipes and swipe metas; `character_name` / `user_name` taken from the messages
    when the header says "unused". Raises ValueError (Russian) when there is nothing to import."""
    if not is_st_chat(text):
        raise ValueError("Это не чат SillyTavern")
    rows = _rows(text)
    header = rows[0] if ("chat_metadata" in rows[0] or "user_name" in rows[0]
                         or "character_name" in rows[0]) else {}
    body = rows[1:] if header else rows
    if not body:
        raise ValueError("В файле чата нет ни одного сообщения")
    messages: list[dict[str, Any]] = []
    swipes: list[list[str]] = []
    metas: list[list[dict[str, Any]]] = []
    character = _name(header.get("character_name"))
    user = _name(header.get("user_name"))
    parent = None
    for number, row in enumerate(body, 1):
        is_user = bool(row.get("is_user"))
        name = row.get("name") if isinstance(row.get("name"), str) else ""
        if is_user and not user and name:
            user = name
        if not is_user and not character and name and not row.get("is_system"):
            character = name
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        narrator = extra.get("type") == "narrator"
        message_id = f"st-{number}"
        messages.append({"id": message_id, "parent_id": parent, "depth": number - 1,
                         "role": "user" if is_user else "system" if narrator else "assistant",
                         "name": name, "content": row["mes"],
                         "exclude_from_prompt": bool(row.get("is_system")) and not narrator})
        parent = message_id
        values = [v for v in row.get("swipes") or [] if isinstance(v, str)]
        info = row.get("swipe_info") if isinstance(row.get("swipe_info"), list) else []
        active = row.get("swipe_id") if isinstance(row.get("swipe_id"), int) else 0
        if not values or len(values) != len(row.get("swipes") or []):
            values, info, active = [row["mes"]], [{"extra": extra}], 0
        if not 0 <= active < len(values):
            active = 0
        values[active] = row["mes"]
        row_metas = [_meta(item.get("extra")) if isinstance(item, dict) else {} for item in info[:len(values)]]
        row_metas += [{} for _ in range(len(values) - len(row_metas))]
        if not row_metas[active]:
            row_metas[active] = _meta(extra)
        messages[-1]["active_swipe"] = active
        swipes.append(values)
        metas.append(row_metas)
    return {"chat": {"leaf_id": parent}, "messages": messages, "swipes": swipes, "swipe_metas": metas,
            "members": [], "chat_notes": [], "message_notes": [], "plugins": [],
            "character_name": character, "user_name": user}
