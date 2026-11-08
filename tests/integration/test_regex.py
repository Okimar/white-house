"""Regex rules over HTTP (stage 11, ADR 0017): scopes and order, depth, the three places (screen, model, saved
text), the worker's time limit, import / export, approval, copies and deletion, the doctor, the migration."""

from __future__ import annotations

import asyncio
import time

import pytest

from tavern.core import regex_runner
from tavern.core.regex import build_spec
from tavern.services import regex as regex_service
from tavern.storage.db import Database
from tavern.storage.migrations import load_migrations


@pytest.fixture(autouse=True)
def _fresh_regex_state():
    regex_service._cache.clear()
    regex_service._slow.clear()
    yield
    regex_service._cache.clear()
    regex_service._slow.clear()


async def finish(ctx, response):
    assert response.status_code == 202, response.text
    run = ctx.runs.get(response.json()["run_id"])
    await asyncio.wait([run.task], timeout=10)


async def setup(client, **card):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind.",
                                                      "first_mes": "Hello", **card})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return ann, chat["id"]


async def add(client, find, replace="", scope="global", owner="", **fields):
    body = {"scope": scope, "owner": owner, "name": fields.pop("name", find[:20]), "find": find, "replace": replace,
            **fields}
    response = await client.post("/api/regex/rules", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def display(client, chat_id, text, role="assistant", depth=0, stream=False):
    response = await client.post("/api/regex/display", json={
        "chat_id": chat_id, "items": [{"id": "x", "text": text, "role": role, "depth": depth, "stream": stream}]})
    assert response.status_code == 200, response.text
    return response.json()["results"]["x"]


def words(nodes):
    out = []
    for n in nodes:
        out.extend(words(n["c"]) if isinstance(n, dict) else [n])
    return out


# --- rules: lists, scopes, order --------------------------------------------------------------


async def test_a_rule_is_created_listed_changed_copied_and_deleted(client):
    rule = await add(client, r"/\*\*(.+?)\*\*/g", "<b>$1</b>", name="Жирный", places=["assistant"])
    assert rule["approved"] and rule["when"] == "display" and rule["error"] == ""
    listed = (await client.get("/api/regex/rules?scope=global")).json()["rules"]
    assert [r["id"] for r in listed] == [rule["id"]]

    changed = (await client.put(f"/api/regex/rules/{rule['id']}", json={"name": "Bold", "enabled": False})).json()
    assert changed["name"] == "Bold" and changed["enabled"] is False and changed["find"] == rule["find"]

    copy = (await client.post(f"/api/regex/rules/{rule['id']}/copy")).json()
    assert copy["name"] == "Bold (копия)" and copy["id"] != rule["id"] and copy["position"] > rule["position"]

    assert (await client.delete(f"/api/regex/rules/{rule['id']}")).status_code == 200
    assert (await client.get(f"/api/regex/rules/{rule['id']}")).status_code == 404


async def test_a_bad_pattern_is_refused_with_the_reason(client):
    response = await client.post("/api/regex/rules", json={"scope": "global", "find": "/(a/", "name": "x"})
    assert response.status_code == 400
    assert "Не закрыта" in response.json()["error"]["message"]
    bad = await client.post("/api/regex/rules", json={"scope": "global", "find": "/a/", "when": "sometimes"})
    assert bad.status_code == 400
    depth = await client.post("/api/regex/rules", json={"scope": "global", "find": "/a/", "min_depth": 5, "max_depth": 2})
    assert depth.status_code == 400


async def test_order_can_be_changed_and_must_list_every_rule(client):
    a, b, c = [await add(client, f"/{x}/") for x in "abc"]
    reordered = (await client.put("/api/regex/order", json={"scope": "global", "ids": [c["id"], a["id"], b["id"]]})).json()
    assert [r["find"] for r in reordered["rules"]] == ["/c/", "/a/", "/b/"]
    short = await client.put("/api/regex/order", json={"scope": "global", "ids": [a["id"]]})
    assert short.status_code == 400


async def test_rules_run_in_order_global_then_preset_then_character(client):
    ann, chat_id = await setup(client)
    preset = (await client.post("/api/presets", json={"name": "P", "from": "default"})).json()
    await client.put(f"/api/chats/{chat_id}/meta", json={"preset_id": preset["id"]})
    await add(client, "/a/", "b", scope="character", owner=ann["id"])      # a -> b, last
    await add(client, "/b/", "c", scope="preset", owner=preset["id"])       # b -> c, second
    await add(client, "/x/", "a")                                            # x -> a, first
    # x -> a (global); the preset's b -> c finds no b yet; the character's a -> b
    assert (await display(client, chat_id, "x"))["text"] == "b"
    assert (await display(client, chat_id, "b"))["text"] == "c"             # preset b -> c, then nothing left to do
    assert (await display(client, chat_id, "a"))["text"] == "b"


async def test_scope_belongs_to_its_owner_only(client):
    ann, chat_id = await setup(client)
    other = (await client.post("/api/characters", json={"name": "Zed"})).json()
    await add(client, "/hello/", "HI", scope="character", owner=other["id"])
    assert (await display(client, chat_id, "hello"))["text"] == "hello"
    await add(client, "/hello/", "HI", scope="character", owner=ann["id"])
    assert (await display(client, chat_id, "hello"))["text"] == "HI"
    nobody = await client.post("/api/regex/rules", json={"scope": "character", "owner": "nope", "find": "/a/"})
    assert nobody.status_code == 404
    builtin = await client.post("/api/regex/rules", json={"scope": "preset", "owner": "default", "find": "/a/"})
    assert builtin.status_code == 400


async def test_deleting_a_character_or_preset_takes_its_rules(client, ctx):
    ann, _ = await setup(client)
    preset = (await client.post("/api/presets", json={"name": "P", "from": "default"})).json()
    await add(client, "/a/", scope="character", owner=ann["id"])
    await add(client, "/b/", scope="preset", owner=preset["id"])
    keep = await add(client, "/c/")
    assert ctx.db.fetchone("SELECT COUNT(*) FROM regex_scripts")[0] == 3
    await client.delete(f"/api/characters/{ann['id']}")
    await client.delete(f"/api/presets/{preset['id']}")
    left = ctx.db.fetchall("SELECT id FROM regex_scripts")
    assert [r["id"] for r in left] == [keep["id"]]


# --- the screen -------------------------------------------------------------------------------


async def test_screen_rules_change_only_what_is_shown(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await add(client, "/secret/g", "***", when="display")
    fake_llm.queue("the secret is out")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "tell me the secret"}))
    saved = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    assert saved[-1]["content"] == "the secret is out" and saved[-2]["content"] == "tell me the secret"
    assert fake_llm.requests[-1]["messages"][-1]["content"] == "tell me the secret"   # the model got it as written
    assert (await display(client, chat_id, "the secret is out"))["text"] == "the *** is out"
    assert (await display(client, chat_id, "the secret", role="user"))["text"] == "the ***"


