"""The settings of a message's buttons: list or panel, and which stay on the panel in any mode."""

from __future__ import annotations


async def test_the_buttons_settings_are_kept(client):
    response = await client.put("/api/settings", json={"message_actions": "always", "message_pinned": ["edit", "notes"]})
    assert response.status_code == 200
    saved = (await client.get("/api/settings")).json()
    assert saved["message_actions"] == "always"
    assert saved["message_pinned"] == ["edit", "notes"]


async def test_nothing_pinned_is_kept_as_an_empty_list(client):
    assert (await client.put("/api/settings", json={"message_pinned": []})).status_code == 200
    assert (await client.get("/api/settings")).json()["message_pinned"] == []


async def test_the_pages_files_are_revalidated_so_an_update_never_mixes_old_and_new(client):
    response = await client.get("/main.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


async def test_unknown_values_are_refused(client):
    assert (await client.put("/api/settings", json={"message_actions": "sometimes"})).status_code == 400
    assert (await client.put("/api/settings", json={"message_pinned": ["fly"]})).status_code == 400
    assert (await client.put("/api/settings", json={"message_pinned": "edit"})).status_code == 400
