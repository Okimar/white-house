"""Stage 7а over HTTP: who answers in a chat with several characters, what the model is told,
"ответь ты", and characters brought in by code (as agents will)."""

from __future__ import annotations

import asyncio

from tavern.storage.db import Database
from tavern.storage.migrations import load_migrations


async def make_character(client, name, **fields):
    return (await client.post("/api/characters", json={"name": name, **fields})).json()


async def two_characters(client):
    """Ann (greets) and Bob in one chat, a connection to the fake."""
    ann = await make_character(client, "Ann", description="Ann is kind.", first_mes="Hello")
    bob = await make_character(client, "Bob", description="Bob the smith.")
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    await client.post(f"/api/chats/{chat['id']}/members", json={"character_id": bob["id"]})
    return ann, bob, chat["id"]


async def finish(ctx, response):
    assert response.status_code == 202, response.text
    run = ctx.runs.get(response.json()["run_id"])
    await asyncio.wait([run.task], timeout=10)


async def messages(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]


def sent(fake_llm):
    return [m["content"] for m in fake_llm.requests[-1]["messages"]]


async def test_the_asked_character_answers_seeing_only_its_own_card(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    greeting = (await messages(client, chat_id))[0]
    assert greeting["character_id"] == ann["id"]

    fake_llm.queue("Bob here.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi", "speaker": bob["id"]}))
    reply = (await messages(client, chat_id))[-1]
    assert (reply["name"], reply["character_id"], reply["content"]) == ("Bob", bob["id"], "Bob here.")
    text = "\n".join(sent(fake_llm))
    assert "Bob the smith." in text and "Ann is kind." not in text  # others: none (the default)
    assert "Ann: Hello" in sent(fake_llm)  # another character's line goes with the name
    assert sent(fake_llm)[-1].startswith("[Write the next reply only as Bob.")


async def test_the_others_can_be_named_or_described(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/members/{ann['id']}", json={"blurb": "  a kind healer  "})
    saved = (await client.put(f"/api/chats/{chat_id}/speakers", json={"others": "names"})).json()["speakers"]
    assert (saved["order"], saved["others"], saved["talk_limit"]) == ("natural", "names", 4)
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi", "speaker": bob["id"]}))
    assert any(c.endswith("Also in the scene:\n- Ann") for c in sent(fake_llm))

    await client.put(f"/api/chats/{chat_id}/speakers", json={"others": "blurbs"})
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi", "speaker": bob["id"]}))
    assert any("- Ann: a kind healer" in c for c in sent(fake_llm))
    bad = await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "chaos"})
    assert bad.json()["error"]["code"] == "bad_speakers"


async def test_regenerate_keeps_the_speaker_and_a_named_one_answers(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "Bob, your turn"}))
    assert (await messages(client, chat_id))[-1]["character_id"] == bob["id"]
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/regenerate"))
    last = (await messages(client, chat_id))[-1]
    assert last["character_id"] == bob["id"] and last["swipe_count"] == 2
    assert sent(fake_llm)[-1].startswith("[Write the next reply only as Bob.")


async def test_manual_order_waits_for_the_pick(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "manual"})
    saved = await client.post(f"/api/chats/{chat_id}/send", json={"text": "who's there?"})
    assert saved.status_code == 201 and saved.json()["run_id"] is None
    assert saved.json()["message"]["content"] == "who's there?" and fake_llm.requests == []

    fake_llm.queue("Me, Bob.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/continue", json={"speaker": bob["id"]}))
    last = (await messages(client, chat_id))[-1]
    assert (last["character_id"], last["content"]) == (bob["id"], "Me, Bob.")
    me_too = await client.post(f"/api/chats/{chat_id}/continue", json={"speaker": "nobody"})
    assert me_too.json()["error"]["code"] == "bad_speaker"


async def test_the_model_can_choose_who_speaks(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "model"})
    fake_llm.queue('{"name": "Bob"}', "Bob speaks.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hmm"}))
    last = (await messages(client, chat_id))[-1]
    assert (last["name"], last["character_id"], last["content"]) == ("Bob", bob["id"], "Bob speaks.")
    asked = fake_llm.requests[-2]
    assert asked["response_format"]["json_schema"]["schema"]["properties"]["name"]["enum"] == ["Ann", "Bob"]
    purposes = [r[0] for r in ctx.db.fetchall("SELECT purpose FROM spending ORDER BY at")]
    assert purposes == ["speaker", "chat"]
    assert last["swipe_meta"]["usage"]["requests"] == 2

    # A useless answer is not fatal: the natural order picks someone.
    fake_llm.queue('{"name": "Zed"}', '{"name": "Zed"}', "Someone speaks.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hmm"}))
    assert (await messages(client, chat_id))[-1]["character_id"] in (ann["id"], bob["id"])


