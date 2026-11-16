"""Wave 2, author's notes over HTTP: the place of a note (migration 0026), the author's note of an imported
SillyTavern chat, and the lorebook setting that lets the scan read the fired notes (off, as in SillyTavern)."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import quote


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return chat["id"]


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


def sent(fake_llm) -> list[dict]:
    return fake_llm.requests[-1]["messages"]


async def test_a_note_goes_before_the_main_prompt_and_the_place_is_checked(client, ctx, fake_llm):
    chat_id = await setup(client)
    made = await client.post("/api/notes", json={"scope": "chat", "owner_id": chat_id, "text": "FIRST WORDS",
                                                 "place": "before_main"})
    assert made.status_code == 201 and made.json()["place"] == "before_main"
    bad = await client.post("/api/notes", json={"scope": "chat", "owner_id": chat_id, "text": "x", "place": "top"})
    assert bad.json()["error"]["code"] == "bad_note"
    await send(client, ctx, chat_id, "hi")
    assert sent(fake_llm)[0]["content"].startswith("FIRST WORDS")
    assert (await client.get(f"/api/chats/{chat_id}/message-notes")).json()["notes"] == []  # never kept


async def test_the_lorebook_scan_reads_the_notes_only_when_asked(client, ctx, fake_llm):
    chat_id = await setup(client)
    await client.post("/api/notes", json={"scope": "chat", "owner_id": chat_id, "text": "The dragon is near."})
    book = (await client.post("/api/lorebooks", json={"name": "W"})).json()
    await client.post(f"/api/lorebooks/{book['id']}/blocks",
                      json={"title": "Dragon", "content": "DRAGON LORE", "keywords": ["dragon"]})
    await client.put("/api/lorebook-uses", json={"scope": "chat", "target": chat_id, "lorebook_ids": [book["id"]]})
    await send(client, ctx, chat_id, "hello")
    assert "DRAGON LORE" not in json.dumps(sent(fake_llm), ensure_ascii=False)
    assert (await client.put("/api/settings", json={"lorebooks": {"scan_notes": True}})).status_code == 200
    await send(client, ctx, chat_id, "hello again")
    assert "DRAGON LORE" in json.dumps(sent(fake_llm), ensure_ascii=False)


async def test_an_imported_sillytavern_chat_brings_its_author_note(client, ctx):
    rows = [{"chat_metadata": {"note_prompt": "Keep it dark.", "note_depth": 2, "note_position": 1,
                               "note_interval": 1, "note_role": 0}, "user_name": "unused", "character_name": "unused"},
            {"name": "Char", "is_user": False, "mes": "Hello."}, {"name": "Me", "is_user": True, "mes": "Hi."}]
    body = "\n".join(json.dumps(r) for r in rows).encode()
    response = await client.post("/api/chats/import.jsonl", content=body, headers={"X-Filename": quote("c.jsonl")})
    assert response.status_code == 201, response.text
    notes = (await client.get(f"/api/notes?scope=chat&owner_id={response.json()['chat']['id']}")).json()["notes"]
    assert [(n["text"], n["depth"], n["place"], n["interval"], n["enabled"]) for n in notes] == [
        ("Keep it dark.", 2, "depth", 0, True)]
