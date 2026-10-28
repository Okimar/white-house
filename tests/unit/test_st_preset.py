"""SillyTavern Chat Completion preset conversion. The fixture is written by hand from the file shape."""

from __future__ import annotations

import pytest

from tavern.core.presets import PresetError
from tavern.formats.st_preset import from_st, to_st

ST_PRESET = {
    "temperature": 1.1,
    "top_p": 0.95,
    "top_k": 0,
    "frequency_penalty": 0,
    "presence_penalty": 0.2,
    "openai_max_context": 120000,
    "openai_max_tokens": 900,
    "impersonation_prompt": "[Write as {{user}}]",
    "wrap_in_quotes": False,
    "prompts": [
        {"identifier": "main", "name": "Main Prompt", "system_prompt": True, "role": "system",
         "content": "You are {{char}}."},
        {"identifier": "nsfw", "name": "Auxiliary", "system_prompt": True, "role": "system", "content": "Aux."},
        {"identifier": "chatHistory", "name": "Chat History", "system_prompt": True, "marker": True},
        {"identifier": "dialogueExamples", "name": "Examples", "system_prompt": True, "marker": True},
        {"identifier": "charDescription", "name": "Char Description", "system_prompt": True, "marker": True},
        {"identifier": "charPersonality", "name": "Char Personality", "system_prompt": True, "marker": True},
        {"identifier": "scenario", "name": "Scenario", "system_prompt": True, "marker": True},
        {"identifier": "jailbreak", "name": "Post-History", "system_prompt": True, "role": "system",
         "content": "Stay in character."},
        {"identifier": "1a2b-3c", "name": "Reminder", "role": "user", "content": "Remember the plot.",
         "injection_position": 1, "injection_depth": 2},
        {"identifier": "mystery", "name": "Unknown marker", "marker": True},
    ],
    "prompt_order": [
        {"character_id": 100000, "order": [{"identifier": "main", "enabled": True}]},
        {"character_id": 100001, "order": [
            {"identifier": "main", "enabled": True},
            {"identifier": "nsfw", "enabled": False},
            {"identifier": "charDescription", "enabled": True},
            {"identifier": "charPersonality", "enabled": True},
            {"identifier": "scenario", "enabled": True},
            {"identifier": "dialogueExamples", "enabled": True},
            {"identifier": "mystery", "enabled": True},
            {"identifier": "chatHistory", "enabled": True},
            {"identifier": "1a2b-3c", "enabled": True},
            {"identifier": "jailbreak", "enabled": True},
        ]},
    ],
}


def test_import_maps_blocks_order_and_settings():
    name, data = from_st(ST_PRESET, "My preset")
    assert name == "My preset"
    assert [(b["kind"], b["enabled"]) for b in data["blocks"]] == [
        ("main", True), ("text", False), ("cast", True), ("examples", True), ("history", True),
        ("text", True), ("post_history", True),
    ]
    reminder = data["blocks"][5]
    assert (reminder["role"], reminder["position"], reminder["depth"]) == ("user", "depth", 2)
    assert data["params"] == {"temperature": 1.1, "top_p": 0.95, "presence_penalty": 0.2, "max_tokens": 900}
    assert data["max_context_tokens"] == 120000
    assert data["prompts"]["impersonate"] == "[Write as {{user}}]"
    assert data["st"] == {"wrap_in_quotes": False}


def test_export_round_trips_through_import():
    _, data = from_st(ST_PRESET)
    exported = to_st(data)
    assert exported["wrap_in_quotes"] is False
    order = [o["identifier"] for o in exported["prompt_order"][0]["order"]]
    assert order[:5] == ["main", "nsfw", "charDescription", "charPersonality", "scenario"]
    _, again = from_st(exported)
    assert again["blocks"] == data["blocks"] and again["params"] == data["params"]


def test_prefill_reasoning_and_card_formats_come_through():
    obj = {**ST_PRESET, "assistant_prefill": "Sure, continuing:", "reasoning_effort": "high",
           "personality_format": "[{{char}}'s personality: {{personality}}]", "scenario_format": "{{scenario}}"}
    _, data = from_st(obj)
    assert data["params"]["reasoning_effort"] == "high"
    assert data["prompts"]["prefill"] == "Sure, continuing:"
    assert data["prompts"]["personality_format"] == "[{{char}}'s personality: {{personality}}]"
    exported = to_st(data)
    assert exported["assistant_prefill"] == "Sure, continuing:" and exported["reasoning_effort"] == "high"
    auto = from_st({**ST_PRESET, "reasoning_effort": "auto"})[1]
    assert "reasoning_effort" not in auto["params"]


def test_cast_block_keeps_its_id_whatever_marker_comes_first():
    order = ST_PRESET["prompt_order"][1]["order"]
    scenario_first = [o for o in order if o["identifier"] == "scenario"] + [
        o for o in order if o["identifier"] != "scenario"]
    obj = {**ST_PRESET, "prompt_order": [{"character_id": 100001, "order": scenario_first}]}
    _, data = from_st(obj)
    assert from_st(to_st(data))[1]["blocks"] == data["blocks"]


@pytest.mark.parametrize("obj", [[], {"temp": 0.7, "rep_pen": 1.1}, {"prompts": "x"}])
def test_not_a_chat_completion_preset_is_refused(obj):
    with pytest.raises(PresetError):
        from_st(obj)
