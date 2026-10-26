"""Stage 2 tests for public character-card and JSONL formats."""

from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

from tavern.formats.cards import (
    CardError,
    card_to_png,
    decode_png_card,
    encode_card,
    parse_card,
)
from tavern.formats.jsonl import export_chat_jsonl, import_chat_jsonl
from tavern.core.prompt import substitute


CARD = {
    "name": "Ann",
    "description": "{{char}} knows {{user}}.",
    "first_mes": "Hello {{user}}!",
    "alternate_greetings": ["Hi again."],
    "tags": ["test"],
    "creator_notes": "round trip",
}


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)


def png_with_text(kind: bytes, payload: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + png_chunk(kind, payload) + png_chunk(b"IEND", b"")


def test_encode_card_uses_v2_wrapper_and_parse_round_trips_unknown_fields():
    encoded = encode_card(CARD, spec="chara_card_v2")
    assert encoded["spec"] == "chara_card_v2"
    assert encoded["spec_version"] == "2.0"
    assert parse_card(encoded)[1]["creator_notes"] == "round trip"


@pytest.mark.parametrize("kind", ["tEXt", "zTXt", "iTXt"])
def test_decode_png_card_supports_public_text_chunk_variants(kind):
    raw = json.dumps(encode_card(CARD), ensure_ascii=False).encode()
    if kind == "tEXt":
        payload = b"chara\x00" + base64.b64encode(raw)
    elif kind == "zTXt":
        payload = b"chara\x00\x00" + zlib.compress(base64.b64encode(raw))
    else:
        payload = b"chara\x00\x00\x00\x00\x00" + base64.b64encode(raw)
    spec, data = decode_png_card(png_with_text(kind.encode(), payload))
    assert spec == "chara_card_v2" and data["name"] == "Ann"


def test_card_to_png_can_be_decoded_without_external_image_library():
    png = card_to_png(CARD)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert decode_png_card(png)[1]["name"] == "Ann"


def test_macros_include_random_choices_and_dice_with_injected_rng():
    assert substitute("{{char}}/{{user}}", char="Ann", user="Bob") == "Ann/Bob"
    assert substitute("{{random::red::blue}}", char="", user="", rng=lambda n: 1) == "blue"
    assert substitute("{{roll:1d6}}", char="", user="", rng=lambda n: 3) == "4"


def test_jsonl_chat_round_trip_preserves_tree_swipes_and_metadata():
    chat = {
        "id": "chat-1", "character_id": "char-1", "title": "Test",
        "meta": {"connection_id": "conn-1"}, "leaf_id": "m2",
    }
    messages = [
        {"id": "m1", "chat_id": "chat-1", "parent_id": None, "depth": 0,
         "role": "user", "name": "Bob", "hidden": False, "exclude_from_prompt": False,
         "owner": "", "content": "Hi", "active_swipe": 0, "swipe_count": 1,
         "extra": {}, "created_at": "now"},
        {"id": "m2", "chat_id": "chat-1", "parent_id": "m1", "depth": 1,
         "role": "assistant", "name": "Ann", "hidden": False, "exclude_from_prompt": False,
         "owner": "", "content": "Hello", "active_swipe": 1, "swipe_count": 2,
         "extra": {"x": 1}, "created_at": "now"},
    ]
    lines = export_chat_jsonl(chat, messages, [["Hello", "Hi again."]])
    imported = import_chat_jsonl(lines)
    assert imported["chat"] == chat
    assert imported["messages"] == messages
    assert imported["swipes"] == [["Hello", "Hi again."]]


def test_invalid_png_and_jsonl_are_rejected():
    with pytest.raises(CardError):
        decode_png_card(b"not a png")
    with pytest.raises(ValueError):
        import_chat_jsonl('{"type":"unknown"}\n')
