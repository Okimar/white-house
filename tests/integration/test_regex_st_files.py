"""The owner's real SillyTavern regex files (tests/fixtures/st_regex/), end to end: import through the route the
Settings «Импорт» button uses, round trip back to the same fields, then what the screen, the saved text and the model
get. U+2800 is the BRAILLE PATTERN BLANK; it is written by escape so it survives any editor."""

from __future__ import annotations

import asyncio
import itertools
import json
import pathlib
import time

import pytest

from tavern.core.regex import split_find, translate
from tavern.services import regex as regex_service

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "st_regex"
BLANK = "⠀"
AI, USER, NORMAL, HTML, REASONING = ("regex-jailbreakai.json", "regex-jailbreakuser.json",
                                     "regex-return_normal_spaces.json", "regex-hide_html.json",
                                     "regex-hide_reasoning.json")
THREE = (AI, USER, NORMAL)


@pytest.fixture(autouse=True)
def _fresh_regex_state():
    regex_service._cache.clear()
    regex_service._slow.clear()
    yield
    regex_service._cache.clear()
    regex_service._slow.clear()


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def import_files(client, *names, as_array=False):
    """What the «Импорт» button does: POST /regex/import with the parsed file."""
    if as_array:
        responses = [await client.post("/api/regex/import", json={"scope": "global", "scripts": [load(n) for n in names]})]
    else:
        responses = [await client.post("/api/regex/import", json={"scope": "global", "scripts": load(n)}) for n in names]
    out = []
    for response in responses:
        assert response.status_code == 201, response.text
        assert response.json()["problems"] == []
        out.extend(response.json()["imported"])
    return out


async def clear_rules(client):
    for rule in (await client.get("/api/regex/rules?scope=global")).json()["rules"]:
        await client.delete(f"/api/regex/rules/{rule['id']}")


async def finish(ctx, response):
    assert response.status_code == 202, response.text
    run = ctx.runs.get(response.json()["run_id"])
    await asyncio.wait([run.task], timeout=10)


