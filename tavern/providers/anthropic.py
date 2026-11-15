"""Anthropic (Claude) through its own Messages API, or a proxy that speaks the same protocol.

`base_url` is the API root with the version, e.g. https://api.anthropic.com/v1. The key goes in
the `x-api-key` header. The body follows SillyTavern's (docs/ST_PARITY.md §11c): with `use_sysprompt` on
(our default when the preset does not say) the leading system messages become `system`, one text block
each; off (an imported SillyTavern preset's default) every system message is a user turn. Later system
messages are user turns; a named example line gets "Speaker: " in front; turns of the same role are
merged; pictures of assistant turns move to the next user turn; an empty text is a zero-width space; the
conversation starts with a user turn ("Let's get started." when there is none). The last assistant turn
(a prefill) loses its trailing spaces and becomes a user turn when thinking is on or on models from 4.6,
which take no prefill. Thinking blocks stream as "reasoning" events.

The API requires `max_tokens`; DEFAULT_MAX_TOKENS is used when the connection does not set it.
Thinking: models from 4.6 think "adaptive" with `output_config.effort` from the reasoning effort (5.x
think anyway: the summary of their thinking is asked for unless `include_thoughts` is false); older ones
get a budget (the connection's, or a share of max_tokens by effort). Sampling: none with thinking or on
models from 4.7, only one of temperature / top_p from 4.1. JSON mode (`response_format`) is a forced
tool: Claude must "call" ANSWER_TOOL with the
answer as its input, which the API holds to the tool's schema. A schema whose root is not an object
is wrapped as {"value": ...} (tool input is always an object) and unwrapped again here. The tool's
input streams as JSON pieces and comes out as one text event at the end. With thinking on, web
search or a 5.x model the API does not allow forcing a tool, so then the request goes in words only
(LLMClient).
"""

from __future__ import annotations

import json
import logging
import re
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
    spoken,
)
from tavern.providers.openai_compat import make_client

log = logging.getLogger(__name__)

API_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
MIN_THINKING = 1024  # the API's smallest thinking budget
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
ANSWER_TOOL = "answer"
WRAPPED_TOOL = "answer_value"  # the schema's root was not an object: the answer is input["value"]
PLACEHOLDER = "Let's get started."  # the user turn when the conversation has none first (as SillyTavern)
EMPTY_TEXT = "​"  # the API rejects empty text blocks
MAX_THINKING_NO_STREAM = 21333  # a bigger budget needs streaming
# Old models (budget thinking): the share of max_tokens each effort thinks; "minimal" = MIN_THINKING.
EFFORT_SHARES = {"low": 0.10, "medium": 0.25, "high": 0.50, "max": 0.95}
ADAPTIVE_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "max": "max"}
ADAPTIVE_FROM = (4, 6)  # adaptive thinking, no prefill
NO_SAMPLING_FROM = (4, 7)  # temperature / top_p / top_k rejected
ONE_SAMPLING_FROM = (4, 1)  # temperature and top_p not together
THINKS_ALWAYS_FROM = (5, 0)  # thinking on even when not asked; no forced tool


