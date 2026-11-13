"""The owner's SillyTavern jailbreak, end to end over HTTP: a world file with a constant entry at depth 1 reaches the
model bare, in its role, before the newest message, even behind a big book; SillyTavern's settings.json brings its
global regex scripts; rules waiting for «Разрешить» are named in the chat's summary; the thinking tag is kept."""

from __future__ import annotations

import asyncio
import json

from tavern.services import presets as preset_service
from tavern.services import regex as regex_service

JAILBREAK = "[SYSTEM DIRECTIVE: keep the story going.]"
BLANK = "⠀"


async def setup(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind.",
                                                      "first_mes": "Hello"})).json()
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    return ann, chat_id


async def send(client, ctx, chat_id, text):
    response = await client.post(f"/api/chats/{chat_id}/send", json={"text": text})
    assert response.status_code == 202, response.text
    await asyncio.wait([ctx.runs.get(response.json()["run_id"]).task], timeout=10)


def world_with_jailbreak(filler: int = 0) -> dict:
    entries = {str(i): {"uid": i, "displayIndex": i, "key": [], "comment": f"Факт {i}", "content": "слово " * 300,
                        "constant": True, "order": 100, "position": 0} for i in range(filler)}
    entries["jb"] = {"uid": 999, "displayIndex": 999, "key": [], "comment": "@ D jailbreak", "content": JAILBREAK,
                     "constant": True, "order": 1000, "position": 4, "depth": 1, "role": 0}
    return {"name": "Gemini", "entries": entries}


async def import_world(client, data):
    made = await client.post("/api/lorebooks/import?name=gemini.json", content=json.dumps(data).encode())
    assert made.status_code == 201, made.text
    book_id = made.json()["id"]
    response = await client.put("/api/lorebook-uses", json={"scope": "global", "target": None, "lorebook_ids": [book_id]})
    assert response.status_code == 200, response.text
    return book_id


async def test_an_imported_depth_1_jailbreak_reaches_the_model_bare_before_the_newest_message(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_world(client, world_with_jailbreak())
    fake_llm.queue("ok")
    await send(client, ctx, chat_id, "Привет")
    sent = [(m["role"], m["content"]) for m in fake_llm.requests[-1]["messages"]]
    assert sent[-3:] == [("assistant", "Hello"), ("system", JAILBREAK), ("user", "Привет")]
    assert not any("Gemini ›" in text for _, text in sent)


async def test_a_big_book_does_not_push_the_high_order_jailbreak_out(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    await import_world(client, world_with_jailbreak(filler=12))  # far more than the 2000-token budget
    await client.put("/api/settings", json={"lorebooks": {"budget_tokens": 2000, "budget_percent": 0}})
    fake_llm.queue("ok")
    await send(client, ctx, chat_id, "Привет")
    texts = [m["content"] for m in fake_llm.requests[-1]["messages"]]
    assert JAILBREAK in texts
    reply = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    saw = (await client.get(f"/api/messages/{reply['id']}/prompt")).json()
    assert saw["meta"]["dropped"]["lore"]  # the filler was cut, and «Что видела модель» says so


def settings_json() -> dict:
    """A trimmed SillyTavern settings.json: the global scripts live in extension_settings.regex."""
    return {
        "username": "User",
        "power_user": {"theme": "Default"},
        "extension_settings": {
            "regex": [
                {"id": "a", "scriptName": "jailbreakuser", "findRegex": "/ /g", "replaceString": BLANK,
                 "placement": [1], "disabled": False, "markdownOnly": False, "promptOnly": True, "runOnEdit": True,
                 "substituteRegex": 0, "minDepth": None, "maxDepth": None, "trimStrings": []},
                {"id": "b", "scriptName": "JailbreakAI", "findRegex": f"/{BLANK}/g", "replaceString": " ",
                 "placement": [2, 6], "disabled": False, "markdownOnly": True, "promptOnly": False,
                 "runOnEdit": True, "substituteRegex": 0, "minDepth": None, "maxDepth": None, "trimStrings": []},
            ],
            "memory": {"enabled": False},
        },
    }


async def test_st_settings_json_brings_its_global_scripts_into_the_global_list(client, ctx, fake_llm):
    regex_service._cache.clear()
    _, chat_id = await setup(client)
    response = await client.post("/api/regex/import", json={"scope": "global", "scripts": settings_json()})
    assert response.status_code == 201, response.text
    made = response.json()["imported"]
    assert [r["name"] for r in made] == ["jailbreakuser", "JailbreakAI"]
    assert all(r["approved"] and r["enabled"] for r in made)  # global: on at once
    fake_llm.queue("ok")
    await send(client, ctx, chat_id, "два слова")
    assert f"два{BLANK}слова" in [m["content"] for m in fake_llm.requests[-1]["messages"]]
    # only the scripts the page sends from a big file work the same
    trimmed = {"extension_settings": {"regex": settings_json()["extension_settings"]["regex"][:1]}}
    assert (await client.post("/api/regex/import", json={"scope": "global", "scripts": trimmed})).status_code == 201
    empty = await client.post("/api/regex/import", json={"scope": "global", "scripts": {"extension_settings": {}}})
    assert empty.status_code == 400 and "общих регекс-скриптов" in empty.json()["error"]["message"]


async def test_the_chat_summary_names_rules_that_wait_for_allow(client, ctx):
    ann, chat_id = await setup(client)
    script = settings_json()["extension_settings"]["regex"][0]
    regex_service.import_rules(ctx, [script], "character", ann["id"], approved=False)
    summary = (await client.get(f"/api/regex/summary?chat_id={chat_id}")).json()
    assert summary["pending"] == 1
    assert summary["pending_lists"] == [{"scope": "character", "owner": ann["id"], "count": 1}]
    await client.post("/api/regex/approve", json={"scope": "character", "owner": ann["id"]})
    assert (await client.get(f"/api/regex/summary?chat_id={chat_id}")).json()["pending"] == 0


async def test_the_last_reasoning_goes_back_in_think_when_the_model_wrote_think(client, ctx, fake_llm):
    _, chat_id = await setup(client)
    st_file = {"prompts": [{"identifier": "main", "content": "Main."}, {"identifier": "chatHistory", "marker": True}],
               "prompt_order": [{"character_id": 100001, "order": [{"identifier": "main", "enabled": True},
                                                                   {"identifier": "chatHistory", "enabled": True}]}]}
    made = await client.post("/api/presets/import/st?name=st.json", content=json.dumps(st_file).encode())
    assert made.status_code == 201, made.text
    preset_id = made.json()["id"]
    await client.put(f"/api/chats/{chat_id}/meta", json={"preset_id": preset_id})
    assert preset_service.resolve(ctx, ctx.chats.get_chat(chat_id))["id"] == preset_id
    fake_llm.queue("<think>my plan</think>The reply.")
    await send(client, ctx, chat_id, "first")
    reply = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    assert reply["content"] == "The reply." and reply["swipe_meta"]["reasoning_tag"] == "think"
    fake_llm.queue("next")
    await send(client, ctx, chat_id, "second")
    assert "<think>\nmy plan\n</think>\n\nThe reply." in [m["content"] for m in fake_llm.requests[-1]["messages"]]
