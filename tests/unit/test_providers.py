"""Providers of stage 6а: settings table, Gemini wire format, several keys with proxies and retries."""

from __future__ import annotations

import httpx
import pytest

from tavern.devtools.fake_llm import FakeLLM
from tavern.providers import keys as K
from tavern.providers import params as P
from tavern.providers.base import ChatMessage, GenerationRequest, ProviderError
from tavern.providers.gemini import GeminiProvider
from tavern.providers.openai_compat import OpenAICompatProvider, _model_info


def fake_client(fake: FakeLLM) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")


def request(**extra) -> GenerationRequest:
    return GenerationRequest(
        messages=[ChatMessage("system", "Be kind."), ChatMessage("user", "a"), ChatMessage("user", "b"),
                  ChatMessage("assistant", "c")],
        model="fake-model", extra=extra,
    )


# Settings table


def test_request_params_keep_only_what_the_kind_takes():
    params = {"temperature": 0.5, "min_p": 0.1, "thinking_budget": 100, "retries": 3,
              "max_context_tokens": 8000, "plugin_thing": 1}
    assert P.request_params("openai", params) == {"temperature": 0.5, "min_p": 0.1, "plugin_thing": 1}
    assert P.request_params("gemini", params) == {"temperature": 0.5, "thinking_budget": 100, "plugin_thing": 1}
    # A plugin kind gets every setting that goes to a backend.
    assert set(P.request_params("plugin", params)) == {"temperature", "min_p", "thinking_budget", "plugin_thing"}


@pytest.mark.parametrize("params, message", [
    ({"temperature": "hot"}, "числом"), ({"temperature": 5}, "от 0 до 2"), ({"top_k": 1.5}, "целым"),
    ({"safety_off": 1}, "да или нет"), ({"stop": "x"}, "список"), ({"reasoning_effort": "max"}, "одно из"),
    ({"extra_body": []}, "объект"),
])
def test_validate_rejects_bad_values(params, message):
    with pytest.raises(ValueError, match=message):
        P.validate(params)


def test_validate_keeps_good_and_unknown_values():
    assert P.validate({"temperature": 1, "top_k": 40.0, "x": "y"}) == {"temperature": 1.0, "top_k": 40, "x": "y"}


# OpenAI-compatible


def test_openai_body_merges_raw_fields_last_and_drops_random_seed():
    body = OpenAICompatProvider("http://x/v1").build_body(
        request(seed=-1, min_p=0.1, extra_body={"min_p": 0.2, "custom": True}), stream=True)
    assert "seed" not in body and body["min_p"] == 0.2 and body["custom"] is True


def test_openrouter_style_model_info():
    info = _model_info({
        "id": "x/y", "name": "Y", "context_length": 128000, "supported_parameters": ["temperature", "reasoning"],
        "pricing": {"prompt": "0.0000025", "completion": "0.00001"}, "top_provider": {"max_completion_tokens": 8192},
    })
    assert (info.context, info.max_output, info.price_in, info.price_out) == (128000, 8192, 2.5, 10.0)
    assert "temperature" in info.params and "reasoning_effort" in info.params and "top_k" not in info.params
    assert "retries" in info.params and "extra_body" in info.params  # Tavern-side settings always apply


async def test_openai_error_reasons():
    fake = FakeLLM()
    fake.fail_next(429, "slow down")
    fake.fail_next(400, "You exceeded your current quota")
    fake.fail_next(400, "bad field")
    provider = OpenAICompatProvider("http://llm/v1", client=fake_client(fake))
    reasons = []
    for _ in range(3):
        with pytest.raises(ProviderError) as err:
            await provider.generate(request())
        reasons.append((err.value.reason, err.value.upstream_status))
    assert reasons == [("limit", 429), ("limit", 400), ("request", 400)]


# Gemini