async def test_places_user_and_model_are_separate(client):
    _, chat_id = await setup(client)
    await add(client, "/hi/", "yo", places=["assistant"])
    assert (await display(client, chat_id, "hi", role="assistant"))["text"] == "yo"
    assert (await display(client, chat_id, "hi", role="user"))["text"] == "hi"
    assert (await display(client, chat_id, "hi", role="system"))["text"] == "hi"


async def test_depth_counts_messages_from_the_end(client):
    _, chat_id = await setup(client)
    await add(client, "/a/", "A", min_depth=1, max_depth=2)
    got = [(await display(client, chat_id, "a", depth=d))["text"] for d in range(4)]
    assert got == ["a", "A", "A", "a"]


async def test_a_rule_that_is_off_or_not_allowed_does_nothing(client, ctx):
    _, chat_id = await setup(client)
    rule = await add(client, "/a/", "B", enabled=False)
    assert (await display(client, chat_id, "a"))["text"] == "a"
    await client.put(f"/api/regex/rules/{rule['id']}", json={"enabled": True})
    assert (await display(client, chat_id, "a"))["text"] == "B"
    ctx.db.execute("UPDATE regex_scripts SET approved = 0")
    assert (await display(client, chat_id, "a"))["text"] == "a"


async def test_html_output_comes_back_as_a_cleaned_tree(client):
    _, chat_id = await setup(client)
    await add(client, r"/\[hp=(\d+)\]/g", '<div style="color:red" onclick="x()">HP $1<script>bad()</script></div>',
              render_html=True)
    result = await display(client, chat_id, "ok [hp=5] done")
    div = next(n for n in result["nodes"] if isinstance(n, dict))
    assert div["t"] == "div" and div["s"] == {"color": "red"} and "a" not in div
    assert div["c"] == ["HP 5"]
    await add(client, r"/fine/", "<b>fine</b>", name="plain")      # no render_html: stays text
    plain = await display(client, chat_id, "fine")
    assert plain["nodes"] is None and plain["text"] == "<b>fine</b>"


