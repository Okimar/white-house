"""Character card JSON (V1 flat, V2 and V3 wrappers) -> the `data` object Tavern stores.

Stage 1 handles JSON only; stage 2 adds PNG chunks and export. Unknown fields are kept as they are
so a card survives an import/export round trip.

Spec shapes (public character card specs):
    V1: {"name": ..., "description": ..., "first_mes": ...}                 (flat)
    V2: {"spec": "chara_card_v2", "spec_version": "2.0", "data": {...}}
    V3: {"spec": "chara_card_v3", "spec_version": "3.0", "data": {...}}
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
import zlib
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
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CardError(AppError):
    code = "invalid_card"


def encode_card(data: dict[str, Any], *, spec: str = "chara_card_v2") -> dict[str, Any]:
    """Wrap normalized card data in the public V2/V3 JSON envelope."""
    if spec not in SPECS:
        raise CardError(f"Unsupported card spec {spec!r}")
    _, normalized = parse_card(data)
    version = "3.0" if spec == "chara_card_v3" else "2.0"
    return {"spec": spec, "spec_version": version, "data": normalized}


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", binascii.crc32(body) & 0xFFFFFFFF)


def card_to_png(data: dict[str, Any], *, spec: str = "chara_card_v2") -> bytes:
    """Create a valid 1x1 RGBA PNG carrying a base64 ``chara`` tEXt chunk."""
    raw = json.dumps(encode_card(data, spec=spec), ensure_ascii=False, separators=(",", ":")).encode()
    text = b"chara\x00" + base64.b64encode(raw)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    scanline = b"\x00\x00\x00\x00\xff"
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(scanline))
        + _png_chunk(b"tEXt", text)
        + _png_chunk(b"IEND", b"")
    )


def _decode_text_chunk(kind: bytes, payload: bytes) -> tuple[str, bytes] | None:
    """Extract keyword and encoded text from PNG tEXt, zTXt or iTXt."""
    if kind == b"tEXt":
        keyword, sep, text = payload.partition(b"\x00")
        return keyword.decode("latin-1"), text if sep else b""
    if kind == b"zTXt":
        keyword, sep, rest = payload.partition(b"\x00")
        if not sep or not rest or rest[0] != 0:
            return None
        try:
            return keyword.decode("latin-1"), zlib.decompress(rest[1:])
        except zlib.error as exc:
            raise CardError("Invalid compressed PNG text chunk") from exc
    if kind == b"iTXt":
        keyword, sep, rest = payload.partition(b"\x00")
        if not sep or len(rest) < 2:
            return None
        compression_flag, compression_method = rest[0], rest[1]
        rest = rest[2:]
        language, sep, rest = rest.partition(b"\x00")
        if not sep:
            return None
        translated, sep, text = rest.partition(b"\x00")
        if not sep:
            return None
        if compression_flag == 1:
            if compression_method != 0:
                return None
            try:
                text = zlib.decompress(text)
            except zlib.error as exc:
                raise CardError("Invalid compressed PNG international text chunk") from exc
        elif compression_flag != 0:
            return None
        return keyword.decode("latin-1"), text
    return None


def decode_png_card(blob: bytes) -> tuple[str, dict[str, Any]]:
    """Read a character card from PNG metadata without decoding the image pixels."""
    if not blob.startswith(PNG_SIGNATURE):
        raise CardError("Character card is not a PNG image")
    pos = len(PNG_SIGNATURE)
    encoded: bytes | None = None
    while pos + 12 <= len(blob):
        length = struct.unpack(">I", blob[pos : pos + 4])[0]
        end = pos + 12 + length
        if end > len(blob):
            raise CardError("Truncated PNG chunk")
        kind = blob[pos + 4 : pos + 8]
        payload = blob[pos + 8 : pos + 8 + length]
        checksum = struct.unpack(">I", blob[pos + 8 + length : end])[0]
        if binascii.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise CardError("Invalid PNG chunk checksum")
        text = _decode_text_chunk(kind, payload)
        if text and text[0].lower() == "chara":
            encoded = text[1]
            break
        pos = end
    if encoded is None:
        raise CardError("PNG has no chara card metadata")
    try:
        decoded = base64.b64decode(encoded, validate=True)
        obj = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise CardError("PNG chara metadata is not valid base64 JSON") from exc
    return parse_card(obj)
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