def test_gemini_body():
    body = GeminiProvider("http://llm/v1beta").build_body(GenerationRequest(
        messages=[ChatMessage("system", "Be kind."), ChatMessage("user", "a"), ChatMessage("user", "b"),
                  ChatMessage("assistant", "c"), ChatMessage("system", "late note")],
        model="m", max_tokens=100, temperature=0.5, stop=["x"],
        extra={"top_k": 40, "seed": -1, "thinking_budget": 512, "include_thoughts": True, "safety_off": True,
               "extra_body": {"generationConfig": {"candidateCount": 1}, "cachedContent": "c1"}},
    ))
    assert body["systemInstruction"] == {"parts": [{"text": "Be kind."}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "a"}, {"text": "b"}]},
        {"role": "model", "parts": [{"text": "c"}]},
        {"role": "user", "parts": [{"text": "late note"}]},
    ]
    assert body["generationConfig"] == {
        "maxOutputTokens": 100, "temperature": 0.5, "stopSequences": ["x"], "topK": 40,
        "thinkingConfig": {"thinkingBudget": 512, "includeThoughts": True}, "candidateCount": 1,
    }
    assert {s["threshold"] for s in body["safetySettings"]} == {"OFF"}
    assert body["cachedContent"] == "c1"


def test_gemini_json_mode_and_effort():
    body = GeminiProvider("http://llm/v1beta").build_body(GenerationRequest(
        messages=[ChatMessage("user", "a")], model="m", extra={"reasoning_effort": "low"},
        response_format={"type": "json_schema", "schema": {"type": "object"}},
    ))
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == {"type": "object"}
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}


async def test_gemini_stream_keeps_thoughts_apart():
    fake = FakeLLM()
    fake.queue("Hello there friend")
    provider = GeminiProvider("http://llm/v1beta", "g-key", client=fake_client(fake))
    events = [e async for e in provider.stream(request(include_thoughts=True))]
    assert [e.text for e in events if e.kind == "reasoning"] == ["Thinking about it."]
    assert "".join(e.text for e in events if e.kind == "text") == "Hello there friend"
    assert events[-1].kind == "done" and events[-1].usage.completion_tokens > 0
    assert fake.seen_keys == ["g-key"]  # in the header, never in the address
    assert fake.gemini_requests[0]["method"] == "streamGenerateContent"


async def test_gemini_models_describe_only_chat_models():
    provider = GeminiProvider("http://llm/v1beta", client=fake_client(FakeLLM()))
    (info,) = await provider.describe_models()
    assert info.id == "fake-model" and info.context == 1048576 and info.max_output == 65536
    assert {"top_k", "thinking_budget", "safety_off"} <= set(info.params)
    assert info.limits["temperature"] == [0, 2.0]


def _sse(*chunks: str) -> httpx.MockTransport:
    body = "".join(f"data: {c}\n\n" for c in chunks).encode()
    return httpx.MockTransport(lambda r: httpx.Response(200, content=body,
                                                        headers={"content-type": "text/event-stream"}))


async def test_gemini_refusals_are_marked():
    # The conftest's anyio marker does not combine with parametrize, so the cases are a loop.
    for chunk in ('{"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}',
                  '{"candidates": [{"finishReason": "SAFETY"}]}'):
        provider = GeminiProvider("http://g/v1beta", client=httpx.AsyncClient(transport=_sse(chunk)))
        with pytest.raises(ProviderError) as err:
            await provider.generate(request())
        assert err.value.reason == "refused"


# Several keys


def keyed(fake: FakeLLM, keys: list[str], **options) -> K.KeyedProvider:
    client = fake_client(fake)
    entries = [{"id": k, "label": k, "key": k, "proxy": ""} for k in keys]
    return K.KeyedProvider("openai", entries,
                           lambda key: (OpenAICompatProvider("http://llm/v1", key["key"], client=client), None),
                           **options)


async def test_spent_key_passes_to_the_next_and_is_remembered():
    fake = FakeLLM(key_status={"spent": 429, "wrong": 401})
    memory: dict[str, int] = {}
    provider = keyed(fake, ["spent", "wrong", "good"], remember=memory, remember_as="c1")
    assert (await provider.generate(request())).text.startswith("Echo")
    assert fake.seen_keys == ["spent", "wrong", "good"] and provider.used_key == "good"
    await provider.generate(request())
    assert fake.seen_keys[-1] == "good" and memory == {"c1": 2}