async def test_streaming_text_does_not_break_on_half_a_tag(client):
    _, chat_id = await setup(client)
    await add(client, r"/\[hp=(\d+)\]/g", "<b>HP $1</b>", render_html=True)
    half = await display(client, chat_id, '[hp=5] <div style="col', stream=True)
    assert "".join(words(half["nodes"])).strip() == "HP 5"
    whole = await display(client, chat_id, '[hp=5] <div style="color:red">done</div>', stream=True)
    assert "done" in "".join(words(whole["nodes"]))


async def test_summary_says_how_many_screen_rules_work_here(client):
    _, chat_id = await setup(client)
    empty = (await client.get(f"/api/regex/summary?chat_id={chat_id}")).json()
    assert empty["display"] == 0
    await add(client, "/a/", "b")
    await add(client, "/c/", "d", when="prompt")
    full = (await client.get(f"/api/regex/summary?chat_id={chat_id}")).json()
    assert full["display"] == 1 and full["stamp"] != empty["stamp"]


# --- the model --------------------------------------------------------------------------------


async def test_prompt_rules_change_only_what_the_model_gets(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await add(client, r"/\[status:[^\]]*\]/g", "", when="prompt", places=["user", "assistant"])
    fake_llm.queue("Fine. [status: hp=5]", "Next.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi [status: mood=ok]"}))
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "again"}))
    sent = "\n".join(m["content"] for m in fake_llm.requests[-1]["messages"])
    assert "[status" not in sent and "Fine." in sent and "hi " in sent
    stored = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    assert stored[-3]["content"] == "Fine. [status: hp=5]" and stored[-4]["content"] == "hi [status: mood=ok]"
    seen = (await client.get(f"/api/messages/{stored[-1]['id']}/prompt")).json()      # «Что видела модель»
    assert "[status" not in str(seen) and "Fine." in str(seen)


async def test_prompt_rules_use_depth_and_macros(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await add(client, "/Ann/g", "NAME", when="prompt", min_depth=1, places=["assistant"])   # not the newest message
    await add(client, "/{{char}}:/g", "", when="prompt", macros="raw", places=["user"])
    fake_llm.queue("Ann said hi", "Ann again")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "Ann: one"}))
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "Ann: two"}))
    joined = "\n".join(m["content"] for m in fake_llm.requests[-1]["messages"])
    assert "NAME said hi" in joined and "Ann: " not in joined and "one" in joined and "two" in joined


async def test_both_means_screen_and_model_but_not_the_saved_text(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await add(client, "/foo/g", "bar", when="both")
    fake_llm.queue("a foo b")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "x foo y"}))
    assert "x bar y" in str(fake_llm.requests[-1]["messages"])
    assert (await display(client, chat_id, "a foo b"))["text"] == "a bar b"
    assert (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]["content"] == "a foo b"


