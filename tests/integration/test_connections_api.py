"""Connections over HTTP (stage 6а): services, several keys with proxies, model facts, trial requests,
and a chat answered through Gemini's own protocol."""

from __future__ import annotations

import asyncio

from tavern.providers import keys as K


async def make(client, **body) -> dict:
    response = await client.post("/api/connections", json={"name": "c", **body})
    assert response.status_code == 201, response.text
    return response.json()


async def test_catalog_lists_services_and_settings(client):
    data = (await client.get("/api/connections/catalog")).json()
    ids = {s["id"] for s in data["services"]}
    assert {"gemini", "openrouter", "deepseek", "koboldcpp", "ollama", "custom"} <= ids
    assert {p["id"] for p in data["settings"]["params"]} >= {"temperature", "thinking_budget", "retries"}


async def test_service_fills_kind_and_address(client):
    conn = await make(client, service="gemini", api_key="g")
    assert (conn["kind"], conn["service"]) == ("gemini", "gemini")
    assert conn["base_url"] == "https://generativelanguage.googleapis.com/v1beta"
    assert (await make(client, service="custom", base_url="http://x/v1"))["kind"] == "openai"
    bad = await client.post("/api/connections", json={"name": "c", "service": "custom"})
    assert bad.status_code == 400


async def test_bad_settings_are_refused(client):
    response = await client.post("/api/connections", json={
        "name": "c", "base_url": "http://x/v1", "params": {"temperature": 9}})
    assert response.status_code == 400 and "Температура" in response.json()["error"]["message"]


async def test_keys_and_proxies_never_come_back(client, ctx):
    conn = await make(client, base_url="http://x/v1", keys=[
        {"label": "main", "key": "sk-one", "proxy": "socks5://bob:pw@10.0.0.5:1080"}])
    key_id = conn["keys"][0]["id"]
    second = (await client.post(f"/api/connections/{conn['id']}/keys", json={"key": "sk-two"})).json()
    assert [k["label"] for k in second["keys"]] == ["main", "Ключ 2"]
    # Empty key keeps the stored one; empty proxy removes the proxy.
    updated = (await client.put(f"/api/connections/{conn['id']}/keys/{key_id}",
                                json={"key": "", "proxy": "", "label": "renamed"})).json()
    assert updated["keys"][0] == {"id": key_id, "label": "renamed", "has_key": True, "has_proxy": False,
                                  "proxy": ""}
    ids = [k["id"] for k in updated["keys"]]
    reordered = (await client.put(f"/api/connections/{conn['id']}/keys/order", json={"ids": ids[::-1]})).json()
    assert [k["id"] for k in reordered["keys"]] == ids[::-1]
    assert (await client.put(f"/api/connections/{conn['id']}/keys/order", json={"ids": ids[:1]})).status_code == 400
    bad_proxy = await client.post(f"/api/connections/{conn['id']}/keys", json={"key": "k", "proxy": "ftp://x"})
    assert bad_proxy.status_code == 400
    listed = (await client.get("/api/connections")).text
    for secret in ("sk-one", "sk-two", "bob", ":pw@"):
        assert secret not in listed
    assert [k["key"] for k in K.load_keys(ctx.secrets, conn["id"])] == ["sk-two", "sk-one"]
    await client.delete(f"/api/connections/{conn['id']}/keys/{key_id}")
    await client.delete(f"/api/connections/{conn['id']}")
    assert K.load_keys(ctx.secrets, conn["id"]) == []


async def test_old_single_key_still_reads(client, ctx):
    conn = ctx.connections.create(name="old", kind="openai", base_url="http://x/v1")
    ctx.secrets.set(K.legacy_key_name(conn["id"]), "sk-old")
    (listed,) = (await client.get("/api/connections")).json()["connections"]
    assert listed["has_key"] and len(listed["keys"]) == 1
    await client.post(f"/api/connections/{conn['id']}/keys", json={"key": "sk-new"})
    assert ctx.secrets.get(K.legacy_key_name(conn["id"])) is None
    assert [k["key"] for k in K.load_keys(ctx.secrets, conn["id"])] == ["sk-old", "sk-new"]


async def test_inspect_remembers_model_facts(client, fake_llm):
    conn = await make(client, base_url="http://llm/v1", model="fake-model")
    assert (await client.get(f"/api/connections/{conn['id']}/model-info")).json()["models"] == []
    (model,) = (await client.post(f"/api/connections/{conn['id']}/inspect")).json()["models"]
    assert (model["context"], model["price_in"], model["price_out"]) == (32768, 1.0, 2.0)
    assert "top_k" in model["params"] and "min_p" not in model["params"]
    gem = await make(client, service="custom_gemini", base_url="http://llm/v1beta")
    (model,) = (await client.post(f"/api/connections/{gem['id']}/inspect")).json()["models"]
    assert model["id"] == "fake-model" and model["context"] == 1048576


