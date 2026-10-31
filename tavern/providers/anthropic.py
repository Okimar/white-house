"""Anthropic (Claude) through its own Messages API, or a proxy that speaks the same protocol.

`base_url` is the API root with the version, e.g. https://api.anthropic.com/v1. The key goes in
the `x-api-key` header. Leading system messages become `system`; later ones join the user turn
(the API has no system role inside the conversation). Turns of the same role are merged, and the
conversation must start with a user turn. Thinking blocks stream as "reasoning" events.

The API requires `max_tokens`; DEFAULT_MAX_TOKENS is used when the connection does not set it.
With thinking on, the API only takes the default sampling, so temperature and top_p/top_k are
left out. JSON mode (`response_format`) is a forced tool: Claude must "call" ANSWER_TOOL with the
answer as its input, which the API holds to the tool's schema. A schema whose root is not an object
is wrapped as {"value": ...} (tool input is always an object) and unwrapped again here. The tool's
input streams as JSON pieces and comes out as one text event at the end. With thinking on or web
search the API does not allow forcing a tool, so then the request goes in words only (LLMClient).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from tavern.providers import params as P
from tavern.providers.base import (
    ChatMessage,
    GenerationRequest,
    ModelInfo,
    Provider,
    ProviderError,
    StreamEvent,
    Usage,
    reason_for_status,
)
from tavern.providers.openai_compat import make_client

log = logging.getLogger(__name__)

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
MIN_THINKING = 1024  # the API's smallest thinking budget
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
ANSWER_TOOL = "answer"
WRAPPED_TOOL = "answer_value"  # the schema's root was not an object: the answer is input["value"]


def _answer_tool(fmt: dict[str, Any]) -> dict[str, Any]:
    schema = fmt.get("schema") if fmt.get("type") == "json_schema" else None
    if not isinstance(schema, dict):
        schema = {"type": "object"}
    if schema.get("type") == "object":
        return {"name": ANSWER_TOOL, "description": "Give the answer in the required shape.", "input_schema": schema}
    return {"name": WRAPPED_TOOL, "description": "Give the answer in the required shape, as `value`.",
            "input_schema": {"type": "object", "properties": {"value": schema}, "required": ["value"]}}


class AnthropicProvider(Provider):
    kind = "anthropic"

    def __init__(self, base_url: str, api_key: str = "", *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or make_client(self.base_url)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "anthropic-version": API_VERSION}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def build_body(self, request: GenerationRequest, *, stream: bool = True) -> dict[str, Any]:
        extra = dict(request.extra)
        system, messages = _messages(request.messages)
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": messages,
            "stream": stream,
        }
        if system:
            body["system"] = system
        budget = int(extra.pop("thinking_budget", 0) or 0)
        top_k = extra.pop("top_k", None)
        if budget:
            budget = max(MIN_THINKING, budget if budget > 0 else MIN_THINKING * 4)  # -1 = "let it decide"
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            # The answer's own room comes on top of the thinking budget.
            body["max_tokens"] = max(body["max_tokens"], budget + 1024)
        else:
            if request.temperature is not None:
                body["temperature"] = request.temperature
            if request.top_p is not None:
                body["top_p"] = request.top_p
            if top_k:
                body["top_k"] = top_k
        if request.stop:
            body["stop_sequences"] = request.stop
        if extra.pop("web_search", False):
            body["tools"] = [WEB_SEARCH_TOOL]
        elif request.response_format and "thinking" not in body:
            tool = _answer_tool(request.response_format)
            body["tools"] = [tool]
            body["tool_choice"] = {"type": "tool", "name": tool["name"]}
        raw = extra.pop("extra_body", None)
        if extra:
            log.debug("Anthropic ignores settings: %s", ", ".join(sorted(extra)))
        if isinstance(raw, dict):
            body.update(raw)
        return body

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request)
        prompt_tokens = completion_tokens = 0
        finish = ""
        wrote = False
        tool = (body.get("tool_choice") or {}).get("name", "")
        answer: list[str] = []  # the forced tool's input, streamed as pieces of JSON
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/messages", json=body, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    raise await _error_from(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        log.debug("Skipping non-JSON stream line: %r", line[:80])
                        continue
                    kind = event.get("type")
                    if kind == "message_start":
                        prompt_tokens = _prompt_tokens((event.get("message") or {}).get("usage") or {})
                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            wrote = True
                            yield StreamEvent("text", delta["text"])
                        elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                            yield StreamEvent("reasoning", delta["thinking"])
                        elif delta.get("type") == "input_json_delta" and tool:
                            answer.append(delta.get("partial_json") or "")
                    elif kind == "message_delta":
                        finish = (event.get("delta") or {}).get("stop_reason") or finish
                        usage = event.get("usage") or {}
                        completion_tokens = int(usage.get("output_tokens") or completion_tokens)
                        prompt_tokens = _prompt_tokens(usage) or prompt_tokens
                    elif kind == "error":
                        err = event.get("error") or {}
                        reason = "server" if err.get("type") in ("overloaded_error", "api_error") else ""
                        raise ProviderError(f"Anthropic: {err.get('message') or err.get('type')}", reason=reason)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Timed out talking to {self.base_url}: {exc!r}", reason="network") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}", reason="network") from exc
        if answer:
            wrote = True
            yield StreamEvent("text", _tool_answer("".join(answer), tool))
        if finish == "refusal" and not wrote:
            raise ProviderError("Claude refused to answer", reason="refused")
        yield StreamEvent("done", usage=Usage(prompt_tokens, completion_tokens), finish_reason=finish)

    async def _model_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        after = ""
        for _ in range(20):  # pages
            query: dict[str, Any] = {"limit": 1000}
            if after:
                query["after_id"] = after
            try:
                response = await self._client.get(f"{self.base_url}/models", params=query, headers=self._headers())
            except httpx.HTTPError as exc:
                raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}", reason="network") from exc
            if response.status_code >= 400:
                raise await _error_from(response)
            try:
                data = response.json()
            except ValueError as exc:
                raise ProviderError("The server returned a non-JSON model list") from exc
            entries += [m for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]
            after = data.get("last_id") or ""
            if not data.get("has_more") or not after:
                break
        return entries

    async def list_models(self) -> list[str]:
        return sorted(str(m["id"]) for m in await self._model_entries())

    async def describe_models(self) -> list[ModelInfo]:
        return sorted((_model_info(m) for m in await self._model_entries()), key=lambda m: m.id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _content(message: ChatMessage) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = [
        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
        for mime, data in message.images
    ]
    if message.content or not parts:
        parts.append({"type": "text", "text": message.content or "."})
    return parts


def _messages(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    system: list[str] = []
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system" and not out:
            system.append(message.content)
            continue
        role = "assistant" if message.role == "assistant" else "user"
        if out and out[-1]["role"] == role:
            out[-1]["content"] += _content(message)
        else:
            out.append({"role": role, "content": _content(message)})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": "."}]})
    return "\n\n".join(system), out


def _tool_answer(raw: str, tool: str) -> str:
    """The forced tool's input as the reply text (unwrapped when the schema was wrapped)."""
    if tool != WRAPPED_TOOL:
        return raw
    try:
        return json.dumps(json.loads(raw).get("value"), ensure_ascii=False)
    except (json.JSONDecodeError, AttributeError):
        return raw  # LLMClient reports it as not fitting


def _prompt_tokens(usage: dict[str, Any]) -> int:
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def _model_info(raw: dict[str, Any]) -> ModelInfo:
    info = ModelInfo(str(raw["id"]), name=str(raw.get("display_name") or ""))
    info.context = int(raw.get("max_input_tokens") or 0)
    info.max_output = int(raw.get("max_tokens") or 0)
    info.params = [p["id"] for p in P.PARAMS if "anthropic" in p["kinds"]]
    info.limits["temperature"] = [0, 1]
    if info.max_output:
        info.limits["max_tokens"] = [1, info.max_output]
    return info


async def _error_from(response: httpx.Response) -> ProviderError:
    await response.aread()
    message = response.text[:300].strip() or response.reason_phrase or "empty response"
    try:
        err = response.json().get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
    except (ValueError, AttributeError):
        pass
    status = response.status_code
    reason = reason_for_status(status)
    if status == 529:
        reason = "server"
    elif status == 400 and "credit balance" in message.lower():
        reason = "limit"
    return ProviderError(f"Anthropic returned {status}: {message}", reason=reason, upstream_status=status)