async def test_muted_members_stay_silent(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={"others": "full"})
    await client.put(f"/api/chats/{chat_id}/members/{ann['id']}", json={"muted": True, "talkativeness": 100})
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "Ann?"}))
    assert (await messages(client, chat_id))[-1]["character_id"] == bob["id"]
    assert "Ann is kind." not in "\n".join(sent(fake_llm))  # out of the prompt, even with full cards

    await client.put(f"/api/chats/{chat_id}/members/{bob['id']}", json={"muted": True})
    before = len(await messages(client, chat_id))
    silent = await client.post(f"/api/chats/{chat_id}/send", json={"text": "anyone?"})
    assert silent.status_code == 409 and silent.json()["error"]["code"] == "nobody_to_answer"
    assert len(await messages(client, chat_id)) == before  # nothing half-written
    bad = await client.put(f"/api/chats/{chat_id}/members/{bob['id']}", json={"talkativeness": 101})
    assert bad.json()["error"]["code"] == "bad_member"


async def test_code_can_bring_a_character_in_and_let_it_speak(client, ctx, fake_llm):
    """What an agent will do (stage 8): add a character to the scene by itself, then hand it the turn."""
    from tavern.services import cast, chat as chat_service

    ann = await make_character(client, "Ann", description="Ann is kind.", first_mes="Hello")
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    guard = await make_character(client, "Guard", description="A city guard.")
    seen = []
    ctx.events.on("chat.members_changed", lambda **kw: seen.append([m["name"] for m in kw["members"]]))

    await cast.add_member(ctx, chat_id, guard["id"], talkativeness=80, blurb="stops strangers")
    assert seen == [["Ann", "Guard"]]
    fake_llm.queue("Halt!")
    run = chat_service.start_continue(ctx, chat_id, speaker=guard["id"])
    await asyncio.wait([run.task], timeout=10)
    last = (await messages(client, chat_id))[-1]
    assert (last["name"], last["content"]) == ("Guard", "Halt!")
    assert "A city guard." in "\n".join(sent(fake_llm)) and "Ann: Hello" in sent(fake_llm)


def test_upgrade_finds_who_wrote_old_replies(tmp_path):
    db = Database(tmp_path / "old.sqlite3")
    db.migrate([m for m in load_migrations() if m.version <= 12])
    db.execute("INSERT INTO characters (id, name, data) VALUES ('a', 'Ann', '{}'), ('b', 'Bob', '{}')")
    db.execute("INSERT INTO chats (id, character_id, title) VALUES ('c', 'a', 'old')")
    db.execute("INSERT INTO chat_members (chat_id, character_id, position) VALUES ('c', 'a', 0), ('c', 'b', 1)")
    db.execute("INSERT INTO messages (id, chat_id, depth, role, name) VALUES "
               "('1', 'c', 0, 'assistant', 'Bob'), ('2', 'c', 1, 'user', 'Bob'), ('3', 'c', 2, 'assistant', 'Zed')")
    db.migrate()
    rows = {r["id"]: r["character_id"] for r in db.fetchall("SELECT id, character_id FROM messages")}
    assert rows == {"1": "b", "2": None, "3": None}
    member = db.fetchone("SELECT talkativeness, muted, blurb FROM chat_members WHERE character_id = 'a'")
    assert tuple(member) == (50, 0, "")


# --- stage 7б: several replies in one turn ------------------------------------------------------


async def finish_turn(ctx, response):
    assert response.status_code == 202, response.text
    run = ctx.runs.get(response.json()["run_id"])
    await asyncio.wait([run.task], timeout=10)
    return run.events


