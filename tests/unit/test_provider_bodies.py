"""Wave 4 of the SillyTavern parity plan: request bodies of the direct Gemini and Claude kinds and the
OpenAI-compatible extras (docs/ST_PARITY.md §9, §11). Every expected body is written by hand from the plain-words
rule; the preset's SillyTavern keys reach the providers through core.presets.provider_extras."""

from __future__ import annotations

import pytest

from tavern.core.presets import ORIGIN_ST, default_preset, provider_extras
from tavern.providers import params as P
from tavern.providers.anthropic import AnthropicProvider
from tavern.providers.base import ChatMessage as M
from tavern.providers.base import GenerationRequest as R
from tavern.providers.gemini import GeminiProvider
from tavern.providers.openai_compat import OpenAICompatProvider, extra_headers

TWO_SYSTEM = [M("system", "S1"), M("system", "S2"), M("user", "u"), M("assistant", "a")]


def gemini(**kw):
    return GeminiProvider("http://g/v1beta").build_body(R(**kw))


def claude(**kw):
    return AnthropicProvider("http://a/v1").build_body(R(**kw))


def openai(stream=False, **kw):
    return OpenAICompatProvider("http://o/v1").build_body(R(**kw), stream=stream)


# Gemini


def test_gemini_system_texts_go_to_system_instruction_one_part_each():
    body = gemini(messages=TWO_SYSTEM, model="gemini-2.5-pro")
    assert body["systemInstruction"] == {"parts": [{"text": "S1"}, {"text": "S2"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "u"}]},
                                {"role": "model", "parts": [{"text": "a"}]}]


@pytest.mark.parametrize(("model", "extra"), [("gemini-2.5-pro", {"use_sysprompt": False}), ("gemma-3-27b", {}),
                                              ("gemini-2.5-flash-image", {})])
def test_gemini_without_the_system_field_sends_system_texts_as_the_users(model, extra):
    body = gemini(messages=TWO_SYSTEM, model=model, extra=extra)
    assert "systemInstruction" not in body
    assert body["contents"][0] == {"role": "user", "parts": [{"text": "S1\n\nS2\n\nu"}]}


def test_gemini_sampling_rules():
    body = gemini(messages=[M("user", "u")], model="gemini-2.5-flash",
                  stop=["a", "b", "", "c", "d", "e", "f", "x" * 20],
                  extra={"seed": 5, "top_k": 0, "frequency_penalty": 0.3, "presence_penalty": 0.1})
    config = body["generationConfig"]
    assert config["candidateCount"] == 1 and config["seed"] == 5
    assert config["stopSequences"] == ["a", "b", "c", "d", "e"]  # 1–16 characters, at most five
    assert "topK" not in config  # 0 is «not set»
    assert not {"frequencyPenalty", "presencePenalty"} & set(config)  # Gemini gets no penalties
    assert {s["threshold"] for s in body["safetySettings"]} == {"OFF"}  # filters off by default
    assert config["thinkingConfig"] == {"includeThoughts": True}
    assert "seed" not in gemini(messages=[M("user", "u")], model="m", extra={"seed": -1})["generationConfig"]


def test_gemini_3_takes_a_thinking_level_max_is_high():
    body = gemini(messages=[M("user", "u")], model="gemini-3-pro", extra={"reasoning_effort": "max"})
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"


# Claude


def test_claude_system_texts_are_blocks_or_user_text():
    on = claude(messages=TWO_SYSTEM, model="claude-sonnet-4-5")
    assert on["system"] == [{"type": "text", "text": "S1"}, {"type": "text", "text": "S2"}]
    off = claude(messages=TWO_SYSTEM, model="claude-sonnet-4-5", extra={"use_sysprompt": False})
    assert "system" not in off
    assert off["messages"][0] == {"role": "user", "content": [
        {"type": "text", "text": "S1"}, {"type": "text", "text": "S2"}, {"type": "text", "text": "u"}]}


def test_claude_empty_text_is_a_zero_width_space_and_an_empty_chat_gets_started():
    body = claude(messages=[M("user", ""), M("assistant", "x")], model="claude-sonnet-4-5")
    assert body["messages"][0]["content"] == [{"type": "text", "text": "​"}]
    only_system = claude(messages=[M("system", "rules")], model="claude-sonnet-4-5")
    assert only_system["messages"][0]["content"] == [{"type": "text", "text": "Let's get started."}]