async def test_thinking_is_split_out_before_the_rules_see_the_reply(client, ctx, fake_llm):
    """Order: the model writes `<thinking>`; it is cut out of the reply; the saved-text rule sees the clean reply
    and the thinking stays untouched in the version's reasoning."""
    ann, chat_id = await setup(client)
    await add(client, "/secret/g", "S", when="stored", places=["assistant"])
    fake_llm.queue("<thinking>secret plan</thinking>The secret door.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "go"}))
    last = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    assert last["content"] == "The S door." and last["swipe_meta"]["reasoning"] == "secret plan"


async def test_lorebook_entries_can_be_rewritten_for_the_model(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    made = await client.post("/api/lorebooks", json={"name": "W"})
    book_id = made.json()["id"]
    entry = await client.post(f"/api/lorebooks/{book_id}/blocks", json={
        "title": "Town", "content": "The town is called Oldwick.", "mode": "always"})
    assert entry.status_code == 201, entry.text
    used = await client.put("/api/lorebook-uses", json={"scope": "global", "target": None, "lorebook_ids": [book_id]})
    assert used.status_code == 200, used.text
    fake_llm.queue("one", "two")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi"}))
    assert "Oldwick" in str(fake_llm.requests[-1]["messages"])
    await add(client, "/Oldwick/g", "Newtown", when="prompt", places=["lore"])
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi again"}))
    sent = str(fake_llm.requests[-1]["messages"])
    assert "Newtown" in sent and "Oldwick" not in sent


# --- the saved text ---------------------------------------------------------------------------


async def test_saved_text_rules_change_the_stored_message_once_and_can_be_taken_back(client, ctx, fake_llm):
    ann, chat_id = await setup(client)
    await add(client, r"/\s+$/", "", when="stored", places=["assistant"])
    await add(client, "/colour/g", "color", when="stored", places=["user", "assistant"])
    fake_llm.queue("The colour is red.   ")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "my colour?"}))
    messages = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]
    assert messages[-2]["content"] == "my color?" and messages[-2]["swipe_meta"]["regex_before"] == "my colour?"
    assert messages[-1]["content"] == "The color is red."
    assert messages[-1]["swipe_meta"]["regex_before"] == "The colour is red.   "
    back = (await client.post(f"/api/regex/messages/{messages[-1]['id']}/original")).json()
    assert back["content"] == "The colour is red.   " and "regex_before" not in back["swipe_meta"]
    assert (await client.post(f"/api/regex/messages/{messages[-1]['id']}/original")).status_code == 400


async def test_saved_text_rules_run_on_edit_only_when_asked(client):
    ann, chat_id = await setup(client)
    await add(client, "/a/g", "b", when="stored", places=["assistant"], run_on_edit=False, name="no")
    await add(client, "/c/g", "d", when="stored", places=["assistant"], run_on_edit=True, name="yes")
    greeting = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][0]
    edited = await client.put(f"/api/messages/{greeting['id']}", json={"content": "a c"})
    assert edited.json()["content"] == "a d"


# --- the time limit ---------------------------------------------------------------------------


async def test_a_runaway_pattern_is_stopped_skipped_and_named(client, ctx, monkeypatch):
    monkeypatch.setattr(regex_runner, "RULE_SECONDS", 1.0)
    _, chat_id = await setup(client)
    bad = await add(client, "/(a+)+$/", "X", name="Зависающий")
    await add(client, "/b/g", "B", name="Нормальный")
    nasty = "a" * 40 + "b"
    started = time.monotonic()
    result = await display(client, chat_id, nasty)
    assert time.monotonic() - started < 6
    assert result["text"] == "a" * 40 + "B"            # the other rule still worked
    again = time.monotonic()
    assert (await display(client, chat_id, nasty + "b"))["text"].endswith("BB")   # skipped now: no new wait
    assert time.monotonic() - again < 1.0
    listed = (await client.get("/api/regex/rules?scope=global")).json()["rules"]
    assert next(r for r in listed if r["id"] == bad["id"])["slow"] is True
    await client.put(f"/api/regex/rules/{bad['id']}", json={"find": "/(a)+x/"})        # edited: tried again
    listed = (await client.get("/api/regex/rules?scope=global")).json()["rules"]
    assert not next(r for r in listed if r["id"] == bad["id"])["slow"]


async def test_the_page_is_told_which_rule_was_skipped(client, ctx, monkeypatch):
    monkeypatch.setattr(regex_runner, "RULE_SECONDS", 1.0)
    _, chat_id = await setup(client)
    await add(client, "/(a+)+$/", "X", name="Зависающий")
    events = ctx.notices.subscribe()
    first = asyncio.ensure_future(events.__anext__())
    await asyncio.sleep(0)
    await display(client, chat_id, "a" * 40 + "b")
    notice = await asyncio.wait_for(first, 5)
    assert notice["type"] == "regex_skipped" and notice["rules"][0]["name"] == "Зависающий"
    await events.aclose()


