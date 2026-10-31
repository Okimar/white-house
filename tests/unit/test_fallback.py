"""Fallback steps (stage 6г): which failure goes where, refusals by phrase, empty replies."""

from __future__ import annotations

import httpx
import pytest

from tavern.devtools.fake_llm import FakeLLM
from tavern.providers.base import ChatMessage, GenerationRequest, ProviderError
from tavern.providers.fallback import FallbackProvider, check_steps
from tavern.providers.openai_compat import OpenAICompatProvider


def provider(fake: FakeLLM, key: str = "") -> OpenAICompatProvider:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")
    return OpenAICompatProvider("http://llm/v1", key, client=client)


def request() -> GenerationRequest:
    return GenerationRequest(messages=[ChatMessage("user", "hi")], model="main-model")


def chain(main: FakeLLM, spare: FakeLLM, steps: list[dict], **kw) -> FallbackProvider:
    return FallbackProvider(provider(main), check_steps(steps),
                            lambda step: (provider(spare), step["model"], f"spare · {step['model']}"), **kw)


async def text_of(p: FallbackProvider) -> str:
    return "".join([e.text async for e in p.stream(request()) if e.kind == "text"])


def test_steps_are_checked():
    assert check_steps([{"on": ["limit", "limit"], "model": " m "}]) == [
        {"on": ["limit"], "connection": "", "model": "m", "key": ""}]
    for bad in ("x", [{"on": []}], [{"on": ["weather"]}], [{"on": ["limit"], "model": 5}], [{}] * 11):
        with pytest.raises(ValueError):
            check_steps(bad)


async def test_the_step_that_answers_the_failure_is_taken():
    main, spare = FakeLLM(), FakeLLM()
    main.fail_next(429, "quota")
    spare.queue("from spare")
    p = chain(main, spare, [{"on": ["refused"], "model": "wrong"}, {"on": ["limit"], "model": "other"}])
    assert await text_of(p) == "from spare"
    assert spare.requests[0]["model"] == "other" and p.used == "spare · other"


async def test_unanswered_failure_is_raised():
    main, spare = FakeLLM(), FakeLLM()
    main.fail_next(401, "bad key")
    with pytest.raises(ProviderError, match="bad key"):
        await text_of(chain(main, spare, [{"on": ["limit"]}]))
    assert spare.requests == []


async def test_text_refusal_and_empty_reply():
    main, spare = FakeLLM(), FakeLLM()
    main.queue("I'm sorry, but I can't continue this story.")
    spare.queue("Once upon a time")
    p = chain(main, spare, [{"on": ["refused"], "model": "uncensored"}])
    assert await text_of(p) == "Once upon a time"
    main.queue("")
    spare.queue("filled")
    assert await text_of(chain(main, spare, [{"on": ["empty"]}])) == "filled"


async def test_long_replies_stream_without_judging():
    main, spare = FakeLLM(), FakeLLM()
    long_reply = "word " * 150 + "I can't help with that"
    main.queue(long_reply)
    p = chain(main, spare, [{"on": ["refused"]}])
    assert await text_of(p) == long_reply and spare.requests == []
    # Without a "refused" step nothing is held back or judged.
    main.queue("I can't help with that")
    assert await text_of(chain(main, spare, [{"on": ["limit"]}])) == "I can't help with that"


async def test_own_phrases_replace_the_built_in_ones():
    main, spare = FakeLLM(), FakeLLM()
    main.queue("Nope, not today")
    spare.queue("ok")
    assert await text_of(chain(main, spare, [{"on": ["refused"]}], phrases=["nope"])) == "ok"