async def setup(client, name="Ann", **card):
    ann = (await client.post("/api/characters", json={"name": name, "description": "Ann is kind.",
                                                      "first_mes": "Hello", **card})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return ann, chat["id"]


async def show(client, chat_id, text, role="assistant", depth=0):
    response = await client.post("/api/regex/display", json={
        "chat_id": chat_id, "items": [{"id": "x", "text": text, "role": role, "depth": depth}]})
    assert response.status_code == 200, response.text
    return response.json()["results"]["x"]["text"]


async def messages(client, chat_id):
    return (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"]


def sent_texts(fake_llm):
    return [m["content"] for m in fake_llm.requests[-1]["messages"]]


# --- every file: the fields, the round trip ---------------------------------------------------


async def test_each_file_is_imported_on_and_exported_with_the_same_values(client):
    for name in (AI, USER, NORMAL, HTML, REASONING):
        original = load(name)
        [rule] = await import_files(client, name)
        assert rule["enabled"] is True and rule["approved"] is True and rule["error"] == "", name   # `disabled` is false
        out = (await client.get(f"/api/regex/export?scope=global&ids={rule['id']}")).json()
        for key, value in original.items():   # id, trimStrings, flags, depths, substituteRegex: all as they came
            assert out[key] == value, (name, key)
        await clear_rules(client)


async def test_the_fields_mean_what_the_files_say(client):
    ai, user, normal, html, reasoning = await import_files(client, AI, USER, NORMAL, HTML, REASONING)
    assert (ai["when"], ai["places"], ai["find"]) == ("display", ["assistant", "reasoning"], f"/{BLANK}/g")
    assert (user["when"], user["places"], user["replace"]) == ("prompt", ["user"], BLANK)
    assert (normal["when"], normal["places"], normal["macros"]) == ("both", ["user", "assistant"], "escaped")
    assert normal["find"] == "/ /g" and normal["replace"] == " "   # as it reached us; the original may carry a blank
    assert (html["when"], html["places"], html["min_depth"]) == ("prompt", ["assistant"], 5)
    assert (reasoning["when"], reasoning["places"], reasoning["min_depth"]) == ("both", ["user", "assistant"], 5)
    assert all(r["run_on_edit"] for r in (ai, user, normal, html, reasoning))


async def test_an_array_of_files_imports_in_order_and_exports_as_an_array(client):
    made = await import_files(client, *THREE, as_array=True)
    assert [r["name"] for r in made] == ["JailbreakAI", "jailbreakuser", "Return Normal Spaces"]
    exported = (await client.get("/api/regex/export?scope=global")).json()
    assert [e["id"] for e in exported] == [load(n)["id"] for n in THREE]
    for e, name in zip(exported, THREE):
        for key, value in load(name).items():
            assert e[key] == value, (name, key)


async def test_both_can_be_chosen_in_the_rule_and_goes_back_as_both_flags(client):
    [rule] = await import_files(client, USER)
    changed = (await client.put(f"/api/regex/rules/{rule['id']}", json={"when": "both"})).json()
    out = (await client.get(f"/api/regex/export?scope=global&ids={rule['id']}")).json()
    assert changed["when"] == "both" and out["markdownOnly"] is True and out["promptOnly"] is True
    stored = (await client.put(f"/api/regex/rules/{rule['id']}", json={"when": "stored"})).json()
    out = (await client.get(f"/api/regex/export?scope=global&ids={rule['id']}")).json()
    assert stored["when"] == "stored" and out["markdownOnly"] is False and out["promptOnly"] is False


async def test_macros_in_the_pattern_raw_and_escaped_differ_and_survive_export(client):
    _, chat_id = await setup(client, name="A.n")
    base = {"scriptName": "n", "findRegex": "/{{char}}/g", "replaceString": "X", "placement": [2],
            "markdownOnly": True, "promptOnly": False, "substituteRegex": 1}
    [raw] = (await client.post("/api/regex/import", json={"scope": "global", "scripts": base})).json()["imported"]
    assert await show(client, chat_id, "Aon A.n") == "X X"                      # raw: the dot is a pattern dot
    await client.delete(f"/api/regex/rules/{raw['id']}")
    [esc] = (await client.post("/api/regex/import", json={"scope": "global",
                                                          "scripts": {**base, "substituteRegex": 2}})).json()["imported"]
    assert await show(client, chat_id, "Aon A.n") == "Aon X"                    # escaped: only the name itself
    out = (await client.get(f"/api/regex/export?scope=global&ids={esc['id']}")).json()
    assert out["substituteRegex"] == 2 and raw["macros"] == "raw" and esc["macros"] == "escaped"


# --- the first three files at work ------------------------------------------------------------


async def test_user_text_goes_to_the_model_with_blanks_but_stays_normal_on_screen_and_in_storage(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, *THREE)
    fake_llm.queue("ok")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "привет как дела"}))
    sent = [t for t in sent_texts(fake_llm) if "привет" in t][-1]
    assert sent == f"привет{BLANK}как{BLANK}дела"
    rows = await messages(client, chat_id)
    mine = next(m for m in rows if m["role"] == "user")
    assert mine["content"] == "привет как дела"
    assert await show(client, chat_id, mine["content"], role="user") == "привет как дела"
    # the old message goes the same way on the next turn
    fake_llm.queue("again")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "и ещё раз"}))
    joined = "\n".join(sent_texts(fake_llm))
    assert f"привет{BLANK}как{BLANK}дела" in joined and f"и{BLANK}ещё{BLANK}раз" in joined


async def test_a_reply_with_blanks_shows_spaces_is_kept_and_goes_back_with_blanks(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, *THREE)
    reply = f"Всё{BLANK}хорошо{BLANK}спасибо"
    fake_llm.queue(reply, "next")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "hi"}))
    last = (await messages(client, chat_id))[-1]
    assert last["content"] == reply                                              # stored as the model wrote it
    assert await show(client, chat_id, last["content"]) == "Всё хорошо спасибо"  # file 1: the screen
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "more"}))
    assert reply in sent_texts(fake_llm)                                         # history keeps the blanks


async def test_what_the_model_saw_shows_the_sent_form(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, *THREE)
    fake_llm.queue(f"a{BLANK}b")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "привет как дела"}))
    last = (await messages(client, chat_id))[-1]
    seen = (await client.get(f"/api/messages/{last['id']}/prompt")).json()
    texts = [e["text"] for e in seen["entries"]]
    assert f"привет{BLANK}как{BLANK}дела" in texts and "привет как дела" not in texts
    counted = await client.post(f"/api/messages/{last['id']}/prompt/count")      # token counting still works
    assert counted.status_code == 200 and counted.json()["tokens"] > 0


