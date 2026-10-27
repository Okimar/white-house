"""OpenAI-compatible chat completions: OpenAI, OpenRouter, llama.cpp server, Ollama /v1, LM Studio,
KoboldCpp /v1, text-generation-webui and anything else that speaks `/chat/completions`.

`base_url` is the API root including `/v1` (e.g. https://api.openai.com/v1).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

import httpx

from tavern.providers.base import (
    GenerationRequest,
    Provider,
    ProviderError,
    StreamEvent,
    Usage,
)

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 120.0  # max silence between chunks, not total time


class OpenAICompatProvider(Provider):
    kind = "openai"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT),
            trust_env=not direct_connection(self.base_url),
        )
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def build_body(self, request: GenerationRequest, *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": stream,
        }
        if request.model:
            body["model"] = request.model
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.stop:
            body["stop"] = request.stop[:4]  # OpenAI accepts at most 4
        if request.response_format:
            body["response_format"] = _response_format(request.response_format)
        if stream:
            body["stream_options"] = {"include_usage": True}
        body.update(request.extra)
        return body

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request, stream=True)
        usage: Usage | None = None
        finish = ""
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/chat/completions", json=body, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    raise await _error_from(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        log.debug("Skipping non-JSON stream line: %r", payload[:80])
                        continue
                    if chunk.get("usage"):
                        usage = _usage(chunk["usage"])
                    for choice in chunk.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            yield StreamEvent("text", delta)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Timed out talking to {self.base_url}: {exc!r}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}") from exc
        yield StreamEvent("done", usage=usage, finish_reason=finish)

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}") from exc
        if response.status_code >= 400:
            raise await _error_from(response)
        try:
            data = response.json().get("data", [])
        except ValueError as exc:
            raise ProviderError("The server returned a non-JSON model list") from exc
        return sorted(str(m["id"]) for m in data if isinstance(m, dict) and "id" in m)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _response_format(fmt: dict[str, Any]) -> dict[str, Any]:
    if fmt.get("type") == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": fmt.get("name", "response"),
                "schema": fmt["schema"],
                "strict": bool(fmt.get("strict", False)),
            },
        }
    return {"type": fmt.get("type", "json_object")}


def _usage(raw: dict[str, Any]) -> Usage:
    return Usage(int(raw.get("prompt_tokens") or 0), int(raw.get("completion_tokens") or 0))


def direct_connection(base_url: str) -> bool:
    """True when requests to this URL must bypass the system HTTP proxy.

    httpx reads the proxy from the environment (on Windows from the registry) but not the
    bypass list, so a local backend would be sent through the proxy, which answers 503.
    Local and private addresses always go direct; other hosts follow the OS bypass list.
    """
    host = httpx.URL(base_url).host
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return address.is_loopback or address.is_private or address.is_link_local
    try:
        return bool(urllib.request.proxy_bypass(host))
    except OSError:
        return False


async def _error_from(response: httpx.Response) -> ProviderError:
    await response.aread()
    message = response.text[:300].strip() or response.reason_phrase or "empty response"
    try:
        err = response.json().get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
        elif isinstance(err, str):
            message = err
    except ValueError:
        pass
    return ProviderError(f"Provider returned {response.status_code}: {message}")
