"""Memory++ M1 through the HTTP API on the fake model: chat vectors (which messages, when, in the background), the
reply's ONE meaning request shared with the plugin, the live tail and found pieces in the prompt, per-chat and
global switches, nothing happens when off, export and undo carry no vectors, the plugin API points it uses."""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from tavern.errors import AppError
from tavern.plugins.manager import PluginRefused
from tavern.providers.base import ProviderError
from tavern.services import embeddings
from tavern.storage import snapshot
from tavern.storage.changelog import Recorder

MODULE = "tavern_plugin_memory"  # how the plugin manager names the plugin's server module


def memory_module():
    return sys.modules[MODULE]


async def boot(client, ctx, *, window: int = 0, plugin: bool = True, settings: dict | None = None) -> dict:
    conn = (await client.post("/api/connections", json={
        "name": "f", "base_url": "http://llm/v1", "model": "m", "params": {"max_context_tokens": window}})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"], "embedding_connection": conn["id"]})
    if plugin:
        assert (await client.post("/api/plugins/memory/enable", json={})).status_code == 200
        if settings:
            assert (await client.put("/api/plugins/memory/settings", json=settings)).status_code == 200
        memory_module().WAKE_DELAY = 0.01
    ann = (await client.post("/api/characters", json={"name": "Ann"})).json()
    bob = (await client.post("/api/characters", json={"name": "Bob"})).json()
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    return {"conn": conn, "ann": ann, "bob": bob, "chat": chat}


def say(ctx, chat_id: str, role: str, text: str, **kw) -> dict:
    return ctx.chats.add_message(chat_id, role=role, content=text, parent_id=ctx.chats.get_chat(chat_id)["leaf_id"], **kw)


async def switch(client, chat_id: str, enabled: bool = True) -> dict:
    r = await client.post("/api/plugins/memory/actions/set_enabled", json={"chat_id": chat_id, "enabled": enabled})
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def state_of(client, chat_id: str) -> dict:
    r = await client.post("/api/plugins/memory/actions/state", json={"chat_id": chat_id})
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def idle(ctx, chat_id: str) -> None:
    await asyncio.sleep(0.03)  # events from sync code are scheduled on the loop: let them wake the job first
    for _ in range(600):
        status = ctx.jobs.status("memory", f"chat:{chat_id}")
        if not status["running"] and not status["queued"]:
            await asyncio.sleep(0.02)
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the job did not finish: {ctx.jobs.status('memory', f'chat:{chat_id}')}")


def vectorized(ctx, chat_id: str) -> set[str]:
    rows = ctx.db.fetchall(
        "SELECT p.first_id FROM p_memory_pieces p JOIN p_memory_vectors v ON v.item_id = p.id "
        "AND v.text_hash = p.text_hash WHERE p.chat_id = ?", (chat_id,))
    return {r["first_id"] for r in rows}


def objects(ctx) -> list[str]:
    return [r["name"] for r in ctx.db.fetchall("SELECT name FROM sqlite_master WHERE name LIKE 'p\\_memory\\_%' ESCAPE '\\'")]


async def send(client, ctx, chat_id: str, text: str) -> list[dict]:
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    body = (await client.get(f"/api/runs/{run_id}/events")).text
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


# -- off means off ---------------------------------------------------------------------------------


async def test_a_plugin_that_is_not_switched_on_touches_nothing(client, ctx, fake_llm):
    setup = await boot(client, ctx, plugin=False)
    chat_id = setup["chat"]["id"]
    for i in range(4):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"сообщение {i}")
    await send(client, ctx, chat_id, "ещё")
    assert objects(ctx) == []  # no table was even made: migrations apply when the plugin is switched on
    assert fake_llm.embedding_requests == []
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["queued"] is False


async def test_memory_off_in_the_chat_does_nothing_even_with_the_plugin_on(client, ctx, fake_llm):
    setup = await boot(client, ctx, window=400)
    chat_id = setup["chat"]["id"]
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"сообщение {i} " + "слово " * 40)
    events = await send(client, ctx, chat_id, "ещё")
    assert events[-1]["type"] == "done"
    await asyncio.sleep(0.1)
    assert fake_llm.embedding_requests == []  # no vectors, no meaning request
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_pieces")["n"] == 0
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_vectors")["n"] == 0
    assert "Ранее в истории" not in json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)