def model_version(model: str) -> tuple[int, int]:
    """(major, minor) from a Claude model name: claude-opus-4-6 -> (4, 6), claude-3-7-sonnet-20250219 ->
    (3, 7), claude-opus-4-20250514 -> (4, 0), anthropic.claude-sonnet-4-5-v1:0 -> (4, 5); (0, 0) when the
    name has no version (a proxy's own name): treated as an old model."""
    numbers = [int(t) for t in re.split(r"[-./:@_]", model.lower()) if t.isdigit() and len(t) <= 2]
    if not numbers:
        return (0, 0)
    return (numbers[0], numbers[1] if len(numbers) > 1 else 0)


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
        version = model_version(request.model or "")
        system, messages = _messages(request.messages, use_system=extra.pop("use_sysprompt", None) is not False)
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": messages,
            "stream": stream,
        }
        if system:
            body["system"] = [{"type": "text", "text": text} for text in system]
        budget = extra.pop("thinking_budget", 0)
        include = extra.pop("include_thoughts", None)
        self._thinking(body, version, int(budget) if isinstance(budget, (int, float)) else 0,
                       P.effort(extra.pop("reasoning_effort", None)), include is not False, stream)
        top_k = extra.pop("top_k", None)
        if "thinking" not in body and version < NO_SAMPLING_FROM:
            temperature, top_p = request.temperature, request.top_p
            if version >= ONE_SAMPLING_FROM and temperature is not None and top_p is not None:
                # These models take only one: top_p when it narrows anything, else temperature.
                temperature, top_p = (None, top_p) if top_p < 1 else (temperature, None)
            if temperature is not None:
                body["temperature"] = temperature
            if top_p is not None:
                body["top_p"] = top_p
            if isinstance(top_k, (int, float)) and top_k > 0:
                body["top_k"] = top_k
        _last_turn(messages, flip="thinking" in body or version >= ADAPTIVE_FROM)
        if request.stop:
            body["stop_sequences"] = request.stop
        if extra.pop("web_search", False):
            body["tools"] = [WEB_SEARCH_TOOL]
        elif request.response_format and "thinking" not in body and version < THINKS_ALWAYS_FROM:
            tool = _answer_tool(request.response_format)
            body["tools"] = [tool]
            body["tool_choice"] = {"type": "tool", "name": tool["name"]}
        for name in P.PRESET_EXTRAS:
            extra.pop(name, None)
        raw = extra.pop("extra_body", None)
        if extra:
            log.debug("Anthropic ignores settings: %s", ", ".join(sorted(extra)))
        if isinstance(raw, dict):
            body.update(raw)
        return body

    @staticmethod
    def _thinking(body: dict[str, Any], version: tuple[int, int], budget: int, effort: str, show: bool,
                  stream: bool) -> None:
        """Turn thinking on in `body` when asked (a budget, or an effort other than none / auto)."""
        asked = bool(budget) or effort in ADAPTIVE_EFFORT
        if version >= ADAPTIVE_FROM:
            if not asked and not (version >= THINKS_ALWAYS_FROM and show and effort != "none"):
                return
            thinking: dict[str, Any] = {"type": "adaptive"}
            if version >= NO_SAMPLING_FROM and show:
                thinking["display"] = "summarized"  # these models send empty thinking unless asked
            body["thinking"] = thinking
            if effort in ADAPTIVE_EFFORT:
                body["output_config"] = {"effort": ADAPTIVE_EFFORT[effort]}
            return
        if not asked:
            return
        if budget:
            size = budget if budget > 0 else MIN_THINKING * 4  # -1 = "let it decide"
        elif effort == "minimal":
            size = MIN_THINKING
        else:
            size = int(body["max_tokens"] * EFFORT_SHARES[effort])
        size = max(MIN_THINKING, size)
        if not stream:
            size = min(size, MAX_THINKING_NO_STREAM)
        body["thinking"] = {"type": "enabled", "budget_tokens": size}
        # The answer's own room comes on top of the thinking budget.
        body["max_tokens"] = max(body["max_tokens"], size + 1024)

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


def _pictures(images: Any) -> list[dict[str, Any]]:
    return [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}} for mime, data in images]


def _messages(messages: list[ChatMessage], *, use_system: bool = True) -> tuple[list[str], list[dict[str, Any]]]:
    """(system texts, turns). Pictures of an assistant turn (the API takes none there) go to the next user turn."""
    system: list[str] = []
    out: list[dict[str, Any]] = []
    carried: list[dict[str, Any]] = []
    for message in messages:
        text = spoken(message)  # a named example line: speaker in front
        if use_system and message.role == "system" and not out:
            if text:
                system.append(text)
            continue
        role = "assistant" if message.role == "assistant" else "user"
        if role == "assistant":
            carried += _pictures(message.images)
            parts: list[dict[str, Any]] = []
        else:
            parts, carried = [*carried, *_pictures(message.images)], []
        if text or not parts:
            parts.append({"type": "text", "text": text or EMPTY_TEXT})
        if out and out[-1]["role"] == role:
            out[-1]["content"] += parts
        else:
            out.append({"role": role, "content": parts})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": PLACEHOLDER}]})
    return system, out


def _last_turn(turns: list[dict[str, Any]], *, flip: bool) -> None:
    """A final assistant turn (a prefill) loses its trailing spaces (the API rejects them); with `flip` (thinking
    on, or a model without prefill) it becomes a user turn, joined to the user turn before it."""
    last = turns[-1]
    if last["role"] != "assistant":
        return
    texts = [p for p in last["content"] if p.get("type") == "text"]
    if texts:
        texts[-1]["text"] = texts[-1]["text"].rstrip() or EMPTY_TEXT
    if not flip:
        return
    if len(turns) > 1 and turns[-2]["role"] == "user":
        turns[-2]["content"] += last["content"]
        turns.pop()
    else:
        last["role"] = "user"


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