def test_claude_pictures_of_the_model_go_to_the_next_user_turn():
    body = claude(messages=[M("user", "q"), M("assistant", "see", images=(("image/png", "QQ"),)), M("user", "ok")],
                  model="claude-sonnet-4-5")
    assert body["messages"][1]["content"] == [{"type": "text", "text": "see"}]
    assert body["messages"][2]["content"][0]["type"] == "image"


def test_claude_effort_is_a_budget_before_4_6_and_adaptive_from_4_6():
    old = claude(messages=[M("user", "q")], model="claude-sonnet-4-5", max_tokens=1000,
                 extra={"reasoning_effort": "medium"})
    assert old["thinking"]["type"] == "enabled" and old["thinking"]["budget_tokens"] >= 1024
    assert old["max_tokens"] > old["thinking"]["budget_tokens"]
    new = claude(messages=[M("user", "q")], model="claude-opus-4-6", extra={"reasoning_effort": "high"})
    assert new["thinking"] == {"type": "adaptive"} and new["output_config"] == {"effort": "high"}


def test_claude_prefill_is_trimmed_and_becomes_user_text_where_prefill_is_gone():
    old = claude(messages=[M("user", "q"), M("assistant", "Sure:  ")], model="claude-sonnet-4-5")
    assert old["messages"][-1] == {"role": "assistant", "content": [{"type": "text", "text": "Sure:"}]}
    new = claude(messages=[M("user", "q"), M("assistant", "Sure:")], model="claude-opus-4-6")
    assert new["messages"] == [{"role": "user", "content": [{"type": "text", "text": "q"},
                                                            {"type": "text", "text": "Sure:"}]}]


# OpenAI-compatible


def test_openai_custom_body_fields_seed_and_n():
    body = openai(messages=[M("user", "u")], model="m", top_p=0.9,
                  extra={"seed": 7, "n": 3, "exclude_body": "top_p", "include_body": '{"foo": 1}',
                         "use_sysprompt": True, "prompt_post_processing": ""})
    assert body == {"messages": [{"role": "user", "content": "u"}], "stream": False, "model": "m", "seed": 7,
                    "foo": 1}


@pytest.mark.parametrize(("mode", "roles"), [
    ("", ["system", "system", "user", "assistant"]),
    ("merge", ["system", "user", "assistant"]),
    ("semi", ["system", "user", "assistant"]),
    ("single", ["user"]),
])
def test_openai_prompt_post_processing(mode, roles):
    body = openai(messages=TWO_SYSTEM, model="m", extra={"prompt_post_processing": mode})
    assert [m["role"] for m in body["messages"]] == roles


def test_openai_extra_headers_never_carry_auth_or_host():
    headers = extra_headers('{"X-Title": "Tavern", "Authorization": "Bearer evil", "Host": "x", "Cookie": "c"}')
    assert headers == {"X-Title": "Tavern"}


def test_tavern_only_keys_are_never_fields_of_any_body():
    extra = {name: "x" for name in P.PRESET_EXTRAS} | {"use_sysprompt": True}
    for body in (openai(messages=[M("user", "u")], model="m", extra=dict(extra)),
                 gemini(messages=[M("user", "u")], model="m", extra=dict(extra)),
                 claude(messages=[M("user", "u")], model="m", extra=dict(extra))):
        flat = {**body, **body.get("generationConfig", {})}
        assert not set(P.PRESET_EXTRAS) & set(flat)


# The preset's SillyTavern keys -> request options


def test_provider_extras_come_only_from_a_sillytavern_preset():
    assert provider_extras(default_preset()) == {}
    st = {**default_preset(), "origin": ORIGIN_ST, "st": {}}
    assert provider_extras(st) == {"use_sysprompt": False}  # SillyTavern's default: off
    st["st"] = {"use_sysprompt": True, "custom_prompt_post_processing": "strict", "custom_include_body": "",
                "custom_exclude_body": "top_p", "custom_include_headers": "X-A: 1"}
    assert provider_extras(st) == {"use_sysprompt": True, "prompt_post_processing": "strict",
                                   "exclude_body": "top_p", "include_headers": "X-A: 1"}