def test_runner_bound_on_a_real_catastrophic_pattern():
    spec = build_spec({"find": "/(x+x+)+y/g", "replace": "!"})[0]
    quick = build_spec({"find": "/c/g", "replace": "C"})[0]
    started = time.monotonic()
    outcome = regex_runner.RUNNER.run([spec, quick], [{"text": "x" * 60 + "c", "rules": [0, 1]}], rule_seconds=1.0)
    took = time.monotonic() - started
    assert took < 5
    assert outcome.skipped == {0: "slow"} and outcome.outs == ["x" * 60 + "C"]
    assert regex_runner.RUNNER.run([quick], [{"text": "abc", "rules": [0]}]).outs == ["abC"]     # the worker came back


def test_the_whole_job_has_a_limit_too():
    slowish = build_spec({"find": "/(x+x+)+y/g", "replace": "!"})[0]
    outcome = regex_runner.RUNNER.run([slowish], [{"text": "x" * 60, "rules": [0]}], rule_seconds=30.0, job_seconds=1.0)
    assert outcome.skipped and outcome.outs == ["x" * 60]


# --- try-out ----------------------------------------------------------------------------------


async def test_try_out_shows_matches_result_and_errors(client):
    response = await client.post("/api/regex/try", json={
        "rule": {"find": r"/(?<n>\d+)/g", "replace": "<$<n>>"}, "text": "a1 b22"})
    result = response.json()
    assert result["ok"] and result["result"] == "a<1> b<22>"
    assert [(m["start"], m["end"], m["text"]) for m in result["matches"]] == [(1, 2, "1"), (4, 6, "22")]
    assert result["names"] == ["n"]
    bad = (await client.post("/api/regex/try", json={"rule": {"find": "/(a/"}, "text": "x"})).json()
    assert bad["ok"] is False and bad["error"] and bad["result"] == "x"
    slow = (await client.post("/api/regex/try", json={"rule": {"find": "/(a+)+$/"}, "text": "a" * 40 + "b"})).json()
    assert slow["ok"] is False and "слишком долго" in slow["error"]
    html = (await client.post("/api/regex/try", json={
        "rule": {"find": "/x/", "replace": "<i>y</i><script>1</script>", "render_html": True}, "text": "x"})).json()
    assert html["nodes"][0]["t"] == "i" and len(html["nodes"]) == 1


# --- import and export ------------------------------------------------------------------------

ST_SCRIPT = {
    "id": "b3f1c2d4-0000-4000-8000-000000000001", "scriptName": "Hide status", "findRegex": "/\\[status:.*?\\]/gs",
    "replaceString": "", "trimStrings": ["!"], "placement": [2, 3], "disabled": False, "markdownOnly": True,
    "promptOnly": False, "runOnEdit": True, "substituteRegex": 1, "minDepth": 1, "maxDepth": None,
    "futureField": {"keep": "me"},
}


async def test_import_export_round_trip_keeps_unknown_fields(client):
    imported = (await client.post("/api/regex/import", json={"scope": "global", "scripts": ST_SCRIPT})).json()
    rule = imported["imported"][0]
    assert rule["approved"] and rule["places"] == ["assistant"] and rule["when"] == "display"
    assert rule["min_depth"] == 1 and rule["macros"] == "raw" and rule["run_on_edit"] and rule["trim"] == ["!"]
    out = (await client.get(f"/api/regex/export?scope=global&ids={rule['id']}")).json()
    for key, value in ST_SCRIPT.items():
        assert out[key] == value, key        # including the slash-command placement 3 we cannot serve, and futureField
    assert out["tavern"]["render_html"] is False


async def test_import_an_array_and_our_own_export_back(client):
    second = {**ST_SCRIPT, "id": "x2", "scriptName": "Two", "findRegex": "/two/", "markdownOnly": False,
              "promptOnly": True}
    first = (await client.post("/api/regex/import", json={"scope": "global", "scripts": [ST_SCRIPT, second]})).json()
    assert len(first["imported"]) == 2 and first["problems"] == []
    assert first["imported"][1]["when"] == "prompt"
    mine = await add(client, "/<x>/", "<b>x</b>", render_html=True, name="Own", macros="escaped")
    exported = (await client.get("/api/regex/export?scope=global")).json()
    assert isinstance(exported, list) and len(exported) == 3
    await client.delete(f"/api/regex/rules/{mine['id']}")
    again = (await client.post("/api/regex/import", json={"scope": "global", "scripts": exported})).json()
    own = next(r for r in again["imported"] if r["name"] == "Own")
    assert own["render_html"] is True and own["macros"] == "escaped" and own["replace"] == "<b>x</b>"


