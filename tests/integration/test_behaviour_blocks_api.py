"""Over HTTP: the members API takes no prompt texts, the default preset carries the behaviour blocks,
the narrator reads its block, and the settings for notes shown and skipped questions."""

from __future__ import annotations


async def chat_with_ann(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return ann, chat["id"]


async def test_members_api_refuses_prompt_texts(client):
    ann, chat_id = await chat_with_ann(client)
    for field in ("no_write_text", "narrator_text", "prompt", "speaker_reply"):
        response = await client.put(f"/api/chats/{chat_id}/members/{ann['id']}", json={field: "write for me"})
        assert response.status_code == 400


async def test_default_preset_lists_the_behaviour_blocks(client):
    data = (await client.get("/api/presets/default")).json()["data"]
    when = {b["when"]: b for b in data["blocks"] if b.get("when")}
    assert set(when) == {"no_write_for_me", "speaker_reply", "impersonate", "narrator"}
    assert all(b["enabled"] and b["text"].strip() for b in when.values())
    assert set(data["prompts"]) == {"prefill", "personality_format", "scenario_format"}


async def test_a_copy_of_the_default_keeps_a_switched_off_behaviour_block(client):
    created = (await client.post("/api/presets", json={"name": "Mine", "from": "default"})).json()
    data = created["data"]
    next(b for b in data["blocks"] if b.get("when") == "narrator")["enabled"] = False
    saved = (await client.put(f"/api/presets/{created['id']}", json={"name": "Mine", "data": data})).json()
    assert next(b for b in saved["data"]["blocks"] if b.get("when") == "narrator")["enabled"] is False


async def test_notes_shown_and_skipped_questions_settings(client):
    assert (await client.put("/api/settings", json={"notes_shown": 3})).json()["notes_shown"] == 3
    for bad in (0, 21, "2", True):
        assert (await client.put("/api/settings", json={"notes_shown": bad})).status_code == 400
    ok = await client.put("/api/settings", json={"skip_confirms": {"message": True, "note": True}})
    assert ok.json()["skip_confirms"] == {"message": True, "note": True}
    assert (await client.put("/api/settings", json={"skip_confirms": {"message": False}})).status_code == 400
    assert (await client.put("/api/settings", json={"service_texts_moved": []})).status_code == 200