async def test_the_three_together_are_stable_in_any_order(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    for order in itertools.permutations(THREE):
        await clear_rules(client)
        await import_files(client, *order)
        assert await show(client, chat_id, f"a{BLANK}b c", role="assistant") == "a b c", order
        assert await show(client, chat_id, "a b c", role="user") == "a b c", order
        fake_llm.queue(f"x{BLANK}y")
        await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "раз два"}))
        assert f"раз{BLANK}два" in sent_texts(fake_llm), order
        assert (await messages(client, chat_id))[-1]["content"] == f"x{BLANK}y", order


async def test_a_rule_that_gives_the_spaces_back_works_in_file_order(client, ctx, fake_llm):
    """If the real third file turns blanks back into spaces for the model too, order decides, and it is the file order."""
    _, chat_id = await setup(client)
    back = {**load(NORMAL), "id": "back", "findRegex": f"/{BLANK}/g", "replaceString": " ", "substituteRegex": 0}
    await import_files(client, USER)
    await client.post("/api/regex/import", json={"scope": "global", "scripts": back})
    fake_llm.queue("ok")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "раз два"}))
    assert "раз два" in sent_texts(fake_llm)                                     # blanks made, then undone


async def test_reasoning_place_changes_the_old_reasoning_sent_back_and_is_shown_by_display(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    preset = (await client.post("/api/presets", json={"name": "T", "from": "default"})).json()
    data = preset["data"]
    next(b for b in data["blocks"] if b["kind"] == "history")["history"] = {"mode": "all", "count": 50,
                                                                          "max_tokens": 0, "thoughts": 3}
    assert (await client.put(f"/api/presets/{preset['id']}", json={"name": "T", "data": data})).status_code == 200
    await client.put(f"/api/chats/{chat_id}/meta", json={"preset_id": preset["id"]})
    await import_files(client, AI)
    await client.post("/api/regex/rules", json={"scope": "global", "name": "t", "find": "/секрет/g", "replace": "S",
                                                "places": ["reasoning"], "when": "prompt"})
    fake_llm.queue(f"<thinking>план{BLANK}секрет</thinking>Ответ{BLANK}тут", "next")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "go"}))
    last = (await messages(client, chat_id))[-1]
    assert last["content"] == f"Ответ{BLANK}тут" and last["swipe_meta"]["reasoning"] == f"план{BLANK}секрет"
    assert await show(client, chat_id, last["swipe_meta"]["reasoning"], role="reasoning") == "план секрет"
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "again"}))
    joined = "\n".join(sent_texts(fake_llm))
    assert f"план{BLANK}S" in joined and "секрет" not in joined             # old thinking, rewritten for the model
    assert last["swipe_meta"]["reasoning"] == f"план{BLANK}секрет"           # stored one untouched


async def test_macros_in_a_message_and_in_a_replacement_still_work(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, USER)
    await client.post("/api/regex/rules", json={"scope": "global", "name": "who", "find": "/кто/g",
                                                "replace": "{{char}}", "places": ["user"], "when": "prompt"})
    fake_llm.queue("ok")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "кто {{user}} {{char}}"}))
    sent = [t for t in sent_texts(fake_llm) if "Ann" in t and BLANK in t and "{{" not in t]
    assert sent, sent_texts(fake_llm)
    assert (await messages(client, chat_id))[-2]["content"] == "кто {{user}} {{char}}"


# --- Hide html / Hide reasoning ----------------------------------------------------------------


def test_the_pattern_split_uses_the_last_slash_and_both_patterns_translate():
    find = load(REASONING)["findRegex"]
    assert split_find(find) == ("<think>[\\s\\S]*?</think>", "g")             # the `/` inside is not an end
    assert translate(find).glob
    html = load(HTML)["findRegex"]
    assert split_find(html)[1] == "g" and translate(html).groups == 1


async def long_chat(client, ctx, chat_id, count, text, exclude=()):
    leaf = ctx.chats.get_chat(chat_id)["leaf_id"]
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        leaf = ctx.chats.add_message(chat_id, role=role, content=text(i, role), parent_id=leaf,
                                  exclude_from_prompt=i in exclude)["id"]


