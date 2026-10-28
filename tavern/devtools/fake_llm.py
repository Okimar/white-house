"""A scriptable fake of an OpenAI-compatible LLM server. No network, no API keys.

In tests, mount it in-process:

    fake = FakeLLM()
    fake.queue("Hello!", "Second reply")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")

Standalone, for manual testing in the browser:

    python -m tavern.devtools.fake_llm --port 5001
    # then point an OpenAI-compatible connection at http://127.0.0.1:5001/v1

Without queued replies it answers "Echo: <last user message>".
`/v1/images/generations` returns a small PNG whose colours depend on the prompt.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import struct
import time
import zlib
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

MODEL_ID = "fake-model"


@dataclass
class FakeLLM:
    chunk_delay: float = 0.0
    base_url: str = ""  # set by test fixtures that serve this fake on a real socket
    replies: deque[str] = field(default_factory=deque)
    failures: deque[tuple[int, str]] = field(default_factory=deque)
    requests: list[dict[str, Any]] = field(default_factory=list)
    image_requests: list[dict[str, Any]] = field(default_factory=list)
    embedding_requests: list[dict[str, Any]] = field(default_factory=list)

    def queue(self, *texts: str) -> None:
        self.replies.extend(texts)

    def fail_next(self, status: int = 500, message: str = "fake failure") -> None:
        self.failures.append((status, message))

    @property
    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/v1/models", self._models, methods=["GET"]),
                Route("/v1/chat/completions", self._chat, methods=["POST"]),
                Route("/v1/images/generations", self._image, methods=["POST"]),
                Route("/v1/embeddings", self._embeddings, methods=["POST"]),
            ]
        )

    async def _embeddings(self, request: Request) -> JSONResponse:
        """Bag-of-word-stems vectors: texts sharing words get similar vectors, like a real model."""
        body = await request.json()
        self.embedding_requests.append(body)
        if self.failures:
            status, message = self.failures.popleft()
            return JSONResponse({"error": {"message": message, "type": "fake"}}, status_code=status)
        texts = body.get("input", [])
        texts = [texts] if isinstance(texts, str) else texts
        data = [{"object": "embedding", "index": i, "embedding": _word_vector(str(t))} for i, t in enumerate(texts)]
        return JSONResponse({"object": "list", "data": data, "model": body.get("model", MODEL_ID),
                             "usage": {"prompt_tokens": sum(len(str(t)) // 4 for t in texts), "total_tokens": 0}})

    async def _image(self, request: Request) -> JSONResponse:
        body = await request.json()
        self.image_requests.append(body)
        if self.failures:
            status, message = self.failures.popleft()
            return JSONResponse({"error": {"message": message, "type": "fake"}}, status_code=status)
        png = _pattern_png(str(body.get("prompt", "")))
        return JSONResponse({"created": int(time.time()), "data": [{"b64_json": base64.b64encode(png).decode()}]})

    def _next_reply(self, body: dict[str, Any]) -> str:
        if self.replies:
            return self.replies.popleft()
        last_user = next(
            (m.get("content") for m in reversed(body.get("messages", [])) if m.get("role") == "user"),
            "",
        )
        return f"Echo: {last_user}"

    async def _models(self, request: Request) -> JSONResponse:
        return JSONResponse({"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]})

    async def _chat(self, request: Request) -> Response:
        body = await request.json()
        self.requests.append(body)
        if self.failures:
            status, message = self.failures.popleft()
            return JSONResponse({"error": {"message": message, "type": "fake"}}, status_code=status)
        text = self._next_reply(body)
        usage = {
            "prompt_tokens": _rough_tokens(json.dumps(body.get("messages", []))),
            "completion_tokens": _rough_tokens(text),
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        created = int(time.time())
        if not body.get("stream"):
            return JSONResponse(
                {
                    "id": "fake-1",
                    "object": "chat.completion",
                    "created": created,
                    "model": body.get("model", MODEL_ID),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                }
            )
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        return StreamingResponse(
            self._stream(text, body.get("model", MODEL_ID), created, usage if include_usage else None),
            media_type="text/event-stream",
        )

    async def _stream(
        self, text: str, model: str, created: int, usage: dict[str, int] | None
    ) -> AsyncIterator[bytes]:
        def event(payload: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(payload)}\n\n".encode()

        base = {"id": "fake-1", "object": "chat.completion.chunk", "created": created, "model": model}
        yield event({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]})
        for piece in _split_keep_spaces(text):
            if self.chunk_delay:
                await anyio.sleep(self.chunk_delay)
            yield event({**base, "choices": [{"index": 0, "delta": {"content": piece}}]})
        yield event({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if usage is not None:
            yield event({**base, "choices": [], "usage": usage})
        yield b"data: [DONE]\n\n"


def _split_keep_spaces(text: str) -> list[str]:
    """'a b c' -> ['a', ' b', ' c']: joining the pieces gives the original text back."""
    parts = text.split(" ")
    return [parts[0], *(" " + p for p in parts[1:])] if text else []


def _rough_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _word_vector(text: str, size: int = 64) -> list[float]:
    import re

    vector = [0.0] * size
    for word in re.findall(r"\w+", text.lower()):
        root = word[:5]  # crude stem so word forms land together
        vector[int(hashlib.md5(root.encode()).hexdigest(), 16) % size] += 1.0
    return vector


def _pattern_png(prompt: str, size: int = 64) -> bytes:
    """A diagonal two-colour gradient; the colours come from the prompt's hash."""
    digest = hashlib.sha256(prompt.encode()).digest()
    a, b = digest[:3], digest[3:6]
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")  # filter type 0
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            row += bytes(round(a[i] * (1 - t) + b[i] * t) for i in range(3))
        rows.append(bytes(row))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
            ">I", binascii.crc32(kind + payload) & 0xFFFFFFFF
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b"")
    )


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Fake OpenAI-compatible LLM server")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--delay", type=float, default=0.05, help="seconds between stream chunks")
    args = parser.parse_args()
    uvicorn.run(FakeLLM(chunk_delay=args.delay).app, host="127.0.0.1", port=args.port, loop="asyncio")


if __name__ == "__main__":
    main()
