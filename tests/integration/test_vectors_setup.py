"""Setting up meaning search (vectors): Voyage AI in the catalog, pasted endpoint addresses, «Проверить векторы»."""

from __future__ import annotations

from tavern.providers import catalog


async def make(client, **body) -> dict:
    response = await client.post("/api/connections", json={"name": "c", **body})
    assert response.status_code == 201, response.text
    return response.json()


def test_fix_address_strips_endpoint_tails():
    fix = catalog.fix_address
    assert fix("https://api.voyageai.com/v1/embeddings", "openai") == ("https://api.voyageai.com/v1", True)
    assert fix("http://x/v1/chat/completions/", "openai") == ("http://x/v1", True)
    assert fix("http://x/v1/", "openai") == ("http://x/v1/", False)
    assert fix("http://x/v1/embeddings", "gemini") == ("http://x/v1/embeddings", False)


def test_embedding_model_names():
    assert catalog.looks_like_embedding_model("voyage-4")
    assert catalog.looks_like_embedding_model("text-embedding-3-small")
    assert not catalog.looks_like_embedding_model("gemini-3.8-flash-high")


async def test_address_is_corrected_on_create_and_update(client):
    conn = await make(client, base_url="http://x/v1/embeddings")
    assert conn["base_url"] == "http://x/v1" and conn["address_fixed"] == "http://x/v1"
    plain = await make(client, base_url="http://x/v1")
    assert "address_fixed" not in plain
    saved = (await client.put(f"/api/connections/{plain['id']}", json={"base_url": "http://y/v1/chat/completions"})).json()
    assert saved["base_url"] == "http://y/v1" and saved["address_fixed"] == "http://y/v1"


async def test_voyage_has_a_built_in_model_list(client):
    voyage = {s["id"]: s for s in (await client.get("/api/connections/catalog")).json()["services"]}["voyage"]
    assert voyage["base_url"] == "https://api.voyageai.com/v1" and voyage["embeddings"]
    conn = await make(client, service="voyage", api_key="k")
    models = (await client.post(f"/api/connections/{conn['id']}/inspect")).json()["models"]  # no network
    assert "voyage-4" in {m["id"] for m in models}
    probed = (await client.post("/api/connections/models", json={"service": "voyage"})).json()
    assert "voyage-4-large" in probed["models"]
    listed = (await client.get(f"/api/connections/{conn['id']}/models")).json()
    assert "voyage-4-lite" in listed["models"]


async def test_embed_request_has_no_encoding_format(client, fake_llm):
    conn = await make(client, base_url="http://llm/v1", model="voyage-4")
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    await client.post("/api/vectors/test")
    assert fake_llm.embedding_requests[-1] == {"input": ["проверка"], "model": "voyage-4"}


async def test_vector_check_works_and_reports_errors(client, fake_llm):
    nothing = await client.post("/api/vectors/test")
    assert nothing.json()["error"]["code"] == "no_embedding_connection"
    conn = await make(client, base_url="http://llm/v1", model="voyage-4")
    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    ok = (await client.post("/api/vectors/test")).json()
    assert ok["ok"] and ok["model"] == "voyage-4" and ok["size"] > 0 and ok["ms"] >= 0
    fake_llm.failures.append((404, "Not Found"))
    bad = (await client.post("/api/vectors/test")).json()
    assert bad["ok"] is False and "404" in bad["error"] and "/v1" in bad["error"]


def test_cohere_addresses_become_the_compatible_root():
    fix = catalog.fix_address
    root = "https://api.cohere.ai/compatibility/v1"
    for pasted in ("https://api.cohere.com/v2", "https://api.cohere.com/v2/embed", "https://api.cohere.com/v1/",
                   "https://api.cohere.ai/v1", "https://api.cohere.ai/v2/embed"):
        assert fix(pasted, "openai") == (root, True), pasted
    assert fix(root, "openai") == (root, False)
    assert fix("https://api.cohere.com/v2", "gemini") == ("https://api.cohere.com/v2", False)
    assert catalog.looks_like_embedding_model("embed-v4.0")


async def test_cohere_has_a_built_in_embedding_list(client):
    cohere = {s["id"]: s for s in (await client.get("/api/connections/catalog")).json()["services"]}["cohere"]
    assert cohere["base_url"] == "https://api.cohere.ai/compatibility/v1" and cohere["embeddings"]
    conn = await make(client, service="cohere", api_key="k", base_url="https://api.cohere.com/v2/embed")
    assert conn["base_url"] == cohere["base_url"] and conn["address_fixed"] == cohere["base_url"]
    models = {m["id"] for m in (await client.post(f"/api/connections/{conn['id']}/inspect")).json()["models"]}
    assert {"embed-v5.0-pro", "embed-v5.0-fast", "embed-v4.0", "embed-multilingual-v3.0"} <= models and not any(m.startswith("command") for m in models)