async def test_hide_html_and_hide_reasoning_work_by_depth_in_prompt_and_display(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, HTML, REASONING)
    body = "a <b>bold</b> b<br/> c<img src='x.png'> d <think>plan</think>e"

    def text(i, role):
        return f"{role} #{i}: {body}"

    await long_chat(client, ctx, chat_id, 9, text)       # greeting + 9 = 10 messages, then the user sends one
    fake_llm.queue("done")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "last <i>x</i>"}))
    rows = await messages(client, chat_id)
    sent = [m["content"] for m in fake_llm.requests[-1]["messages"]]
    # path: greeting, #0..#8, the new message. #i has depth 9 - i, so the rules (from depth 5) reach #0..#4.
    for i in range(9):
        role = "user" if i % 2 == 0 else "assistant"
        [line] = [s for s in sent if s.startswith(f"{role} #{i}:")]
        if 9 - i >= 5:
            assert "<think>" not in line, i
            assert ("<b>" not in line and "<br/>" not in line and "<img" not in line) == (role == "assistant"), i
        else:
            assert "<think>plan</think>" in line and "<b>bold</b>" in line and "<br/>" in line, i
    # model replies only for HTML: a user message keeps its tags at any depth
    old_user = next(s for s in sent if s.startswith("user #0"))
    assert "<b>bold</b>" in old_user and "<think>" not in old_user
    # stored text unchanged
    assert all("<think>plan</think>" in m["content"] for m in rows if "#" in m["content"])
    # display (Hide reasoning is `both`; Hide html is prompt only), by the page's depth
    assert "<think>" not in await show(client, chat_id, f"x {body}", role="assistant", depth=5)
    assert "<think>plan</think>" in await show(client, chat_id, f"x {body}", role="assistant", depth=4)
    assert "<b>bold</b>" in await show(client, chat_id, f"x {body}", role="assistant", depth=9)


async def test_depth_counts_from_the_newest_message_not_from_what_fits_the_budget(client, ctx, fake_llm):
    """Depth counts the whole active path (hidden-from-prompt ones too), so trimming old messages by `last N`
    does not change which message is "5 back"."""
    _, chat_id = await setup(client)
    await import_files(client, REASONING)
    preset = (await client.post("/api/presets", json={"name": "L", "from": "default"})).json()
    data = preset["data"]
    next(b for b in data["blocks"] if b["kind"] == "history")["history"] = {"mode": "last", "count": 4,
                                                                          "max_tokens": 0, "thoughts": 0}
    await client.put(f"/api/presets/{preset['id']}", json={"name": "L", "data": data})
    await client.put(f"/api/chats/{chat_id}/meta", json={"preset_id": preset["id"]})
    await long_chat(client, ctx, chat_id, 9, lambda i, role: f"m{i} <think>t{i}</think>")
    fake_llm.queue("done")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "<think>new</think> q"}))
    sent = "\n".join(sent_texts(fake_llm))
    assert "<think>" in sent        # the 4 newest are depth 0..3: under 5, so untouched even though older ones are cut
    assert "t4" not in sent


async def test_a_message_hidden_from_the_prompt_does_not_count_for_depth(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, REASONING)
    await long_chat(client, ctx, chat_id, 6, lambda i, role: f"m{i} <think>t{i}</think>", exclude={4})
    fake_llm.queue("done")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "q"}))
    sent = "\n".join(sent_texts(fake_llm))
    # SillyTavern counts only the messages it sends: greeting, m0..m3, m5 (m4 hidden), the new one. m0 is depth 5:
    # cut; m1 is depth 4 (it would be 5 had the hidden m4 counted): kept
    assert "t4" not in sent and "t0" not in sent
    assert "<think>t1</think>" in sent and "<think>t2</think>" in sent and "<think>t5</think>" in sent


async def test_a_think_block_cut_at_save_time_is_not_handled_twice(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_files(client, REASONING)
    fake_llm.queue("<think>hidden plan</think>Visible.")
    await finish(ctx, await client.post(f"/api/chats/{chat_id}/send", json={"text": "go"}))
    last = (await messages(client, chat_id))[-1]
    assert last["content"] == "Visible." and last["swipe_meta"]["reasoning"] == "hidden plan"
    # a literal tag in older stored text (typed by hand) is removed by the rule only at depth >= 5
    leaf = ctx.chats.get_chat(chat_id)["leaf_id"]
    old = ctx.chats.add_message(chat_id, role="assistant", content="old <think>x</think> text", parent_id=leaf)
    assert await show(client, chat_id, old["content"], depth=4) == "old <think>x</think> text"
    assert await show(client, chat_id, old["content"], depth=5) == "old  text"


# --- speed -------------------------------------------------------------------------------------
