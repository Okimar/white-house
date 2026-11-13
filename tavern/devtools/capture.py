"""A recording stand-in for an LLM server: saves the exact request a chat program sends.

Point any client (SillyTavern, this app, anything OpenAI / Gemini / Claude shaped) at it and every
generation request lands in the output folder as one pretty JSON file (time, path, headers without
secrets, body). Then `python -m tavern.devtools.compare_requests A.json B.json` shows how two programs
differ for the same inputs.

    python -m tavern.devtools.capture --port 5003 --out captures
    # OpenAI-compatible address:  http://127.0.0.1:5003/v1
    # Gemini address:             http://127.0.0.1:5003/v1beta
    # Claude address:             http://127.0.0.1:5003/v1

Without `--forward` it answers with a fixed fake reply (streamed when the client asked for a stream),
so the client finishes normally. With `--forward https://real.service/v1` the request goes on to the
real server and its answer is streamed back (the client's own key is passed on, never saved).
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from tavern.devtools.fake_llm import MODEL_ID, _rough_tokens, _split_keep_spaces

REPLY = "Captured: this is a fixed fake reply."

# Header names that never reach the file (keys, tokens, cookies, anything auth-like).
_SECRET_HEADER = re.compile(r"auth|key|token|secret|cookie|session|credential", re.IGNORECASE)
_SECRET_QUERY = re.compile(r"key|token|secret|auth", re.IGNORECASE)
_HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding", "keep-alive"}


def kind_of(path: str) -> str | None:
    """Which protocol a generation path belongs to, or None when it is not one."""
    clean = path.rstrip("/")
    if clean.endswith(":streamGenerateContent"):
        return "gemini_stream"
    if clean.endswith(":generateContent"):
        return "gemini"
    if clean.endswith("/chat/completions"):
        return "openai"
    if clean.endswith("/completions"):
        return "text"
    if clean.endswith("/messages"):
        return "anthropic"
    return None


def clean_headers(headers: Any) -> dict[str, str]:
    """The request headers without anything that could hold a secret."""
    return {name.lower(): value for name, value in headers.items() if not _SECRET_HEADER.search(name)}


def clean_query(query: str) -> str:
    """Gemini clients put the key in the address (`?key=...`): drop it, keep the rest."""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if not _SECRET_QUERY.search(k)]
    return urlencode(pairs)


def _forward_url(base: str, path: str, query: str) -> str:
    """base + request path, without repeating a part both have (base `.../v1` + path `/v1/chat/...`)."""
    parts = urlsplit(base)
    base_path = parts.path.rstrip("/")
    for size in range(len(base_path), 0, -1):  # longest tail of the base that starts the path
        tail = base_path[-size:]
        if tail.startswith("/") and path.startswith(tail + "/"):
            base_path = base_path[:-size]
            break
    url = f"{parts.scheme}://{parts.netloc}{base_path}{path}"
    return f"{url}?{query}" if query else url


async def _already_read(upstream: httpx.Response) -> AsyncIterator[bytes]:
    """In-process transports hand over a response whose body is read already."""
    yield upstream.content


class Capture:
    def __init__(self, out: Path, forward: str | None = None, reply: str = REPLY,
                 transport: httpx.AsyncBaseTransport | None = None, chunk_delay: float = 0.0) -> None:
        self.out = Path(out)
        self.forward = forward
        self.reply = reply
        self.chunk_delay = chunk_delay
        self.saved: list[Path] = []
        self._counter = itertools.count(1)
        self._client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(None, connect=30.0))

    @property
    def app(self) -> Starlette:
        return Starlette(routes=[Route("/{path:path}", self._handle, methods=["GET", "POST"])])

    # ---- saving ----

    def _save(self, request: Request, raw: bytes) -> Path:
        try:
            body: Any = json.loads(raw) if raw else None
        except ValueError:
            body = {"_raw": raw.decode("utf-8", errors="replace")}
        now = datetime.now()
        path = request.url.path
        record = {
            "timestamp": now.isoformat(timespec="microseconds"),
            "path": path,
            "query": clean_query(request.url.query),
            "headers": clean_headers(request.headers),
            "body": body,
        }
        self.out.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", path.strip("/"))[-60:] or "root"
        file = self.out / f"{now:%Y%m%d-%H%M%S}-{next(self._counter):03d}-{slug}.json"
        file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.saved.append(file)
        return file

    # ---- handling ----

    async def _handle(self, request: Request) -> Response:
        path = request.url.path
        if request.method == "GET":
            if path.rstrip("/").endswith("models"):
                return self._models()
            return JSONResponse({"error": {"message": f"not found: {path}"}}, status_code=404)
        kind = kind_of(path)
        if kind is None:
            return JSONResponse({"error": {"message": f"not a generation path: {path}"}}, status_code=404)
        raw = await request.body()
        self._save(request, raw)
        if self.forward:
            return await self._pass_on(request, raw)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            body = {}
        return self._fake_reply(kind, body if isinstance(body, dict) else {}, request.query_params.get("alt") == "sse")

    def _models(self) -> JSONResponse:
        """Both shapes in one answer: OpenAI's `data` and Gemini's `models`."""
        return JSONResponse({
            "object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "name": "Capture Model", "context_length": 32768}],
            "models": [{"name": f"models/{MODEL_ID}", "displayName": "Capture Model", "inputTokenLimit": 1048576,
                        "outputTokenLimit": 65536, "supportedGenerationMethods": ["generateContent"]}],
        })

    async def _pass_on(self, request: Request, raw: bytes) -> Response:
        url = _forward_url(self.forward or "", request.url.path, request.url.query)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
        try:
            upstream = await self._client.send(
                self._client.build_request("POST", url, headers=headers, content=raw), stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": {"message": f"forwarding to {urlsplit(url).netloc} failed: {exc}"}},
                                status_code=502)
        kept = {k: v for k, v in upstream.headers.items() if k.lower() in {"content-type", "cache-control"}}
        chunks = _already_read(upstream) if upstream.is_stream_consumed else upstream.aiter_raw()
        return StreamingResponse(chunks, status_code=upstream.status_code, headers=kept,
                                 background=BackgroundTask(upstream.aclose))

    # ---- fake replies ----

    def _fake_reply(self, kind: str, body: dict[str, Any], sse: bool) -> Response:
        text = self.reply
        usage_in = _rough_tokens(json.dumps(body))
        usage_out = _rough_tokens(text)
        stream = bool(body.get("stream"))
        created = int(time.time())
        model = str(body.get("model", MODEL_ID))
        if kind == "openai":
            usage = {"prompt_tokens": usage_in, "completion_tokens": usage_out, "total_tokens": usage_in + usage_out}
            if not stream:
                return JSONResponse({"id": "capture-1", "object": "chat.completion", "created": created, "model": model,
                                     "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                  "finish_reason": "stop"}], "usage": usage})
            return self._sse(self._openai_stream(text, model, created, usage))
        if kind == "text":
            if not stream:
                return JSONResponse({"choices": [{"index": 0, "text": text, "finish_reason": "stop"}]})
            return self._sse(self._text_stream(text))
        if kind == "anthropic":
            if not stream:
                return JSONResponse({"id": "capture-1", "type": "message", "role": "assistant", "model": model,
                                     "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                                     "usage": {"input_tokens": usage_in, "output_tokens": usage_out}})
            return self._sse(self._anthropic_stream(text, usage_in, usage_out))
        usage = {"promptTokenCount": usage_in, "candidatesTokenCount": usage_out}
        full = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
                "usageMetadata": usage}
        if kind == "gemini":
            return JSONResponse(full)
        if sse:
            return self._sse(self._gemini_stream(text, usage))
        return JSONResponse([full])  # streamGenerateContent without alt=sse is one JSON array

    @staticmethod
    def _sse(events: AsyncIterator[bytes]) -> StreamingResponse:
        return StreamingResponse(events, media_type="text/event-stream")

    async def _pause(self) -> None:
        if self.chunk_delay:
            import anyio

            await anyio.sleep(self.chunk_delay)

    async def _openai_stream(self, text: str, model: str, created: int, usage: dict[str, int]) -> AsyncIterator[bytes]:
        def event(payload: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(payload)}\n\n".encode()

        base = {"id": "capture-1", "object": "chat.completion.chunk", "created": created, "model": model}
        yield event({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        for piece in _split_keep_spaces(text):
            await self._pause()
            yield event({**base, "choices": [{"index": 0, "delta": {"content": piece}}]})
        yield event({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield event({**base, "choices": [], "usage": usage})
        yield b"data: [DONE]\n\n"

    async def _text_stream(self, text: str) -> AsyncIterator[bytes]:
        for piece in _split_keep_spaces(text):
            await self._pause()
            yield ("data: " + json.dumps({"choices": [{"index": 0, "text": piece}]}) + "\n\n").encode()
        yield ("data: " + json.dumps({"choices": [{"index": 0, "text": "", "finish_reason": "stop"}]}) + "\n\n").encode()
        yield b"data: [DONE]\n\n"

    async def _anthropic_stream(self, text: str, usage_in: int, usage_out: int) -> AsyncIterator[bytes]:
        def wire(payload: dict[str, Any]) -> bytes:
            return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode()

        yield wire({"type": "message_start", "message": {"usage": {"input_tokens": usage_in}}})
        yield wire({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        for piece in _split_keep_spaces(text):
            await self._pause()
            yield wire({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}})
        yield wire({"type": "content_block_stop", "index": 0})
        yield wire({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": usage_out}})
        yield wire({"type": "message_stop"})

    async def _gemini_stream(self, text: str, usage: dict[str, int]) -> AsyncIterator[bytes]:
        pieces = _split_keep_spaces(text)
        for i, piece in enumerate(pieces):
            await self._pause()
            payload: dict[str, Any] = {"candidates": [{"content": {"role": "model", "parts": [{"text": piece}]}}]}
            if i == len(pieces) - 1:
                payload["candidates"][0]["finishReason"] = "STOP"
                payload["usageMetadata"] = usage
            yield ("data: " + json.dumps(payload) + "\r\n\r\n").encode()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Save every LLM request a client sends (for comparing programs)")
    parser.add_argument("--port", type=int, default=5003)
    parser.add_argument("--out", required=True, help="folder for the saved requests")
    parser.add_argument("--forward", default=None, help="real base URL to pass requests to, e.g. https://api.openai.com/v1")
    parser.add_argument("--delay", type=float, default=0.02, help="seconds between fake stream chunks")
    args = parser.parse_args()
    capture = Capture(Path(args.out), forward=args.forward, chunk_delay=args.delay)
    print(f"capturing into {Path(args.out).resolve()} on http://127.0.0.1:{args.port}"
          + (f", forwarding to {args.forward}" if args.forward else ", fake replies"))
    uvicorn.run(capture.app, host="127.0.0.1", port=args.port, loop="asyncio", log_level="warning")


if __name__ == "__main__":
    main()
