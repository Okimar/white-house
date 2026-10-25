import json

import httpx
import pytest

from tavern.devtools.fake_llm import FakeLLM


@pytest.fixture
async def llm():
    fake = FakeLLM()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake.app), base_url="http://llm"
    ) as http:
        yield fake, http


async def test_non_streaming_reply_and_echo(llm):
    fake, http = llm
    fake.queue("Queued reply")
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    first = (await http.post("/v1/chat/completions", json=body)).json()
    assert first["choices"][0]["message"]["content"] == "Queued reply"
    assert first["usage"]["total_tokens"] > 0
    second = (await http.post("/v1/chat/completions", json=body)).json()
    assert second["choices"][0]["message"]["content"] == "Echo: hi"
    assert len(fake.requests) == 2


async def test_streaming_pieces_reassemble(llm):
    fake, http = llm
    fake.queue("one two three")
    body = {"messages": [], "stream": True, "stream_options": {"include_usage": True}}
    text, usage = "", None
    async with http.stream("POST", "/v1/chat/completions", json=body) as response:
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            for choice in chunk["choices"]:
                text += choice["delta"].get("content", "")
            usage = chunk.get("usage") or usage
    assert text == "one two three"
    assert usage is not None


async def test_scripted_failure(llm):
    fake, http = llm
    fake.fail_next(429, "slow down")
    response = await http.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 429
    assert response.json()["error"]["message"] == "slow down"
