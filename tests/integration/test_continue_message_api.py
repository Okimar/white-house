"""«Дописать» over HTTP (wave 5): the model goes on with the last message; the text is added to the same message
and version; a stop or an empty answer leaves the message as it was."""

from __future__ import annotations

import asyncio


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind.",
                                                      "first_mes": "He opened the"})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return chat["id"]


async def run(client, ctx, chat_id):
    response = await client.post(f"/api/chats/{chat_id}/continue-message")
    assert response.status_code == 202, response.text
    found = ctx.runs.get(response.json()["run_id"])
    await asyncio.wait([found.task], timeout=10)
    return found


async def messages(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]


async def test_the_last_message_is_continued_in_place(client, ctx, fake_llm):
    chat_id = await setup(client)
    fake_llm.queue("the door.")  # the last word again, then on (asked in the continue instruction)
    found = await run(client, ctx, chat_id)
    after = await messages(client, chat_id)
    assert len(after) == 1 and after[0]["content"] == "He opened the door."
    assert after[0]["swipe_count"] == 1  # no new version: the same one grew
    sent = fake_llm.requests[-1]["messages"]
    assert sent[-2]["role"] == "assistant" and sent[-2]["content"] == "He opened the"
    assert sent[-1]["role"] == "user" and sent[-1]["content"].startswith(
        "[Continue your last message without repeating its original content.]") and "«He opened the»" in sent[-1]["content"]
    deltas = "".join(e["text"] for e in found.events if e["type"] == "delta")
    assert deltas.startswith("He opened the")  # the page's stream starts from the old text
    fake_llm.queue("windows.")  # a cut word, repeated whole
    await client.put(f"/api/messages/{after[0]['id']}", json={"content": "He opened the win"})
    await run(client, ctx, chat_id)
    assert (await messages(client, chat_id))[0]["content"] == "He opened the windows."


async def test_an_empty_answer_leaves_the_message_alone(client, ctx, fake_llm):
    chat_id = await setup(client)
    fake_llm.queue("   ")
    await run(client, ctx, chat_id)
    assert [m["content"] for m in await messages(client, chat_id)] == ["He opened the"]


async def test_nothing_to_continue_in_an_empty_chat(client):
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    response = await client.post(f"/api/chats/{chat_id}/continue-message")
    assert response.json()["error"]["code"] == "nothing_to_continue"
