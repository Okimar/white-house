"""Regression: a chat exported by SillyTavern (.jsonl, maybe sent as .json, BOM, CRLF, big) imports.

The owner's file failed with «Unknown JSONL record at line 1»: only our own format was read.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from tavern.formats.st_chat import is_st_chat, parse_st_chat

BLANK = "⠀"
FILENAME = "Универсальный_рассказчик_2026_11_04@03h21m31s689ms.jsonl"


def _st_file(replies: int = 30, filler: int = 0) -> str:
    rows = [{"chat_metadata": {"integrity": "0f6c2d7e-1111-4222-8333-944455556666", "note_prompt": "\n",
                               "note_interval": 1, "note_position": 1, "note_depth": 4, "note_role": 0,
                               "timedWorldInfo": {"sticky": {}, "cooldown": {}}, "tainted": True,
                               "lastInContextMessageId": 3},
             "user_name": "unused", "character_name": "unused"}]
    swipes = [f"Greeting {n} for {{{{user}}}}.\r\n\r\nMore." for n in range(5)]
    first = {"name": "Char", "is_user": False, "is_system": False, "send_date": "2026-11-04T00:21:31.737Z",
             "mes": "Greeting 0 for SKRIPTY.\r\n\r\nMore.", "extra": {}, "swipe_id": 0, "swipes": swipes,
             "swipe_info": [{"send_date": "2026-11-04T00:21:31.737Z", "extra": {}} for _ in swipes]}
    rows.append(first)
    pad = "x" * filler
    for n in range(replies):
        user = {"name": "SKRIPTY", "is_user": True, "is_system": False, "send_date": "2026-11-04T00:22:00.000Z",
                "mes": f"[c4]{BLANK}User{BLANK}line{BLANK}{n}\nsecond", "extra": {"isSmallSys": False, "reasoning": ""},
                "force_avatar": "/thumbnail?type=persona&file=user.png"}
        if n % 2:
            user["extra"]["bias"] = ""
        extra = {"api": "custom", "model": "some-model", "reasoning": f"Thinking {n}",
                 "reasoning_duration": None if n % 2 else 1234, "reasoning_signature": None,
                 "time_to_first_token": 900, "reasoning_type": "parsed"}
        bot = {"extra": extra, "name": "Char", "is_user": False, "send_date": "2026-11-04T00:23:00.000Z",
               "mes": f"Reply{BLANK}{n}\r\n{pad}", "title": "", "gen_started": "2026-11-04T00:22:01.000Z",
               "gen_finished": "2026-11-04T00:23:00.000Z", "swipes": [f"Reply{BLANK}{n}\r\n{pad}"], "swipe_id": 0,
               "swipe_info": [{"send_date": "2026-11-04T00:23:00.000Z", "gen_started": "x", "gen_finished": "y",
                               "extra": dict(extra)}]}
        rows += [user, bot]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"


def test_the_parser_reads_the_sillytavern_shape():
    text = _st_file(3)
    assert is_st_chat(text)
    parsed = parse_st_chat(text)
    messages = parsed["messages"]
    assert len(messages) == 7
    assert [m["role"] for m in messages[:3]] == ["assistant", "user", "assistant"]
    assert parsed["character_name"] == "Char" and parsed["user_name"] == "SKRIPTY"
    assert len(parsed["swipes"][0]) == 5 and parsed["swipes"][0][0] == "Greeting 0 for SKRIPTY.\r\n\r\nMore."
    assert parsed["swipe_metas"][2][0]["reasoning"] == "Thinking 0"


async def _import(client, body: bytes, filename: str = FILENAME):
    return await client.post("/api/chats/import.jsonl", content=body,
                             headers={"X-Filename": quote(filename)})


async def test_a_sillytavern_chat_imports_with_swipes_thinking_and_names(client, ctx):
    char = (await client.post("/api/characters", json={"name": "Char", "description": "d"})).json()
    response = await _import(client, _st_file(30, filler=20_000).encode("utf-8"))
    assert response.status_code == 201, response.text
    chat = response.json()["chat"]
    assert chat["character_id"] == char["id"]
    assert "Универсальный рассказчик" in chat["title"]
    path = ctx.chats.active_path(chat["id"])
    assert len(path) == 61
    assert path[0]["role"] == "assistant" and path[0]["character_id"] == char["id"]
    assert path[1]["role"] == "user" and path[1]["name"] == "SKRIPTY"
    assert path[1]["content"].startswith("[c4]" + BLANK)
    assert [v["content"] for v in ctx.chats.list_swipes(path[0]["id"])][1] == "Greeting 1 for {{user}}.\r\n\r\nMore."
    assert len(ctx.chats.list_swipes(path[0]["id"])) == 5
    assert ctx.chats.list_swipes(path[2]["id"])[0]["meta"]["reasoning"] == "Thinking 0"


async def test_bom_crlf_and_json_extension_also_import(client, ctx):
    body = ("﻿" + _st_file(2).replace("\n", "\r\n")).encode("utf-8")
    response = await _import(client, body, "chat.json")
    assert response.status_code == 201, response.text
    assert len(ctx.chats.active_path(response.json()["chat"]["id"])) == 5


async def test_a_chat_with_no_known_character_still_imports(client, ctx):
    response = await _import(client, _st_file(1).encode("utf-8"))
    assert response.status_code == 201, response.text
    assert response.json()["chat"]["character_id"] is None


async def test_a_bad_file_says_why_in_russian(client):
    for body in (b"not json at all", b"{\"x\": 1}\n", "﻿[1,2]".encode()):
        response = await _import(client, body)
        assert response.status_code == 400
        message = response.json()["error"]["message"]
        assert any("а" <= ch <= "я" for ch in message.lower()), message