async def test_switching_the_plugin_off_removes_its_handlers_and_stops_its_jobs(client, ctx, fake_llm, monkeypatch):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    hold = asyncio.Event()
    started = []

    class Slow:
        async def embed(self, texts, *, model):
            started.append(1)
            await hold.wait()
            return [[1.0, 0.0] for _ in texts]

        async def aclose(self):
            pass

    monkeypatch.setattr(ctx.providers, "create", lambda connection: Slow())
    for i in range(4):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"сообщение {i}")
    await switch(client, chat_id)
    for _ in range(300):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started and ctx.jobs.status("memory", f"chat:{chat_id}")["running"] is True
    assert (await client.post("/api/plugins/memory/disable")).status_code == 200
    await asyncio.sleep(0.05)
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["running"] is False
    assert ctx.events.handlers("message.added") == [] and ctx.events.handlers("chat.before_generate") == []
    assert ctx.events.handlers("meaning.wanted") == []
    say(ctx, chat_id, "user", "после выключения")
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["queued"] is False


# -- which messages are vectorized -----------------------------------------------------------------


async def test_every_message_but_each_speakers_latest_is_vectorized_and_only_its_text_goes_out(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id, ann, bob = setup["chat"]["id"], setup["ann"]["id"], setup["bob"]["id"]
    m = [
        say(ctx, chat_id, "user", "первое от меня"),
        say(ctx, chat_id, "assistant", "Энн говорит раз", character_id=ann, name="Ann"),
        say(ctx, chat_id, "assistant", "Боб говорит раз", character_id=bob, name="Bob"),
        say(ctx, chat_id, "user", "второе от меня"),
        say(ctx, chat_id, "assistant", "скрытое", character_id=ann, name="Ann", hidden=True),
        say(ctx, chat_id, "assistant", "спрятано от модели", character_id=ann, name="Ann", exclude_from_prompt=True),
        say(ctx, chat_id, "system", "заметка системы"),
        say(ctx, chat_id, "assistant", "бросок кубиков", owner="dice"),
        say(ctx, chat_id, "assistant", "Энн говорит два", character_id=ann, name="Ann"),
        say(ctx, chat_id, "user", "третье от меня"),
    ]
    ctx.chats.update_swipe_meta(m[1]["id"], 0, {"reasoning": "ТАЙНЫЕ МЫСЛИ ЭНН"})
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    ids = {x["content"]: x["id"] for x in m}
    # the latest of each speaker stays out: my "третье", Ann's "два", and Bob's only message
    assert vectorized(ctx, chat_id) == {ids["первое от меня"], ids["Энн говорит раз"], ids["второе от меня"]}
    sent = [text for body in fake_llm.embedding_requests for text in body["input"]]
    assert sorted(sent) == sorted(["первое от меня", "Энн говорит раз", "второе от меня"])
    assert not any("ТАЙНЫЕ" in text or "Ann" in text for text in sent)  # no thinking, no names, no prompts
    pieces = {r["first_id"] for r in ctx.db.fetchall("SELECT first_id FROM p_memory_pieces")}
    # hidden, hidden from the model, system and plugin messages never become pieces; the latest ones do (keywords)
    assert pieces == {ids[t] for t in ("первое от меня", "Энн говорит раз", "Боб говорит раз", "второе от меня",
                                       "Энн говорит два", "третье от меня")}


async def test_a_message_that_stops_being_the_latest_gets_its_vector_in_the_background(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    first = say(ctx, chat_id, "user", "первое")
    say(ctx, chat_id, "assistant", "ответ")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert vectorized(ctx, chat_id) == set()  # the only messages are each speaker's latest
    second = say(ctx, chat_id, "user", "второе")
    await client.post(f"/api/chats/{chat_id}/messages", json={"text": "третье", "role": "assistant"})
    await idle(ctx, chat_id)
    assert first["id"] in vectorized(ctx, chat_id)  # no longer the user's latest
    assert second["id"] not in vectorized(ctx, chat_id)  # now the user's latest


async def test_an_edit_makes_the_vector_stale_and_it_is_redone_in_the_background(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    a = say(ctx, chat_id, "user", "яблоки у торговца")
    say(ctx, chat_id, "assistant", "ответ раз")
    say(ctx, chat_id, "user", "и ещё сообщение")
    say(ctx, chat_id, "assistant", "ответ два")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert a["id"] in vectorized(ctx, chat_id)
    old_hash = ctx.db.fetchone("SELECT text_hash FROM p_memory_pieces WHERE first_id = ?", (a["id"],))["text_hash"]
    fake_llm.embedding_requests.clear()
    r = await client.put(f"/api/messages/{a['id']}", json={"content": "груши у торговца"})
    assert r.status_code == 200
    stale = ctx.db.fetchone("SELECT p.text_hash AS h, v.text_hash AS vh FROM p_memory_pieces p "
                            "JOIN p_memory_vectors v ON v.item_id = p.id WHERE p.first_id = ?", (a["id"],))
    assert stale["vh"] == old_hash  # the vector is still of the old text: stale until redone
    await idle(ctx, chat_id)
    assert [body["input"] for body in fake_llm.embedding_requests] == [["груши у торговца"]]
    assert a["id"] in vectorized(ctx, chat_id)
    assert ctx.db.fetchone("SELECT text_hash FROM p_memory_pieces WHERE first_id = ?", (a["id"],))["text_hash"] != old_hash


async def test_hiding_a_message_removes_its_pieces_and_deleting_it_removes_them_with_the_vectors(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    ids = [say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i} дело{i}")["id"] for i in range(6)]
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert ids[0] in vectorized(ctx, chat_id)
    await client.put(f"/api/messages/{ids[0]}", json={"exclude_from_prompt": True})
    await idle(ctx, chat_id)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_pieces WHERE first_id = ?", (ids[0],))["n"] == 0
    await client.delete(f"/api/messages/{ids[1]}")
    await idle(ctx, chat_id)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_pieces WHERE first_id = ?", (ids[1],))["n"] == 0
    orphans = ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_vectors v WHERE NOT EXISTS "
                              "(SELECT 1 FROM p_memory_pieces p WHERE p.id = v.item_id)")["n"]
    assert orphans == 0


async def test_deleting_the_chat_takes_pieces_and_vectors_along(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i}")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_vectors")["n"] > 0
    await client.delete(f"/api/chats/{chat_id}")
    for table in ("p_memory_pieces", "p_memory_vectors", "p_memory_chats"):
        assert ctx.db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


# -- the reply -------------------------------------------------------------------------------------


async def test_the_reply_makes_one_embedding_request_shared_with_the_plugin(client, ctx, fake_llm):
    """No lorebooks, no bubbles: today that means no embedding request at all. With memory on there is exactly one
    (the meaning request), and the plugin uses its query vectors: a piece is found by MEANING alone (the stems of
    "королева" and "королевство" differ, the first five letters, which the fake model hashes, do not)."""
    setup = await boot(client, ctx, window=400, settings={"tail_percent": 10, "tail_min": 2, "memory_tokens": 300})
    chat_id = setup["chat"]["id"]
    old = say(ctx, chat_id, "user", "Королева.")
    for i in range(11):
        say(ctx, chat_id, "assistant" if i % 2 == 0 else "user", f"сообщение {i} " + "слово " * 40)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert old["id"] in vectorized(ctx, chat_id)
    memory_module().WAKE_DELAY = 60  # the job must not run beside the reply under test
    fake_llm.embedding_requests.clear()
    events = await send(client, ctx, chat_id, "королевство")
    assert events[-1]["type"] == "done"
    assert len(fake_llm.embedding_requests) == 1
    assert fake_llm.embedding_requests[0]["input"][-1] == "королевство"
    prompt = json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)
    assert "Ранее в истории (отрывок)" in prompt and "Королева." in prompt
    saw = (await client.get(f"/api/messages/{events[-1]['message']['id']}/prompt")).json()
    sources = [e["source"] for e in saw["entries"]]
    assert f"plugin:memory:piece:{ctx.db.fetchone('SELECT id FROM p_memory_pieces WHERE first_id = ?', (old['id'],))['id']}" in sources
    memory_entry = next(e for e in saw["entries"] if e["kind"] == "memory")
    assert memory_entry["label"].startswith("Ранее в истории (отрывок)")


async def test_keywords_find_a_name_and_the_whole_history_goes_when_it_fits(client, ctx, fake_llm):
    setup = await boot(client, ctx, window=0)
    chat_id = setup["chat"]["id"]
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"Вчера Мария купила {i} яблок")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    events = await send(client, ctx, chat_id, "что с Марией")
    assert events[-1]["type"] == "done"
    prompt = json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)
    assert "Ранее в истории" not in prompt  # no window known: the whole history goes, as always
    assert prompt.count("Вчера Мария купила") == 6


