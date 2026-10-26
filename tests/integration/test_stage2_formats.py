"""HTTP tests for stage 2 card and JSONL endpoints."""

from __future__ import annotations

from tavern.formats.cards import card_to_png


async def test_character_png_import_and_json_png_exports(client):
    card = {"name": "PNG Ann", "description": "d", "first_mes": "hi"}
    response = await client.post("/api/characters/import/png", content=card_to_png(card))
    assert response.status_code == 201
    character = response.json()
    character_id = character["id"]

    exported_json = await client.get(f"/api/characters/{character_id}/export.json")
    assert exported_json.status_code == 200
    assert exported_json.json()["data"]["name"] == "PNG Ann"

    exported_png = await client.get(f"/api/characters/{character_id}/export.png")
    assert exported_png.status_code == 200
    assert exported_png.headers["content-type"] == "image/png"
    assert exported_png.content == card_to_png(card)


async def test_character_png_filename_cannot_inject_headers(client):
    character = (await client.post("/api/characters", json={"name": "bad\"\r\nname"})).json()
    response = await client.get(f"/api/characters/{character['id']}/export.png")
    assert "\r" not in response.headers["content-disposition"]
    assert "\n" not in response.headers["content-disposition"]
    assert '"' in response.headers["content-disposition"]


async def test_chat_jsonl_export_and_import(client, ctx):
    character = (await client.post("/api/characters", json={"name": "Ann"})).json()
    opened = await client.post("/api/chats", json={"character_id": character["id"]})
    chat_id = opened.json()["chat"]["id"]
    chat = ctx.chats.get_chat(chat_id)
    user = ctx.chats.add_message(chat_id, role="user", content="hello", parent_id=chat["leaf_id"])
    reply = ctx.chats.add_message(chat_id, role="assistant", content="one", parent_id=user["id"])
    ctx.chats.add_swipe(reply["id"], "two")
    ctx.chats.add_message(chat_id, role="assistant", content="branch", parent_id=user["id"], set_leaf=False)

    exported = await client.get(f"/api/chats/{chat_id}/export.jsonl")
    assert exported.status_code == 200
    imported = await client.post("/api/chats/import.jsonl", content=exported.content)
    assert imported.status_code == 201
    new_id = imported.json()["chat"]["id"]
    page = (await client.get(f"/api/chats/{new_id}")).json()
    assert [message["content"] for message in page["messages"]][-2:] == ["hello", "two"]


async def test_invalid_card_png_and_jsonl_return_structured_errors(client):
    png = await client.post("/api/characters/import/png", content=b"no")
    assert png.status_code == 400 and png.json()["error"]["code"] == "invalid_card"
    jsonl = await client.post("/api/chats/import.jsonl", content=b'{"type":"bad"}\n')
    assert jsonl.status_code == 400 and jsonl.json()["error"]["code"] == "invalid_jsonl"