async def test_without_rotation_the_first_failure_stops():
    fake = FakeLLM(key_status={"spent": 429})
    with pytest.raises(ProviderError, match=r"\[spent\]"):
        await keyed(fake, ["spent", "good"], rotate=False).generate(request())


async def test_retries_repeat_the_same_key_on_server_errors():
    fake = FakeLLM()
    fake.fail_next(503, "busy")
    assert (await keyed(fake, ["a", "b"], retries=1).generate(request())).text
    assert fake.seen_keys == ["a", "a"]
    fake.fail_next(503, "busy")
    with pytest.raises(ProviderError, match="busy"):
        await keyed(fake, ["a", "b"]).generate(request())  # no retries; a server error is not the key's fault


async def test_errors_never_carry_the_key():
    leaky = httpx.MockTransport(lambda r: httpx.Response(
        401, json={"error": {"message": f"bad key {r.headers['authorization'][7:]}"}}))
    client = httpx.AsyncClient(transport=leaky)
    secret = "sk-very-secret-value"
    provider = K.KeyedProvider("openai", [{"id": "1", "label": "", "key": secret, "proxy": ""}],
                               lambda key: (OpenAICompatProvider("http://x/v1", key["key"], client=client), None))
    with pytest.raises(ProviderError) as err:
        await provider.list_models()
    assert secret not in err.value.message and "***" in err.value.message


def test_proxy_checks_and_public_view():
    assert K.check_proxy("socks5://user:pw@10.0.0.1:1080") == "socks5://user:pw@10.0.0.1:1080"
    for bad in ("ftp://x:1", "not a url", "http://"):
        with pytest.raises(ValueError):
            K.check_proxy(bad)
    (view,) = K.public_keys([{"id": "1", "label": "L", "key": "sk", "proxy": "http://user:pw@proxy.lan:3128"}])
    assert view == {"id": "1", "label": "L", "has_key": True, "has_proxy": True, "proxy": "http://proxy.lan:3128"}


async def test_key_with_proxy_gets_its_own_client(ctx):
    conn = ctx.connections.create(name="p", kind="openai", base_url="https://api.example/v1")
    K.save_keys(ctx.secrets, conn["id"], [K.new_key(key="k", proxy="socks5://127.0.0.1:9")])
    provider = ctx.providers.create(conn)
    inner = provider.inner(0)
    assert inner._client is not None and provider._clients == [inner._client]
    await provider.aclose()
    assert inner._client.is_closed


# Stage 6б: Claude, pictures, web search, thinking


def test_anthropic_body():
    from tavern.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider("http://a/v1")
    body = provider.build_body(GenerationRequest(
        messages=[ChatMessage("system", "Be kind."), ChatMessage("assistant", "Hi!"),
                  ChatMessage("user", "look", images=(("image/png", "QUJD"),)), ChatMessage("system", "note")],
        model="claude-x", temperature=0.5, top_p=0.9, stop=["END"], extra={"top_k": 20, "web_search": True},
    ))
    assert body["system"] == "Be kind." and body["max_tokens"] == 4096 and body["stream"] is True
    assert body["messages"][0] == {"role": "user", "content": [{"type": "text", "text": "."}]}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][2]["content"] == [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "text", "text": "look"}, {"type": "text", "text": "note"}]
    assert (body["temperature"], body["top_p"], body["top_k"], body["stop_sequences"]) == (0.5, 0.9, 20, ["END"])
    assert body["tools"][0]["name"] == "web_search"
    thinking = provider.build_body(GenerationRequest(
        messages=[ChatMessage("user", "a")], model="m", max_tokens=500, temperature=0.5,
        extra={"thinking_budget": 2000, "top_k": 5}))
    assert thinking["thinking"] == {"type": "enabled", "budget_tokens": 2000}
    assert thinking["max_tokens"] == 3024 and "temperature" not in thinking and "top_k" not in thinking