async def test_a_found_piece_goes_only_when_its_message_was_left_out_of_the_window(client, ctx, fake_llm):
    setup = await boot(client, ctx, window=100_000, settings={"tail_percent": 10, "tail_min": 2})
    chat_id = setup["chat"]["id"]
    for i in range(8):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"Вчера Мария купила {i} яблок")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    events = await send(client, ctx, chat_id, "что с Марией")
    assert events[-1]["type"] == "done"
    prompt = json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)
    assert "Ранее в истории" not in prompt and prompt.count("Вчера Мария купила") == 8  # all fit: nothing twice


async def test_the_live_tail_is_the_newest_messages_and_is_never_cut_for_memory(client, ctx, fake_llm):
    setup = await boot(client, ctx, window=520, settings={"tail_percent": 30, "tail_min": 4, "memory_tokens": 200})
    chat_id = setup["chat"]["id"]
    for i in range(14):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"номер{i} " + "слово " * 24)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    memory_module().WAKE_DELAY = 60
    events = await send(client, ctx, chat_id, "конец")
    assert events[-1]["type"] == "done"
    prompt = json.dumps(fake_llm.requests[-1]["messages"], ensure_ascii=False)
    assert "номер13" in prompt and "номер12" in prompt and "номер11" in prompt  # the newest are word for word