async def test_a_script_with_a_pattern_we_cannot_run_is_kept_switched_off(client):
    odd = {**ST_SCRIPT, "findRegex": "/\\p{L}+/u", "scriptName": "Letters"}
    result = (await client.post("/api/regex/import", json={"scope": "global", "scripts": odd})).json()
    assert result["problems"] and "Юникода" in result["problems"][0]["why"]
    rule = result["imported"][0]
    assert rule["enabled"] is False and rule["error"] and rule["find"] == odd["findRegex"]
    not_a_script = await client.post("/api/regex/import", json={"scope": "global", "scripts": {"hello": 1}})
    assert not_a_script.status_code == 400


async def test_rules_in_an_imported_card_wait_for_permission(client, ctx, fake_llm):
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "Zed", "description": "d", "first_mes": "hi", "extensions": {
            "world": "x", "regex_scripts": [{**ST_SCRIPT, "markdownOnly": False, "promptOnly": True,
                                             "placement": [1, 2]}]}}}
    created = (await client.post("/api/characters", json=card)).json()
    assert "regex_scripts" not in created["data"]["extensions"] and created["data"]["extensions"]["world"] == "x"
    rules = (await client.get(f"/api/regex/rules?scope=character&owner={created['id']}")).json()["rules"]
    assert len(rules) == 1 and rules[0]["approved"] is False
    assert (await client.get("/api/regex")).json()["characters"][0]["pending"] == 1

    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": created["id"]})).json()["chat"]
    fake_llm.queue("ok", "ok")
    await finish(ctx, await client.post(f"/api/chats/{chat['id']}/send", json={"text": "x [status: hp=1] y"}))
    assert "[status" in str(fake_llm.requests[-1]["messages"])       # not allowed yet: the model gets it as is

    approved = await client.post("/api/regex/approve", json={"scope": "character", "owner": created["id"]})
    assert approved.json() == {"approved": 1}
    await finish(ctx, await client.post(f"/api/chats/{chat['id']}/send", json={"text": "x [status: hp=1] y"}))
    assert str(fake_llm.requests[-1]["messages"]).count("[status") == 1      # min depth 1: only the newest keeps it

    exported = (await client.get(f"/api/characters/{created['id']}/export.json")).json()
    scripts = exported["data"]["extensions"]["regex_scripts"]
    assert scripts[0]["futureField"] == {"keep": "me"} and scripts[0]["id"] == ST_SCRIPT["id"]
    assert exported["data"]["extensions"]["world"] == "x"


async def test_rules_in_an_imported_preset_wait_for_permission(client):
    st = {"temperature": 0.8, "prompts": [{"identifier": "main", "name": "Main", "role": "system", "content": "Hi"},
                                          {"identifier": "chatHistory", "name": "H", "marker": True}],
          "extensions": {"regex_scripts": [ST_SCRIPT], "other": 1}}
    response = await client.post("/api/presets/import", json=st)
    assert response.status_code in (200, 201), response.text
    preset = response.json()
    rules = (await client.get(f"/api/regex/rules?scope=preset&owner={preset['id']}")).json()["rules"]
    assert len(rules) == 1 and rules[0]["approved"] is False
    stored = (await client.get(f"/api/presets/{preset['id']}")).json()
    assert "regex_scripts" not in (stored["data"]["st"].get("extensions") or {})
    assert stored["data"]["st"]["extensions"]["other"] == 1
    exported = (await client.get(f"/api/presets/{preset['id']}/export?format=st")).json()
    assert exported["extensions"]["other"] == 1
    assert exported["extensions"]["regex_scripts"][0]["futureField"] == {"keep": "me"}
    native = (await client.get(f"/api/presets/{preset['id']}/export")).json()
    again = await client.post("/api/presets/import", json=native)
    assert again.status_code in (200, 201)
    copied = (await client.get(f"/api/regex/rules?scope=preset&owner={again.json()['id']}")).json()["rules"]
    assert len(copied) == 1 and copied[0]["approved"] is False


# --- copies, doctor, migration ----------------------------------------------------------------


