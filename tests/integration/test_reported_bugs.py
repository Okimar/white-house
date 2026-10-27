"""Regression tests for bugs reported from manual use: chat without a character, PNG export of a
character with a non-ASCII name, model list for an unsaved connection, local backend behind a proxy."""

from __future__ import annotations

from tavern.providers.openai_compat import OpenAICompatProvider, direct_connection


async def test_chat_without_character_accepts_null_character_id(client):
    # The browser sends {"character_id": null}; that used to be rejected as "must be of type str".
    response = await client.post("/api/chats", json={"character_id": None})
    assert response.status_code == 201, response.text
    assert response.json()["chat"]["character_id"] is None
    assert response.json()["messages"] == []


async def test_png_export_of_non_ascii_name(client):
    character = (await client.post("/api/characters", json={"name": "Аня Тестовая"})).json()
    response = await client.get(f"/api/characters/{character['id']}/export.png")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "filename*=UTF-8''%D0%90%D0%BD%D1%8F" in disposition
    assert disposition.isascii()


async def test_models_can_be_listed_before_saving_a_connection(client, fake_llm):
    response = await client.post("/api/connections/models", json={"base_url": "http://llm/v1", "api_key": "k"})
    assert response.status_code == 200, response.text
    assert response.json()["models"] == ["fake-model"]


async def test_model_probe_requires_an_address(client):
    response = await client.post("/api/connections/models", json={"base_url": " "})
    assert response.status_code == 400


def test_local_and_private_backends_bypass_the_system_proxy():
    assert direct_connection("http://127.0.0.1:7861/antigravity/v1")
    assert direct_connection("http://localhost:5001/v1")
    assert direct_connection("http://192.168.1.5:8080/v1")
    assert direct_connection("http://[::1]:5001/v1")


async def test_provider_for_local_backend_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    local = OpenAICompatProvider("http://127.0.0.1:7861/v1")
    try:
        assert local._client._trust_env is False
    finally:
        await local.aclose()
