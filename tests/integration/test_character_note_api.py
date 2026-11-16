"""Wave 6 over HTTP: an imported V2 card keeps its «Character's Note» (extensions.depth_prompt) and the chat sends
it every reply at its depth; in the text mode «Написать за меня» opens the user's turn."""

from __future__ import annotations

import asyncio

from tavern.formats.cards import card_to_png


async def finish(ctx, response):
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


async def test_an_imported_cards_character_note_reaches_the_model(client, ctx, fake_llm):
    card = {"name": "Ann", "description": "Ann is kind.", "first_mes": "Hello.",
            "extensions": {"depth_prompt": {"prompt": "{{char}} never lies.", "depth": 0, "role": "system"}}}
    imported = await client.post("/api/characters/import/png", content=card_to_png(card))
    assert imported.status_code == 201, imported.text
    character = imported.json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": character["id"]})).json()["chat"]["id"]
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi"}))
    assert fake_llm.requests[-1]["messages"][-1] == {"role": "system", "content": "Ann never lies."}


async def test_text_mode_impersonation_opens_the_users_turn(client, ctx, fake_llm):
    conn = (await client.post("/api/connections", json={
        "name": "local", "base_url": "http://llm/v1", "model": "Qwen2.5-7B-Instruct",
        "params": {"api_mode": "text", "template": "auto"}})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "d", "first_mes": "Hi."})).json()
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    fake_llm.queue("I wave.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/impersonate"))
    sent = fake_llm.text_requests[-1]
    assert sent["prompt"].endswith("<|im_start|>user\n") and "point of view" not in sent["prompt"]
    assert "\nAnn:" in sent["stop"]
