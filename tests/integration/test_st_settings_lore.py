"""SillyTavern's settings.json brings its world info settings and global books along with its global regex scripts
(the owner's file, 2026-11-17: depth 1, recursion on with no step limit, 25 %, include names, character first)."""

from __future__ import annotations

from tavern.services.lorebooks import UNLIMITED_RECURSION, settings_from_st

OWNER_LIKE = {
    "world_info_settings": {
        "world_info": {"globalSelect": ["The Garden 1.5", "Сильваннета", "Missing Book"], "charLore": []},
        "world_info_depth": 1, "world_info_min_activations": 0, "world_info_min_activations_depth_max": 0,
        "world_info_budget": 25, "world_info_include_names": True, "world_info_recursive": True,
        "world_info_overflow_alert": False, "world_info_case_sensitive": False, "world_info_match_whole_words": True,
        "world_info_character_strategy": 1, "world_info_budget_cap": 0, "world_info_use_group_scoring": False,
        "world_info_max_recursion_steps": 0,
    },
    "extension_settings": {"regex": [{"scriptName": "Return Normal Spaces", "findRegex": "/⠀/g",
                                      "replaceString": " ", "placement": [2], "disabled": False}]},
}


def test_the_settings_map_to_ours():
    settings, names = settings_from_st(OWNER_LIKE)
    assert settings == {"scan_messages": 1, "min_activations": 0, "min_depth_max": 0, "budget_percent": 25,
                        "budget_cap": 0, "include_names": True, "group_scoring": False,
                        "recursion": UNLIMITED_RECURSION, "strategy": "character_first"}
    assert names == ["The Garden 1.5", "Сильваннета", "Missing Book"]
    limited = {"world_info_settings": {"world_info_recursive": True, "world_info_max_recursion_steps": 3}}
    assert settings_from_st(limited)[0]["recursion"] == 3
    assert settings_from_st({"world_info_settings": {"world_info_recursive": False}})[0]["recursion"] == 0
    assert settings_from_st({"extension_settings": {}}) is None


async def test_importing_settings_json_brings_the_lorebook_settings_and_global_books(client):
    garden = (await client.post("/api/lorebooks", json={"name": "The Garden 1.5"})).json()
    other = (await client.post("/api/lorebooks", json={"name": "сильваннета"})).json()
    response = await client.post("/api/regex/import", json={"scope": "global", "scripts": OWNER_LIKE})
    assert response.status_code == 201, response.text
    lore = response.json()["lorebooks"]
    assert lore["books_on"] == ["The Garden 1.5", "Сильваннета"] and lore["books_missing"] == ["Missing Book"]
    settings = (await client.get("/api/lorebooks")).json()["settings"]
    assert (settings["scan_messages"], settings["recursion"], settings["strategy"]) == (1, UNLIMITED_RECURSION,
                                                                                       "character_first")
    uses = (await client.get("/api/lorebook-uses?scope=global")).json()["lorebook_ids"]
    assert uses == [garden["id"], other["id"]]
    assert [r["name"] for r in response.json()["imported"]] == ["Return Normal Spaces"]