async def test_they_talk_among_themselves_up_to_the_limit(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "turns"})
    fake_llm.queue("b1", "a1", "b2")
    events = await finish_turn(ctx, await client.post(f"/api/chats/{chat_id}/talk", json={"count": 3}))
    shown = [(m["name"], m["content"]) for m in await messages(client, chat_id)]
    assert shown == [("Ann", "Hello"), ("Bob", "b1"), ("Ann", "a1"), ("Bob", "b2")]
    kinds = [e["type"] for e in events if e["type"] != "delta"]
    assert kinds[0] == "turn_start" and kinds.count("reply_done") == 3 and kinds[-1] == "done"
    assert events[-1]["reload"] is True
    assert "Ann: a1" in sent(fake_llm)  # Bob, speaking last, sees Ann's line with her name

    await client.put(f"/api/chats/{chat_id}/speakers", json={"talk_limit": 2})
    await finish_turn(ctx, await client.post(f"/api/chats/{chat_id}/talk"))
    assert len(await messages(client, chat_id)) == 6
    bad = await client.post(f"/api/chats/{chat_id}/talk", json={"count": 99})
    assert bad.json()["error"]["code"] == "bad_count"


async def test_everyone_answers_at_once_without_seeing_each_other(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "all", "together": "list"})
    events = await finish_turn(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "everyone, hi"}))
    shown = [(m["name"], m["role"]) for m in await messages(client, chat_id)]
    assert shown == [("Ann", "assistant"), ("User", "user"), ("Ann", "assistant"), ("Bob", "assistant")]
    for request in fake_llm.requests:  # each saw the chat up to the round, not the other's reply
        assert request["messages"][-2]["content"] == "everyone, hi"
    assert [e["type"] for e in events].count("start") == 2
    path = await messages(client, chat_id)
    assert [m["depth"] for m in path] == [0, 1, 2, 3]  # one chain, nothing left hanging

    # "Продолжить" also starts a round; an empty reply is dropped and the chain stays whole.
    fake_llm.queue("", "only one")
    await finish_turn(ctx, await client.post(f"/api/chats/{chat_id}/continue"))
    path = await messages(client, chat_id)
    assert path[-1]["content"] == "only one" and [m["depth"] for m in path] == [0, 1, 2, 3, 4]


async def test_a_round_can_be_one_message_and_the_narrator_sums_it_up(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={
        "order": "all", "together": "one", "narrator": {"enabled": True, "name": "Голос"}})
    fake_llm.queue("I heal.", "I forge.", "Both worked at once.")
    await finish_turn(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "go"}))
    *_, joined, scene = await messages(client, chat_id)
    assert joined["name"] == "Ann, Bob" and joined["character_id"] is None and joined["extra"]["together"]
    assert set(joined["content"].split("\n\n")) == {"Ann: I heal.", "Bob: I forge."}
    assert joined["swipe_meta"]["usage"]["requests"] == 2
    assert (scene["role"], scene["name"], scene["content"]) == ("system", "Голос", "Both worked at once.")
    asked = fake_llm.requests[-1]["messages"]
    assert asked[0]["content"].startswith("You are the narrator") and "This round, all at the same time" in asked[1]["content"]
    purposes = sorted(r[0] for r in ctx.db.fetchall("SELECT purpose FROM spending"))
    assert purposes == ["chat", "chat", "narrator"]

    # The joined message already names everyone: no "Ann, Bob:" in front of it next time.
    await finish_turn(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "next", "speaker": bob["id"]}))
    assert not any(c.startswith("Ann, Bob:") for c in sent(fake_llm))


async def test_stop_in_the_middle_of_a_round_leaves_a_whole_chain(client, ctx, fake_llm):
    ann, bob, chat_id = await two_characters(client)
    await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "all"})
    fake_llm.chunk_delay = 0.05
    fake_llm.queue("word " * 40, "word " * 40)
    started = await client.post(f"/api/chats/{chat_id}/send", json={"text": "go"})
    run = ctx.runs.get(started.json()["run_id"])
    await asyncio.sleep(0.1)
    assert (await client.post(f"/api/chats/{chat_id}/stop")).json()["stopped"] is True
    await asyncio.wait([run.task], timeout=10)
    assert run.events[-1]["type"] == "stopped" and run.events[-1]["reload"] is True
    path = await messages(client, chat_id)
    assert [m["depth"] for m in path] == list(range(len(path)))
    assert path[1]["content"] == "go" and all(m["content"].strip() for m in path)
    chat = (await client.get(f"/api/chats/{chat_id}")).json()["chat"]
    assert chat["leaf_id"] == path[-1]["id"]