async def test_probe_finds_refused_settings(client, fake_llm):
    fake_llm.rejected_params = {"min_p"}
    conn = await make(client, base_url="http://llm/v1", model="fake-model")
    data = (await client.post(f"/api/connections/{conn['id']}/probe", json={})).json()
    assert data["probe"]["min_p"] == "rejected" and data["probe"]["top_k"] == "ok"
    assert "thinking_budget" not in data["probe"]  # not a setting of this kind
    first = fake_llm.requests[0]
    assert first["max_tokens"] == 1 and "min_p" not in first  # the plain request goes first
    (stored,) = (await client.get(f"/api/connections/{conn['id']}/model-info")).json()["models"]
    assert stored["probe"]["min_p"] == "rejected"
    assert data["probe"]["structured"] == "rejected"  # the fake echoes: no JSON


async def test_probe_tells_whether_the_model_answers_in_a_shape(client, fake_llm):
    conn = await make(client, base_url="http://llm/v1", model="fake-model")
    fake_llm.queue("OK", '{"ok": true}')
    data = (await client.post(f"/api/connections/{conn['id']}/probe", json={"names": ["structured"]})).json()
    assert data["probe"] == {"structured": "ok"}
    assert fake_llm.requests[-1]["response_format"]["type"] == "json_schema"
    fake_llm.rejected_params = {"response_format"}
    fake_llm.queue("OK", '{"ok": false}')
    data = (await client.post(f"/api/connections/{conn['id']}/probe", json={"names": ["structured"]})).json()
    assert data["probe"]["structured"] == "words"


async def test_probe_reports_a_broken_connection(client, fake_llm):
    fake_llm.key_status = {"sk-bad": 401}
    conn = await make(client, base_url="http://llm/v1", model="fake-model", api_key="sk-bad")
    response = await client.post(f"/api/connections/{conn['id']}/probe", json={})
    assert response.status_code == 502 and "sk-bad" not in response.text


async def test_chat_through_gemini_with_spent_first_key(client, ctx, fake_llm):
    fake_llm.key_status = {"spent": 429}
    fake_llm.queue("Hello from Gemini")
    conn = await make(client, service="custom_gemini", base_url="http://llm/v1beta", model="fake-model",
                      keys=[{"key": "spent"}, {"key": "fresh"}],
                      params={"temperature": 0.4, "min_p": 0.2, "thinking_budget": 64, "safety_off": True})
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    char = (await client.post("/api/characters", json={"name": "Ann", "description": "Ann is kind."})).json()
    chat = (await client.post("/api/chats", json={"character_id": char["id"]})).json()["chat"]
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "hi"})).json()["run_id"]
    run = ctx.runs.get(run_id)
    await asyncio.wait([run.task], timeout=10)
    messages = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"]
    assert messages[-1]["content"] == "Hello from Gemini"
    sent = fake_llm.gemini_requests[-1]
    assert "Ann is kind." in sent["systemInstruction"]["parts"][0]["text"]
    config = sent["generationConfig"]
    assert config["temperature"] == 0.4 and config["thinkingConfig"] == {"thinkingBudget": 64}
    assert "min_p" not in str(sent) and sent["safetySettings"]
    assert fake_llm.seen_keys == ["spent", "fresh"]


async def gemini_chat(client, ctx, fake_llm, **params) -> dict:
    conn = await make(client, service="custom_gemini", base_url="http://llm/v1beta", model="fake-model",
                      params=params)
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return (await client.post("/api/chats", json={})).json()["chat"]


async def wait_run(ctx, run_id: str) -> None:
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)


async def test_thinking_is_streamed_and_kept_apart(client, ctx, fake_llm):
    fake_llm.queue("The answer")
    chat = await gemini_chat(client, ctx, fake_llm, include_thoughts=True)
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "q"})).json()["run_id"]
    await wait_run(ctx, run_id)
    events = (await client.get(f"/api/runs/{run_id}/events")).text
    assert '"type": "reasoning"' in events or '"type":"reasoning"' in events
    reply = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert reply["content"] == "The answer"
    assert reply["swipe_meta"]["reasoning"] == "Thinking about it."


async def test_pictures_go_with_the_message(client, ctx, fake_llm):
    from tavern.devtools.fake_llm import _pattern_png

    png = _pattern_png("cat")
    asset = (await client.post("/api/assets?name=cat.png", content=png)).json()
    chat = await gemini_chat(client, ctx, fake_llm)
    missing = await client.post(f"/api/chats/{chat['id']}/send", json={"text": "x", "images": ["nope.png"]})
    assert missing.status_code == 404
    run_id = (await client.post(f"/api/chats/{chat['id']}/send",
                                json={"text": "", "images": [asset["name"]]})).json()["run_id"]
    await wait_run(ctx, run_id)
    user = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-2]
    assert user["role"] == "user" and user["swipe_meta"]["images"] == [asset["name"]]
    parts = fake_llm.gemini_requests[-1]["contents"][-1]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "image/png"
    # A picture deleted from the library later is skipped, the chat still works.
    await client.delete(f"/api/assets/{asset['name']}")
    run_id = (await client.post(f"/api/chats/{chat['id']}/send", json={"text": "again"})).json()["run_id"]
    await wait_run(ctx, run_id)
    assert all("inlineData" not in p for c in fake_llm.gemini_requests[-1]["contents"] for p in c["parts"])
