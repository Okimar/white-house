"""A scriptable fake of an OpenAI-compatible LLM server. No network, no API keys.

In tests, mount it in-process:

    fake = FakeLLM()
    fake.queue("Hello!", "Second reply")
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")

Standalone, for manual testing in the browser:

    python -m tavern.devtools.fake_llm --port 5001
    # then point an OpenAI-compatible connection at http://127.0.0.1:5001/v1

Without queued replies it answers "Echo: <last user message>".
"""

from __future__ import annotations

import argparse
import json
import time
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
            ]
        )

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


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Fake OpenAI-compatible LLM server")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--delay", type=float, default=0.05, help="seconds between stream chunks")
    args = parser.parse_args()
    uvicorn.run(FakeLLM(chunk_delay=args.delay).app, host="127.0.0.1", port=args.port, loop="asyncio")


if __name__ == "__main__":
    main()