async def test_rules_are_in_the_whole_data_copy_and_come_back_on_restore(client, ctx):
    rule = await add(client, "/a/", "b", name="kept")
    made = ctx.backups.create("manual", force=True)
    await client.delete(f"/api/regex/rules/{rule['id']}")
    assert regex_service.repo(ctx).list("global") == []
    ctx.backups.restore(made["name"])
    assert [r["name"] for r in regex_service.repo(ctx).list("global")] == ["kept"]


async def test_the_doctor_names_rules_that_cannot_work(client, ctx):
    rule = await add(client, "/a/", "b", name="fine")
    ctx.db.execute("UPDATE regex_scripts SET find = ? WHERE id = ?", ("/(a/", rule["id"]))
    ctx.db.execute("INSERT INTO regex_scripts (id, scope, owner_id, name, find, created_at, updated_at) "
                   "VALUES ('orphan', 'character', 'gone', 'o', '/a/', 'now', 'now')")
    found = regex_service.problems(ctx)
    assert {p["id"] for p in found} == {rule["id"], "orphan"}


def test_migration_on_a_database_from_before(tmp_path):
    db = Database(tmp_path / "old.sqlite3")
    db.migrate([m for m in load_migrations() if m.version < 22])
    db.execute("INSERT INTO characters (id, name, data) VALUES ('a', 'Ann', '{}')")
    db.execute("INSERT INTO presets (id, name, data) VALUES ('p', 'P', '{}')")
    assert db.fetchone("SELECT name FROM sqlite_master WHERE name = 'regex_scripts'") is None
    db.migrate()
    assert db.fetchone("SELECT name FROM sqlite_master WHERE name = 'regex_scripts'") is not None
    db.execute("INSERT INTO regex_scripts (id, scope, owner_id, find, created_at, updated_at) "
               "VALUES ('r', 'character', 'a', '/x/', 'n', 'n')")
    db.execute("DELETE FROM characters WHERE id = 'a'")
    assert db.fetchone("SELECT COUNT(*) FROM regex_scripts")[0] == 0
    assert db.fetchone("SELECT name FROM presets WHERE id = 'p'") is not None


# --- speed ------------------------------------------------------------------------------------


async def test_a_thousand_rules_over_a_long_chat_stay_fast(client, ctx):
    """Owner's question: «1000 regexes, lag?». Measured at the time: view of 300 messages ~150 ms cold, ~10 ms
    cached; the bounds are generous so a slow machine does not fail, but a return of the per-rule work does."""
    ann, chat_id = await setup(client)
    rules = [{"name": f"r{i}", "find": f"/#{i}#/g", "replace": f"W{i}", "places": ["user", "assistant"],
              "when": "display" if i % 3 else "both", "min_depth": 2 if i % 7 == 0 else None}
             for i in range(1000)]
    from tavern.storage.repos.regex import RegexRepo
    from tavern.core.regex import normalize_rule

    RegexRepo(ctx.db).create_many("global", "", [normalize_rule(r) for r in rules])
    items = [{"id": str(i), "text": f"message {i} with #{i % 1000}# and #{(i * 7) % 1000}# " * 4,
              "role": "assistant" if i % 2 else "user", "depth": 299 - i} for i in range(300)]
    started = time.monotonic()
    first = (await client.post("/api/regex/display", json={"chat_id": chat_id, "items": items})).json()
    cold = time.monotonic() - started
    started = time.monotonic()
    second = (await client.post("/api/regex/display", json={"chat_id": chat_id, "items": items})).json()
    warm = time.monotonic() - started
    assert second == first and first["skipped"] == []
    assert "W5 " in first["results"]["5"]["text"] and "#5#" not in first["results"]["5"]["text"]
    # the prompt: all rules in one worker call
    chat = ctx.chats.get_chat(chat_id)
    history = [{"id": i["id"], "role": i["role"], "content": i["text"], "swipe_meta": {}} for i in items]
    started = time.monotonic()
    messages, _, _, skipped = await asyncio.to_thread(regex_service.prompt_texts, ctx, chat, history, [], "Ann", "You")
    prompt = time.monotonic() - started
    assert skipped == [] and "W3 " in messages["3"] and "5" not in messages
    print(f"\nregex 1000 rules x 300 messages: display cold {cold * 1000:.0f} ms, cached {warm * 1000:.0f} ms, "
          f"prompt {prompt * 1000:.0f} ms")
    assert cold < 4.0 and warm < 0.8 and prompt < 3.0