async def test_a_failing_embedding_service_shows_the_usual_notice_and_the_reply_goes_on(client, ctx, fake_llm):
    setup = await boot(client, ctx, window=400)
    chat_id = setup["chat"]["id"]
    for i in range(8):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"сообщение {i} " + "слово " * 30)
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    memory_module().WAKE_DELAY = 60
    fake_llm.failures.append((500, "сломалось"))
    events = await send(client, ctx, chat_id, "дальше")
    assert any(e["type"] == "meaning_failed" for e in events)
    assert events[-1]["type"] == "done"


# -- the background job gives way ------------------------------------------------------------------


class Gated:
    def __init__(self):
        self.started = self.cancelled = self.finished = 0
        self.hold: asyncio.Event | None = None
        self.limit_first = 0
        self.inputs: list[list[str]] = []

    async def embed(self, texts, *, model):
        self.started += 1
        if self.limit_first > 0:
            self.limit_first -= 1
            raise ProviderError("rate", reason="limit", upstream_status=429)
        try:
            if self.hold is not None:
                await self.hold.wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.finished += 1
        self.inputs.append(list(texts))
        return [[1.0, 0.0, 0.5] for _ in texts]

    async def aclose(self):
        pass


async def wait_for(cond, tries: int = 300) -> None:
    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


async def test_a_reply_cancels_the_vectorizing_step_at_once_and_it_resumes_afterwards(client, ctx, monkeypatch):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    provider = Gated()
    provider.hold = asyncio.Event()
    monkeypatch.setattr(ctx.providers, "create", lambda connection: provider)
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i} дело{i}")
    await switch(client, chat_id)
    await wait_for(lambda: provider.started == 1)
    ctx.foreground.enter()  # a reply (or an assistant request) starts: RunManager does exactly this
    await wait_for(lambda: provider.cancelled == 1)
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["paused"] is True
    await asyncio.sleep(0.1)
    assert provider.started == 1  # nothing asked while the reply runs
    provider.hold.set()
    ctx.foreground.leave()
    await idle(ctx, chat_id)
    assert provider.started == 2 and provider.finished == 1  # the same step, made again
    assert len(vectorized(ctx, chat_id)) == 4  # all but the two speakers' latest messages
    assert sum(len(texts) for texts in provider.inputs) == 4


async def test_a_rate_limit_is_waited_out_and_the_job_goes_on(client, ctx, monkeypatch):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    provider = Gated()
    provider.limit_first = 2
    monkeypatch.setattr(ctx.providers, "create", lambda connection: provider)
    monkeypatch.setattr(embeddings, "RATE_WAIT", 0.01)
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i}")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert provider.started == 3 and len(vectorized(ctx, chat_id)) == 4
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["error"] == ""


async def test_without_an_embedding_connection_nothing_is_vectorized_and_the_job_says_so(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    await client.put("/api/settings", json={"embedding_connection": ""})
    for i in range(4):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i}")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert fake_llm.embedding_requests == []
    state = await state_of(client, chat_id)
    assert state["enabled"] is True and state["connected"] is False and state["pieces"] == 4 and state["vectors"] == 0


# -- switches --------------------------------------------------------------------------------------


