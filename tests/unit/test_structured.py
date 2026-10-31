"""Structured output (stage 6ж): the schema check, retries with the problems, words-only fallback,
Claude's forced tool."""

from __future__ import annotations

import json

import httpx
import pytest

from tavern.core.llm import LLMClient, StructuredOutputError
from tavern.core.structured import problems
from tavern.devtools.fake_llm import FakeLLM
from tavern.providers.anthropic import AnthropicProvider, _tool_answer
from tavern.providers.base import ChatMessage
from tavern.providers.openai_compat import OpenAICompatProvider

PERSON = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "age": {"type": "integer", "minimum": 0},
        "mood": {"enum": ["happy", "sad"]},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2, "uniqueItems": True},
        "pet": {"$ref": "#/$defs/pet"},
    },
    "required": ["name", "age"],
    "additionalProperties": False,
    "$defs": {"pet": {"anyOf": [{"type": "null"}, {"type": "object", "required": ["kind"]}]}},
}
USER = [ChatMessage("user", "Who are you?")]


def fake_client(fake: FakeLLM) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")


def openai(fake: FakeLLM) -> LLMClient:
    return LLMClient(OpenAICompatProvider("http://llm/v1", client=fake_client(fake)), model="m")


def test_a_fitting_value_has_no_problems():
    assert problems({"name": "Аня", "age": 20, "mood": "happy", "tags": ["a"], "pet": None}, PERSON) == []
    assert problems({"name": "Аня", "age": 20.0, "pet": {"kind": "cat"}}, PERSON) == []


def test_problems_name_the_place():
    found = problems({"name": "", "age": -1, "mood": "angry", "tags": ["a", "a", "b"], "pet": {}, "x": 1}, PERSON)
    assert found == [
        "$.name: at least 1 characters", "$.age: at least 0", '$.mood: must be one of ["happy", "sad"]',
        "$.tags: at most 2 items", "$.tags: items must not repeat", "$.pet: fits none of the allowed shapes",
        "$.x: not allowed here",
    ]
    assert problems({"age": True}, PERSON) == ["$.name: required", "$.age: must be integer, got boolean"]
    assert problems([1, "2"], {"type": "array", "items": {"type": "number"}}) == ["$[1]: must be number, got string"]
    assert problems("x", {"type": ["string", "null"], "pattern": "^[a-z]+$"}) == []


async def test_the_model_is_told_what_did_not_fit_and_tries_again():
    fake = FakeLLM()
    fake.queue('{"name": "Аня"}', 'Here: {"name": "Аня", "age": 20}')
    assert await openai(fake).generate_json(USER, schema=PERSON) == {"name": "Аня", "age": 20}
    first, second = fake.requests
    assert first["response_format"]["json_schema"]["schema"] == PERSON
    assert "JSON Schema" in first["messages"][-1]["content"]  # said in words as well
    assert second["messages"][-2] == {"role": "assistant", "content": '{"name": "Аня"}'}
    assert "$.age: required" in second["messages"][-1]["content"]


async def test_gives_up_after_the_retries():
    fake = FakeLLM()
    fake.queue("no idea", "still no")
    with pytest.raises(StructuredOutputError) as err:
        await openai(fake).generate_json(USER, schema=PERSON, retries=1)
    assert err.value.reason == "format" and err.value.details["text"] == "still no"
    assert len(fake.requests) == 2


async def test_a_backend_without_json_mode_is_asked_in_words():
    fake = FakeLLM()
    fake.rejected_params = {"response_format"}
    fake.queue('{"name": "Аня", "age": 3}')
    assert await openai(fake).generate_json(USER, schema=PERSON) == {"name": "Аня", "age": 3}
    rejected, words = fake.requests  # the fake records the refused request too
    assert "response_format" in rejected and "response_format" not in words


async def test_claude_answers_through_a_forced_tool():
    fake = FakeLLM()
    llm = LLMClient(AnthropicProvider("http://llm/v1", "k", client=fake_client(fake)), model="claude")
    fake.queue('{"name": "Аня", "age": 20}')
    assert await llm.generate_json(USER, schema=PERSON) == {"name": "Аня", "age": 20}
    body = fake.anthropic_requests[0]
    assert body["tool_choice"] == {"type": "tool", "name": "answer"} and body["tools"][0]["input_schema"] == PERSON

    # A root that is not an object goes wrapped as {"value": ...} and comes back unwrapped.
    fake.queue('{"value": ["a", "b"]}')
    assert await llm.generate_json(USER, schema={"type": "array", "items": {"type": "string"}}) == ["a", "b"]
    assert fake.anthropic_requests[1]["tools"][0]["name"] == "answer_value"

    # With thinking on the API does not allow forcing a tool: the request goes in words.
    fake.queue('{"name": "Аня", "age": 1}')
    await llm.generate_json(USER, schema=PERSON, thinking_budget=2048)
    assert "tools" not in fake.anthropic_requests[2] and "thinking" in fake.anthropic_requests[2]


def test_wrapped_answer_that_is_not_json_is_passed_on():
    assert _tool_answer('{"value": [1]}', "answer_value") == "[1]"
    assert _tool_answer("{broken", "answer_value") == "{broken"
    assert json.loads(_tool_answer('{"a": 1}', "answer")) == {"a": 1}