async def test_import_of_a_thousand_rules_keeps_them_all(client):
    scripts = [{"scriptName": f"s{i}", "findRegex": f"/x{i}/g", "replaceString": "y", "placement": [1, 2]}
               for i in range(1000)]
    result = (await client.post("/api/regex/import", json={"scope": "global", "scripts": scripts})).json()
    assert len(result["imported"]) == 1000 and result["problems"] == []
    assert [r["position"] for r in result["imported"]] == list(range(1, 1001))
    assert len((await client.get("/api/regex/rules?scope=global")).json()["rules"]) == 1000


async def test_rules_read_again_after_every_kind_of_change(client):
    """The rules of a chat are kept between requests; any change (edit, switch off, order, delete) shows at once."""
    _, chat_id = await setup(client)
    a = await add(client, "/a/", "1", name="a")
    b = await add(client, "/a/", "2", name="b")
    assert (await display(client, chat_id, "a"))["text"] == "1"       # a -> 1, then b finds no "a" left
    assert (await display(client, chat_id, "a"))["text"] == "1"
    await client.put("/api/regex/order", json={"scope": "global", "ids": [b["id"], a["id"]]})
    assert (await display(client, chat_id, "a"))["text"] == "2"       # b -> 2, a finds no "a"
    await client.put(f"/api/regex/rules/{a['id']}", json={"enabled": False})
    assert (await display(client, chat_id, "a"))["text"] == "2"
    await client.delete(f"/api/regex/rules/{b['id']}")
    assert (await display(client, chat_id, "a"))["text"] == "a"


async def test_the_doctor_fix_removes_rules_without_an_owner(client, ctx):
    from tavern.services import doctor

    ctx.db.execute("INSERT INTO regex_scripts (id, scope, owner_id, name, find, created_at, updated_at) "
                   "VALUES ('orphan', 'preset', 'gone', 'o', '/a/', 'now', 'now')")
    assert "regex_orphans" in [f["id"] for f in doctor.full(ctx)]
    result = doctor.fix(ctx, ["regex_orphans"])
    assert result["fixed"] == ["regex_orphans"]
    assert regex_service.problems(ctx) == []


async def test_changing_how_a_result_is_shown_does_not_serve_the_old_cached_one(client):
    _, chat_id = await setup(client)
    rule = await add(client, "/a/", "<b>x</b>", render_html=True)
    assert (await display(client, chat_id, "a"))["nodes"] is not None
    await client.put(f"/api/regex/rules/{rule['id']}", json={"render_html": False})
    shown = await display(client, chat_id, "a")
    assert shown["nodes"] is None and shown["text"] == "<b>x</b>"


async def test_the_assistant_can_reach_the_rules_and_try_them_but_not_allow_them(ctx):
    from tavern.services import assistant_app
    from tavern.services.assistant_tools import ToolError

    for method, path in (("GET", "/regex"), ("POST", "/regex/rules"), ("POST", "/regex/try"), ("DELETE", "/regex/rules/x")):
        assistant_app._check(method, path, {})
    assert "/regex/try" in assistant_app.READ_POSTS and "/regex/display" in assistant_app.READ_POSTS
    with pytest.raises(ToolError):
        assistant_app._check("POST", "/regex/approve", {"scope": "character", "owner": "x"})


async def test_a_rule_the_assistant_makes_is_journalled_and_can_be_taken_back(ctx):
    from tavern.services import assistant_app
    from tavern.storage import snapshot

    env = assistant_app.Env(ctx, None)
    made = await assistant_app.app_call(env, {"method": "POST", "path": "/regex/rules", "what": "Правило: убрать статус",
                                              "body": {"scope": "global", "name": "Статус", "find": r"/\[s\]/g"}})
    assert made.result["status"] == 201 and made.what == "Правило: убрать статус"
    assert [r["name"] for r in regex_service.repo(ctx).list("global")] == ["Статус"]
    snapshot.restore(ctx.db, made.undo)
    assert regex_service.repo(ctx).list("global") == []
    tried = await assistant_app.app_call(env, {"method": "POST", "path": "/regex/try",
                                               "body": {"rule": {"find": "/a/", "replace": "b"}, "text": "a"}})
    assert tried.result["data"]["result"] == "b" and tried.what == ""
