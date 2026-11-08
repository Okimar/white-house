"""Meaning-search threshold: every vector service has its own default until the user sets a number."""

from __future__ import annotations

from tavern.providers import catalog


async def conn(client, **fields):
    body = {"name": "c", "base_url": "http://llm/v1", "model": "m", **fields}
    return (await client.post("/api/connections", json=body)).json()



def test_catalog_knows_cohere_and_voyage_by_service_id_or_by_host():
    assert catalog.service_threshold({"service": "cohere", "base_url": "http://my-proxy/v1"}) == ("Cohere", 0.30)
    assert catalog.service_threshold({"service": "", "base_url": "https://api.cohere.ai/compatibility/v1"})[1] == 0.30
    assert catalog.service_threshold({"base_url": "https://api.voyageai.com/v1/"})[0] == "Voyage AI"
    assert catalog.service_threshold({"service": "ollama", "base_url": "http://127.0.0.1:11434/v1"}) is None


async def test_unknown_service_keeps_the_generic_threshold(client, ctx):
    from tavern.services import character_tree
    c = await conn(client)
    assert c["meaning_threshold"] is None
    await client.put("/api/settings", json={"embedding_connection": c["id"]})
    assert character_tree.retrieval_settings(ctx)["threshold"] == catalog.DEFAULT_THRESHOLD == 0.55


async def test_picking_a_known_service_switches_the_default(client, ctx):
    from tavern.services import character_tree
    c = await conn(client, service="cohere", base_url="https://api.cohere.ai/compatibility/v1")
    assert c["meaning_threshold"] == {"service": "Cohere", "value": 0.30}
    await client.put("/api/settings", json={"embedding_connection": c["id"]})
    assert character_tree.retrieval_settings(ctx)["threshold"] == 0.30


async def test_a_hand_set_threshold_is_never_overwritten(client, ctx):
    from tavern.services import character_tree
    c = await conn(client, service="cohere", base_url="https://api.cohere.ai/compatibility/v1")
    await client.put("/api/settings", json={"embedding_connection": c["id"],
                                            "retrieval": {"threshold": 0.7, "threshold_manual": True}})
    assert character_tree.retrieval_settings(ctx)["threshold"] == 0.7
