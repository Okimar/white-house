"""Instruct templates over HTTP and a chat in text mode (stage 6в)."""

from __future__ import annotations

import asyncio


async def test_list_edit_and_protect_builtins(client):
    listed = (await client.get("/api/templates")).json()["templates"]
    assert {"chatml", "llama3", "mistral", "gemma"} <= {t["id"] for t in listed}
    assert (await client.put("/api/templates/chatml", json={"name": "x"})).status_code == 400
    assert (await client.delete("/api/templates/chatml")).status_code == 400
    mine = (await client.post("/api/templates", json={"name": "Мой", "user_prefix": "> "})).json()
    assert mine["builtin"] is False and mine["user_prefix"] == "> "
    edited = (await client.put(f"/api/templates/{mine['id']}", json={"name": "Мой 2", "models": "mymodel"})).json()
    assert edited["name"] == "Мой 2" and edited["user_prefix"] == ""
    assert (await client.post("/api/templates", json={"name": ""})).status_code == 400
    await client.delete(f"/api/templates/{mine['id']}")
    assert mine["id"] not in {t["id"] for t in (await client.get("/api/templates")).json()["templates"]}


async def test_import_st_pair_merge_and_export(client):
    instruct = {"name": "Alp", "input_sequence": "### I:", "output_sequence": "### R:", "system_sequence": "",
                "stop_sequence": "### I:", "wrap": True}
    made = (await client.post("/api/templates/import", json=instruct)).json()
    assert made["user_prefix"] == "### I:\n"
    merged = (await client.post(f"/api/templates/import?into={made['id']}",
                                json={"name": "ctx", "story_string": "{{system}}", "chat_start": "START"})).json()
    assert merged["id"] == made["id"] and merged["chat_start"] == "START\n" and merged["user_prefix"] == "### I:\n"
    exported = await client.get(f"/api/templates/{made['id']}/export?format=st-instruct")
    assert exported.json()["input_sequence"] == "### I:\n" and "attachment" in exported.headers["content-disposition"]
    ours = (await client.get("/api/templates/llama3/export")).json()
    assert ours["bos"] == "<|begin_of_text|>" and "id" not in ours
    again = (await client.post("/api/templates/import", json=ours)).json()
    assert again["name"] == "Llama 3" and again["builtin"] is False
    assert (await client.post("/api/templates/import", json={"nonsense": 1})).status_code == 400


async def test_preview(client):
    shown = (await client.post("/api/templates/preview", json={"id": "auto", "model": "gemma-2-9b"})).json()
    assert shown["name"].startswith("Gemma") and shown["text"].startswith("<bos><start_of_turn>user")
    draft = (await client.post("/api/templates/preview",
                               json={"template": {"name": "d", "user_prefix": "U:", "assistant_prefix": "A:"}})).json()
    assert draft["text"].endswith("A:")


async def test_chat_in_text_mode_uses_the_template(client, ctx, fake_llm):
    fake_llm.queue("Fine, thanks")
    conn = (await client.post("/api/connections", json={
        "name": "local", "service": "koboldcpp", "base_url": "http://llm/v1", "model": "Qwen2.5-7B-Instruct",
        "params": {"api_mode": "text", "template": "auto", "stop": ["\nMe:"]}})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    char = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    chat = (await client.post("/api/chats", json={"character_id": char["id"]})).json()["chat"]
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "How are you?"})).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    assert (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]["content"] == "Fine, thanks"
    sent = fake_llm.text_requests[-1]
    assert sent["prompt"].startswith("<|im_start|>system\n") and "Ann is kind." in sent["prompt"]
    assert sent["prompt"].endswith("How are you?<|im_end|>\n<|im_start|>assistant\n")
    assert sent["stop"] == ["\nMe:", "<|im_end|>", "<|im_start|>user"] and "messages" not in sent
    assert "api_mode" not in sent and "template" not in sent
