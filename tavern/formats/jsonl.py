"""JSONL export and import for Tavern chat trees."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

_FORMAT = "tavern_chat_v1"
RECORD_TYPES = {"header", "chat", "message", "swipes", "swipe_meta", "member", "chat_note", "message_note", "plugin"}


def _line(kind: str, value: Any) -> str:
    return json.dumps({"type": kind, "value": value}, ensure_ascii=False, separators=(",", ":"))


def export_chat_jsonl(
    chat: dict[str, Any],
    messages: Iterable[dict[str, Any]],
    swipes: Iterable[list[str]] = (),
    *,
    chat_notes: Iterable[dict[str, Any]] = (),
    message_notes: Iterable[dict[str, Any]] = (),
    swipe_metas: Iterable[list[dict[str, Any]]] = (),
    members: Iterable[dict[str, Any]] = (),
    plugins: Iterable[dict[str, Any]] = (),
) -> str:
    """Serialize one chat as a self-contained, line-oriented document.

    `chat_notes`: the chat's author's notes; `message_notes`: notes attached to its messages;
    `swipe_metas`: per message, what each version carried (thinking, pictures, spending);
    `members`: who takes part; `plugins`: what plugins keep with the chat, one `{"plugin", "version", "data"}` each
    (ADR 0019 point E). Files written before the last three are still read. A file with plugin records says
    `version: 2` in its header: an older build refuses the unknown record type, so the header tells why.
    """
    plugins = list(plugins)
    rows = [_line("header", {"format": _FORMAT, "version": 2 if plugins else 1})]
    rows.append(_line("chat", chat))
    rows.extend(_line("member", member) for member in members)
    rows.extend(_line("message", message) for message in messages)
    rows.extend(_line("swipes", values) for values in swipes)
    rows.extend(_line("swipe_meta", values) for values in swipe_metas)
    rows.extend(_line("chat_note", note) for note in chat_notes)
    rows.extend(_line("message_note", note) for note in message_notes)
    rows.extend(_line("plugin", record) for record in plugins)
    return "\n".join(rows) + "\n"


def import_chat_jsonl(source: str | Iterable[str]) -> dict[str, Any]:
    """Parse a JSONL document and preserve message/swipe dictionaries unchanged."""
    lines = source.splitlines() if isinstance(source, str) else source
    chat = None
    messages: list[dict[str, Any]] = []
    swipes: list[list[str]] = []
    swipe_metas: list[list[dict[str, Any]]] = []
    members: list[dict[str, Any]] = []
    chat_notes: list[dict[str, Any]] = []
    message_notes: list[dict[str, Any]] = []
    plugins: list[dict[str, Any]] = []
    header = None
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at line {number}") from exc
        if not isinstance(record, dict) or record.get("type") not in RECORD_TYPES:
            raise ValueError(f"Unknown JSONL record at line {number}")
        kind, value = record["type"], record.get("value")
        if kind == "header":
            if header is not None or not isinstance(value, dict) or value.get("format") != _FORMAT:
                raise ValueError("Invalid JSONL header")
            header = value
        elif kind == "chat":
            if chat is not None or not isinstance(value, dict):
                raise ValueError("Invalid JSONL chat record")
            if not isinstance(value.get("title", ""), str):
                raise ValueError("Invalid JSONL chat title")
            chat = value
        elif kind == "message":
            if not isinstance(value, dict):
                raise ValueError("Invalid JSONL message record")
            if value.get("role") not in {"user", "assistant", "system"}:
                raise ValueError("Invalid JSONL message role")
            if not isinstance(value.get("id"), str) or not isinstance(value.get("content"), str):
                raise ValueError("Invalid JSONL message fields")
            if value.get("parent_id") is not None and not isinstance(value["parent_id"], str):
                raise ValueError("Invalid JSONL message parent")
            messages.append(value)
        elif kind == "chat_note":
            if not isinstance(value, dict) or not isinstance(value.get("text", ""), str):
                raise ValueError("Invalid JSONL chat note")
            chat_notes.append(value)
        elif kind == "message_note":
            if (not isinstance(value, dict) or not isinstance(value.get("message_id"), str)
                    or not isinstance(value.get("text"), str)):
                raise ValueError("Invalid JSONL message note")
            message_notes.append(value)
        elif kind == "plugin":
            if (not isinstance(value, dict) or not isinstance(value.get("plugin"), str)
                    or not isinstance(value.get("data"), dict)):
                raise ValueError("Invalid JSONL plugin record")
            plugins.append(value)
        elif kind == "member":
            if not isinstance(value, dict) or not isinstance(value.get("character_id"), str):
                raise ValueError("Invalid JSONL member")
            members.append(value)
        elif kind == "swipe_meta":
            if not isinstance(value, list) or not all(isinstance(meta, dict) for meta in value):
                raise ValueError("Invalid JSONL swipe meta")
            swipe_metas.append(value)
        else:
            if not isinstance(value, list) or not all(isinstance(text, str) for text in value):
                raise ValueError("Invalid JSONL swipes record")
            swipes.append(value)
    if header is None or chat is None:
        raise ValueError("JSONL document needs a header and chat record")
    ids = {message.get("id") for message in messages}
    if None in ids or len(ids) != len(messages):
        raise ValueError("JSONL messages need unique ids")
    if any(message.get("parent_id") not in {None, *ids} for message in messages):
        raise ValueError("JSONL message parent is missing")
    return {"chat": chat, "messages": messages, "swipes": swipes, "swipe_metas": swipe_metas, "members": members,
            "chat_notes": chat_notes, "message_notes": message_notes, "plugins": plugins}
