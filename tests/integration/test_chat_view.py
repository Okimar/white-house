"""Chat view «Пузыри» / «Документ»: the app-wide setting and the per-chat override kept in the chat's meta."""

from __future__ import annotations


async def _chat(client) -> str:
    character = (await client.post("/api/characters", json={"name": "Ann", "first_mes": "Hi"})).json()
    return (await client.post("/api/chats", json={"character_id": character["id"]})).json()["chat"]["id"]


async def test_the_view_setting_is_kept(client):
    assert (await client.put("/api/settings", json={"chat_view": "document"})).status_code == 200
    assert (await client.get("/api/settings")).json()["chat_view"] == "document"
    assert (await client.put("/api/settings", json={"chat_view": "bubbles"})).status_code == 200
    assert (await client.get("/api/settings")).json()["chat_view"] == "bubbles"


async def test_an_unknown_view_setting_is_refused(client):
    assert (await client.put("/api/settings", json={"chat_view": "scroll"})).status_code == 400
    assert (await client.put("/api/settings", json={"chat_view": 3})).status_code == 400


async def test_a_chat_keeps_its_own_view_and_can_return_to_the_setting(client):
    chat_id = await _chat(client)
    saved = (await client.put(f"/api/chats/{chat_id}/meta", json={"chat_view": "document"})).json()
    assert saved["meta"]["chat_view"] == "document"
    assert (await client.get(f"/api/chats/{chat_id}")).json()["chat"]["meta"]["chat_view"] == "document"
    cleared = (await client.put(f"/api/chats/{chat_id}/meta", json={"chat_view": ""})).json()
    assert "chat_view" not in cleared["meta"]


async def test_the_chat_view_does_not_touch_other_meta(client):
    chat_id = await _chat(client)
    await client.put(f"/api/chats/{chat_id}/meta", json={"generator": "thoughts"})
    meta = (await client.put(f"/api/chats/{chat_id}/meta", json={"chat_view": "bubbles"})).json()["meta"]
    assert meta["generator"] == "thoughts" and meta["chat_view"] == "bubbles"


async def test_an_unknown_chat_view_is_refused(client):
    chat_id = await _chat(client)
    assert (await client.put(f"/api/chats/{chat_id}/meta", json={"chat_view": "scroll"})).status_code == 400
    assert (await client.put(f"/api/chats/{chat_id}/meta", json={"chat_view": 1})).status_code == 400
