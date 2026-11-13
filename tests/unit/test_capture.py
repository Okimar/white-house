import json

import httpx
import pytest

from tavern.devtools.capture import Capture, _forward_url, clean_query, kind_of


def make(tmp_path, **kw):
    cap = Capture(tmp_path / "out", **kw)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=cap.app), base_url="http://cap")
    return cap, client


def saved(cap):
    return [json.loads(p.read_text(encoding="utf-8")) for p in cap.saved]


def test_kind_of_paths():
    assert kind_of("/v1/chat/completions") == "openai"
    assert kind_of("/chat/completions") == "openai"
    assert kind_of("/v1/messages") == "anthropic"
    assert kind_of("/v1beta/models/gemini-x:generateContent") == "gemini"
    assert kind_of("/v1beta/models/gemini-x:streamGenerateContent") == "gemini_stream"
    assert kind_of("/v1/embeddings") is None


async def test_saves_body_and_strips_secrets(tmp_path):
    cap, client = make(tmp_path)
    body = {"model": "m", "messages": [{"role": "user", "content": "привет"}], "temperature": 0.5}
    headers = {"Authorization": "Bearer sk-secret", "x-api-key": "k1", "x-goog-api-key": "k2",
               "Cookie": "a=b", "X-Custom": "keep"}
    reply = await client.post("/v1/chat/completions?key=AIza-secret&alt=sse", json=body, headers=headers)
    assert reply.status_code == 200
    (rec,) = saved(cap)
    assert rec["body"] == body
    assert rec["path"] == "/v1/chat/completions"
    assert rec["query"] == "alt=sse"
    assert rec["headers"]["x-custom"] == "keep"
    text = cap.saved[0].read_text(encoding="utf-8")
    for secret in ("sk-secret", "k1", "k2", "a=b", "AIza-secret", "authorization"):
        assert secret not in text
    assert "привет" in text  # kept readable, not \u-escaped
    assert text.count("\n") > 5  # pretty printed


async def test_each_request_gets_its_own_file(tmp_path):
    cap, client = make(tmp_path)
    for i in range(3):
        await client.post("/v1/messages", json={"messages": [], "n": i})
    assert len({p.name for p in cap.saved}) == 3
    assert [r["body"]["n"] for r in saved(cap)] == [0, 1, 2]


async def test_non_streaming_and_streaming_fake_replies(tmp_path):
    cap, client = make(tmp_path, reply="one two three")
    plain = (await client.post("/v1/chat/completions", json={"messages": []})).json()
    assert plain["choices"][0]["message"]["content"] == "one two three"

    text = ""
    async with client.stream("POST", "/v1/chat/completions", json={"messages": [], "stream": True}) as response:
        lines = [ln async for ln in response.aiter_lines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    for line in lines[:-1]:
        for choice in json.loads(line[6:])["choices"]:
            text += choice.get("delta", {}).get("content") or ""
    assert text == "one two three"


async def test_anthropic_and_gemini_replies(tmp_path):
    cap, client = make(tmp_path, reply="a b")
    msg = (await client.post("/v1/messages", json={"messages": []})).json()
    assert msg["content"][0]["text"] == "a b"
    async with client.stream("POST", "/v1/messages", json={"messages": [], "stream": True}) as response:
        raw = "".join([ln + "\n" async for ln in response.aiter_lines()])
    deltas = [json.loads(ln[6:]) for ln in raw.splitlines() if ln.startswith("data: ")]
    assert "".join(d["delta"]["text"] for d in deltas if d["type"] == "content_block_delta") == "a b"

    path = "/v1beta/models/g:generateContent"
    full = (await client.post(path, json={"contents": []})).json()
    assert full["candidates"][0]["content"]["parts"][0]["text"] == "a b"
    async with client.stream("POST", "/v1beta/models/g:streamGenerateContent?alt=sse", json={"contents": []}) as response:
        raw = "".join([ln + "\n" async for ln in response.aiter_lines()])
    parts = [json.loads(ln[6:])["candidates"][0]["content"]["parts"][0]["text"]
             for ln in raw.splitlines() if ln.startswith("data: ")]
    assert "".join(parts) == "a b"
    arr = (await client.post("/v1beta/models/g:streamGenerateContent", json={"contents": []})).json()
    assert isinstance(arr, list) and len(cap.saved) == 5


async def test_models_and_unknown_paths(tmp_path):
    cap, client = make(tmp_path)
    for path in ("/v1/models", "/models", "/v1beta/models"):
        data = (await client.get(path)).json()
        assert data["data"][0]["id"] and data["models"][0]["name"]
    assert (await client.post("/v1/embeddings", json={})).status_code == 404
    assert (await client.get("/nothing")).status_code == 404
    assert cap.saved == []


async def test_forward_passes_request_and_answer(tmp_path):
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=b'data: {"choices":[{"delta":{"content":"real"}}]}\n\ndata: [DONE]\n\n')

    cap, client = make(tmp_path, forward="https://real.example/v1", transport=httpx.MockTransport(upstream))
    body = {"messages": [{"role": "user", "content": "x"}], "stream": True}
    response = await client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer real-key"})
    assert "real" in response.text and response.status_code == 200
    assert seen["url"] == "https://real.example/v1/chat/completions"  # /v1 not doubled
    assert seen["auth"] == "Bearer real-key" and seen["body"] == body
    assert "real-key" not in cap.saved[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("base,path,query,url", [
    ("https://h/v1", "/v1/chat/completions", "", "https://h/v1/chat/completions"),
    ("https://h", "/v1/chat/completions", "", "https://h/v1/chat/completions"),
    ("https://h/api/v1beta", "/v1beta/models/m:generateContent", "alt=sse",
     "https://h/api/v1beta/models/m:generateContent?alt=sse"),
    ("https://h/openai", "/chat/completions", "", "https://h/openai/chat/completions"),
])
def test_forward_url(base, path, query, url):
    assert _forward_url(base, path, query) == url


def test_clean_query_drops_keys():
    assert clean_query("key=abc&alt=sse&api_key=z") == "alt=sse"