async def test_new_chats_follow_the_global_switch_and_old_ones_do_not(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    before = setup["chat"]["id"]
    await asyncio.sleep(0.05)  # the event of the first chat is heard while the switch is still off
    await client.put("/api/plugins/memory/settings", json={"new_chats": True})
    after = (await client.post("/api/chats", json={})).json()["chat"]["id"]
    await asyncio.sleep(0.05)
    states = {c: (await state_of(client, c))["enabled"] for c in (before, after)}
    assert states == {before: False, after: True}


async def test_the_default_is_off_everywhere(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    state = await state_of(client, chat_id)
    assert state["enabled"] is False and state["new_chats"] is False
    plugin = (await client.get("/api/plugins/memory")).json()
    assert plugin["values"]["new_chats"] is False and plugin["builtin"] is True


async def test_switching_memory_off_in_a_chat_stops_its_job(client, ctx, monkeypatch):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    provider = Gated()
    provider.hold = asyncio.Event()
    monkeypatch.setattr(ctx.providers, "create", lambda connection: provider)
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i}")
    await switch(client, chat_id)
    await wait_for(lambda: provider.started == 1)
    state = await switch(client, chat_id, False)
    assert state["enabled"] is False
    await wait_for(lambda: provider.cancelled == 1)
    assert ctx.jobs.status("memory", f"chat:{chat_id}")["running"] is False


# -- vectors stay with the chat --------------------------------------------------------------------


async def test_the_export_file_has_no_vectors_and_no_memory_records(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    for i in range(6):
        say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i} дело{i}")
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_vectors")["n"] > 0
    text = (await client.get(f"/api/chats/{chat_id}/export.jsonl")).text
    piece_ids = [r["id"] for r in ctx.db.fetchall("SELECT id FROM p_memory_pieces")]
    assert piece_ids and not any(pid in text for pid in piece_ids)
    assert "vector" not in text.lower() and "p_memory" not in text and "memory" not in text.lower()
    kinds = {json.loads(line).get("type") for line in text.splitlines() if line.strip()}
    assert "plugin" not in kinds


async def test_vectors_never_enter_the_assistants_undo_journal(client, ctx, fake_llm):
    setup = await boot(client, ctx)
    chat_id = setup["chat"]["id"]
    ids = [say(ctx, chat_id, "user" if i % 2 == 0 else "assistant", f"слово{i} дело{i}")["id"] for i in range(6)]
    await switch(client, chat_id)
    await idle(ctx, chat_id)
    from tavern.storage import changelog

    watched = changelog._tables(ctx.db)
    assert "p_memory_pieces" in watched and "p_memory_chats" in watched and "p_memory_vectors" not in watched
    with Recorder(ctx.db) as rec:
        await client.delete(f"/api/messages/{ids[1]}")
    undo = rec.snapshot()
    dumped = json.dumps(undo)
    assert "p_memory_vectors" not in dumped and "vector" not in dumped
    assert "p_memory_pieces" in dumped  # the pieces cascade away with the message and come back on undo
    snapshot.restore(ctx.db, undo)
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_memory_pieces WHERE first_id = ?", (ids[1],))["n"] == 1
    assert ids[1] not in vectorized(ctx, chat_id)  # the vector is made again by the job, not restored
    await client.post("/api/plugins/memory/actions/vectorize", json={"chat_id": chat_id})
    await idle(ctx, chat_id)
    assert ids[1] in vectorized(ctx, chat_id)


# -- the plugin API points -------------------------------------------------------------------------


async def test_api_embed_is_metered_as_the_plugin_and_needs_a_connection(client, ctx, fake_llm):
    await boot(client, ctx)
    api = ctx.plugins.loaded["memory"].api
    vectors = await api.embed(["раз", "два"])
    assert len(vectors) == 2 and len(vectors[0]) == 64
    assert api.vectors_model() == "m"
    rows = ctx.db.fetchall("SELECT purpose FROM spending")
    assert [r["purpose"] for r in rows] == ["plugin:memory"]
    await client.put("/api/settings", json={"embedding_connection": ""})
    assert api.vectors_model() == ""
    with pytest.raises(AppError) as error:
        await api.embed(["раз"])
    assert error.value.code == "no_embedding_connection"


async def test_api_embed_obeys_the_plugins_money_limit_but_not_the_models_permission(client, ctx, fake_llm, monkeypatch):
    await boot(client, ctx)
    api = ctx.plugins.loaded["memory"].api
    assert (await client.get("/api/plugins/memory")).json()["access"]["models"] is False
    await api.embed(["раз"])  # permission to use chat models is not needed for vectors
    await client.put("/api/plugins/memory/access", json={"day": 1.0})
    monkeypatch.setattr(ctx.spending, "purpose", lambda *a, **k: {"cost": 5.0})
    with pytest.raises(PluginRefused) as error:
        await api.embed(["два"])
    assert error.value.code == "plugin_limit"


async def test_api_job_runs_once_per_key_and_reports_status(client, ctx):
    await boot(client, ctx)
    api = ctx.plugins.loaded["memory"].api
    runs = []

    async def work(job):
        runs.append(1)
        job.progress(1, 2, "половина")

    api.job("demo", work, delay=0)
    api.job("demo", work, delay=0)
    await wait_for(lambda: runs)
    await asyncio.sleep(0.05)
    assert api.job_status("demo")["done"] == 1 and api.job_status("demo")["note"] == "половина"
    assert api.job_status("other")["running"] is False
