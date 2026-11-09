"""The reply's meaning request keeps its query vectors (ADR 0019 section 2.2-F) and is made for a plugin even when
no bubble has a vector, only when the filter event `meaning.wanted` says so."""

from __future__ import annotations

from tavern.services import character_tree, lorebooks


async def connect(client):
    conn = (await client.post("/api/connections", json={"name": "f", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"], "embedding_connection": conn["id"]})
    return conn


async def test_no_request_without_block_vectors_unless_a_plugin_wants_one(client, ctx, fake_llm):
    await connect(client)
    empty = await character_tree.meaning_search(ctx, [], ["текст"])
    assert fake_llm.embedding_requests == [] and empty.queries == [] and empty.scores == {}
    wanted = await character_tree.meaning_search(ctx, [], ["текст", "ещё"], wanted=True)
    assert len(fake_llm.embedding_requests) == 1 and fake_llm.embedding_requests[0]["input"] == ["текст", "ещё"]
    assert len(wanted.queries) == 2 and wanted.texts == ["текст", "ещё"] and wanted.model == "m"
    assert wanted.scores == {} and wanted.error is None


async def test_a_meaning_still_unpacks_as_scores_and_error(client, ctx, fake_llm):
    await connect(client)
    scores, error = await character_tree.meaning_search(ctx, [], ["текст"], wanted=True)
    assert scores == {} and error is None


async def test_a_failing_service_gives_the_error_and_no_vectors(client, ctx, fake_llm):
    await connect(client)
    fake_llm.failures.append((500, "сломалось"))
    meaning = await character_tree.meaning_search(ctx, [], ["текст"], wanted=True)
    assert meaning.error and meaning.queries == []


async def test_meaning_for_reply_passes_wanted_through(client, ctx, fake_llm):
    await connect(client)
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    history = [{"id": "1", "role": "user", "content": "привет", "exclude_from_prompt": False}]
    quiet = await lorebooks.meaning_for_reply(ctx, chat, [], history)
    assert fake_llm.embedding_requests == [] and quiet.queries == []
    asked = await lorebooks.meaning_for_reply(ctx, chat, [], history, wanted=True)
    assert len(fake_llm.embedding_requests) == 1 and len(asked.queries) == 1


async def test_the_chat_asks_the_filter_and_a_plugin_can_answer_yes(client, ctx, fake_llm):
    import asyncio
    import json

    await connect(client)
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    seen = []

    def wants(value, chat=None, **_):
        seen.append(chat["id"])
        return True

    ctx.events.on("meaning.wanted", wants, owner="test")
    job_meanings = []
    ctx.events.on("chat.before_generate", lambda job: job_meanings.append(job.meaning), owner="test")
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "привет"})).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    assert seen == [chat["id"]]
    assert len(fake_llm.embedding_requests) == 1  # one request, because a plugin wanted it
    assert len(job_meanings) == 1 and len(job_meanings[0].queries) == 1  # and the job carries its vectors
    assert json.loads("{}") == {}