async def test_anthropic_stream_and_errors():
    from tavern.providers.anthropic import AnthropicProvider

    chunks = [
        '{"type": "message_start", "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 5}}}',
        '{"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "Hmm."}}',
        '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}',
        '{"type": "content_block_delta", "delta": {"type": "text_delta", "text": " you"}}',
        '{"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}}',
    ]
    provider = AnthropicProvider("http://a/v1", "k", client=httpx.AsyncClient(transport=_sse(*chunks)))
    events = [e async for e in provider.stream(request())]
    assert [(e.kind, e.text) for e in events[:-1]] == [("reasoning", "Hmm."), ("text", "Hello"), ("text", " you")]
    assert (events[-1].usage.prompt_tokens, events[-1].usage.completion_tokens) == (15, 7)

    refusal = AnthropicProvider("http://a/v1", client=httpx.AsyncClient(transport=_sse(
        '{"type": "message_delta", "delta": {"stop_reason": "refusal"}, "usage": {"output_tokens": 0}}')))
    with pytest.raises(ProviderError) as err:
        await refusal.generate(request())
    assert err.value.reason == "refused"

    for status, message, reason in ((529, "Overloaded", "server"), (400, "Your credit balance is too low", "limit"),
                                    (401, "invalid x-api-key", "auth")):
        failing = AnthropicProvider("http://a/v1", client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda r, s=status, m=message: httpx.Response(s, json={"type": "error", "error": {"message": m}}))))
        with pytest.raises(ProviderError) as err:
            await failing.generate(request())
        assert err.value.reason == reason


def test_pictures_and_web_search_in_other_protocols():
    picture = ChatMessage("user", "see", images=(("image/jpeg", "QUJD"),))
    openai = OpenAICompatProvider("http://x/v1").build_body(
        GenerationRequest(messages=[picture], extra={"web_search": True}), stream=True)
    assert openai["messages"][0]["content"] == [
        {"type": "text", "text": "see"}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}}]
    assert openai["web_search_options"] == {} and "web_search" not in openai
    router = OpenAICompatProvider("https://openrouter.ai/api/v1").build_body(
        GenerationRequest(messages=[picture], extra={"web_search": True}), stream=True)
    assert router["plugins"] == [{"id": "web"}] and "web_search_options" not in router
    gemini = GeminiProvider("http://g/v1beta").build_body(
        GenerationRequest(messages=[picture], model="m", extra={"web_search": True}))
    assert gemini["contents"][0]["parts"] == [{"inlineData": {"mimeType": "image/jpeg", "data": "QUJD"}},
                                              {"text": "see"}]
    assert gemini["tools"] == [{"google_search": {}}]


async def test_llm_client_passes_thinking_through():
    """Regression: a "reasoning" event used to be taken for the end of the stream."""
    from tavern.core.llm import LLMClient

    fake = FakeLLM()
    fake.queue("Answer here")
    llm = LLMClient(GeminiProvider("http://llm/v1beta", client=fake_client(fake)), model="fake-model")
    events = [e async for e in llm.stream([ChatMessage("user", "q")], include_thoughts=True)]
    assert [e.kind for e in events][0] == "reasoning" and events[-1].kind == "done"
    assert "".join(e.text for e in events if e.kind == "text") == "Answer here"
    assert sum(e.kind == "done" for e in events) == 1


async def test_claude_against_the_fake_server():
    from tavern.providers.anthropic import AnthropicProvider

    fake = FakeLLM()
    provider = AnthropicProvider("http://llm/v1", "k", client=fake_client(fake))
    events = [e async for e in provider.stream(GenerationRequest(
        [ChatMessage("user", "hi")], model="fake-model", extra={"thinking_budget": 2000}))]
    assert [e.kind for e in events] == ["reasoning", "text", "text", "done"]
    assert "".join(e.text for e in events if e.kind == "text") == "Echo: hi"
    assert fake.seen_keys == ["k"] and fake.anthropic_requests[0]["thinking"]["budget_tokens"] == 2000
    assert await provider.list_models() == ["fake-model"]
