"""Instruct templates (stage 6в): rendering, choosing by model name, SillyTavern files both ways."""

from __future__ import annotations

import pytest

from tavern.core import templates as T
from tavern.providers.base import ChatMessage

TALK = [ChatMessage("system", "Be Ann."), ChatMessage("user", "Hi"), ChatMessage("assistant", "Hello"),
        ChatMessage("user", "How are you?")]


def test_chatml_render_opens_the_reply():
    text, stops = T.render(T.BUILTIN["chatml"], TALK)
    assert text == ("<|im_start|>system\nBe Ann.<|im_end|>\n<|im_start|>user\nHi<|im_end|>\n"
                    "<|im_start|>assistant\nHello<|im_end|>\n<|im_start|>user\nHow are you?<|im_end|>\n"
                    "<|im_start|>assistant\n")
    assert stops == ["<|im_end|>", "<|im_start|>user"]


def test_continue_leaves_the_last_reply_open():
    text, _ = T.render(T.BUILTIN["llama3"], [*TALK, ChatMessage("assistant", "I am")])
    assert text.endswith("<|start_header_id|>assistant<|end_header_id|>\n\nI am")
    assert text.startswith("<|begin_of_text|><|start_header_id|>system")


def test_model_without_system_role_gets_it_as_user_turn():
    text, _ = T.render(T.BUILTIN["gemma"], TALK)
    assert text.startswith("<bos><start_of_turn>user\nBe Ann.<end_of_turn>\n<start_of_turn>user\nHi")


def test_story_string_keeps_only_the_system_part():
    template = {**T.BUILTIN["alpaca"], "chat_start": "***\n",
                "story_string": "{{#if system}}{{system}}\n{{/if}}{{#if description}}{{description}}{{/if}}{{persona}}"}
    text, _ = T.render(template, TALK)
    assert text.startswith("Be Ann.\n\n***\n### Instruction:\nHi")


@pytest.mark.parametrize("model, expected", [
    ("Qwen2.5-14B-Instruct-Q4_K_M.gguf", "chatml"), ("meta-llama/Llama-3.1-8B-Instruct", "llama3"),
    ("Mistral-Nemo-12B", "mistral"), ("gemma-3-27b-it", "gemma"), ("some-unknown-model", T.FALLBACK),
])
def test_detect_by_model_name(model, expected):
    assert T.detect(model, T.BUILTIN) == expected


def test_validate_and_bad_values():
    assert T.validate({"name": "x"})["story_string"] == "{{system}}"
    for bad in ({}, {"name": "x", "models": "("}, {"name": "x", "user_prefix": 5}, {"name": "x", "stop": "s"}):
        with pytest.raises(ValueError):
            T.validate(bad)


def test_sillytavern_files_both_ways():
    instruct = {"name": "My ChatML", "input_sequence": "<|im_start|>user", "input_suffix": "<|im_end|>",
                "output_sequence": "<|im_start|>assistant", "output_suffix": "<|im_end|>",
                "system_sequence": "<|im_start|>system", "system_suffix": "<|im_end|>", "wrap": True,
                "stop_sequence": "<|im_end|>", "activation_regex": "qwen", "system_same_as_user": False}
    context = {"name": "Default", "chat_start": "***", "example_separator": "***",
               "story_string": "{{#if system}}{{system}}\n{{/if}}{{#if wiBefore}}{{wiBefore}}{{/if}}"}
    ours = T.from_st(context, T.from_st(instruct))
    assert ours["name"] == "My ChatML" and ours["models"] == "qwen"
    assert ours["user_prefix"] == "<|im_start|>user\n" and ours["user_suffix"] == "\n<|im_end|>"
    assert ours["chat_start"] == "***\n" and ours["stop"] == ["<|im_end|>"]
    text, _ = T.render(ours, TALK[:2])
    assert text == ("<|im_start|>system\nBe Ann.\n<|im_end|>***\n<|im_start|>user\nHi\n<|im_end|>"
                    "<|im_start|>assistant\n")
    back = T.to_st(ours)
    assert back["instruct"]["input_sequence"] == "<|im_start|>user\n" and back["instruct"]["activation_regex"] == "qwen"
    assert back["context"]["chat_start"] == "***" and "{{system}}" in back["context"]["story_string"]
    again = T.from_st(back["context"], T.from_st(back["instruct"]))
    assert T.render(again, TALK) == T.render(ours, TALK)
