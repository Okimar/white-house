"""Google Gemini through its own API (AI Studio, or a proxy that speaks the same protocol).

`base_url` is the API root with the version, e.g. https://generativelanguage.googleapis.com/v1beta.
Streaming uses `models/{model}:streamGenerateContent?alt=sse`. The key goes in the
`x-goog-api-key` header, never in the address, so it cannot leak into logs with a URL.

The body follows SillyTavern's (docs/ST_PARITY.md §11b): with `use_sysprompt` on (our default when the
preset does not say) the leading system messages go to `systemInstruction`, one part each, while at least
one message is left for `contents`; off (an imported SillyTavern preset's default) every system message is a
user turn. Gemma and picture models never get a systemInstruction. Other roles become "user" / "model";
neighbours of the same role are joined into the first text part with a blank line; a named example line gets
"Speaker: " in front. Safety filters are sent OFF unless the connection says `safety_off: false`.
generationConfig: candidateCount 1, topK only above 0, at most 5 stop strings of 1–16 characters, seed only
when ≥ 0, no penalties; thinking by model family (2.5: a budget, 3: a level; pro / flash differ) with the
thoughts sent back unless `include_thoughts` is false. Thought parts (`"thought": true`) are sent as
"reasoning" events, never mixed into the reply text.
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

SAFETY_CATEGORIES = (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
)
# Answers stopped for these reasons are refusals, not ordinary ends.
REFUSAL_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}

# Settings that go into generationConfig, by our name -> Gemini's name.
_CONFIG_NAMES = {"temperature": "temperature", "top_p": "topP", "top_k": "topK", "max_tokens": "maxOutputTokens",
                 "seed": "seed"}
# Settings Gemini never gets (SillyTavern does not send penalties; newer models reject them).
_NOT_SENT = ("presence_penalty", "frequency_penalty", *P.PRESET_EXTRAS)
MAX_STOPS = 5
MAX_STOP_LENGTH = 16
# Gemini 3: thinking level by effort, pro and flash apart (pro has only low / high).
_LEVELS_PRO = {"none": "low", "minimal": "low", "low": "low", "medium": "low", "high": "high", "max": "high"}
_LEVELS_FLASH = {"none": "minimal", "minimal": "minimal", "low": "low", "medium": "medium", "high": "high",
                 "max": "high"}
# Gemini 2.5: thinking budget by effort ("auto" = -1, dynamic). Pro cannot go below 128.
_BUDGETS = {"low": 1024, "medium": 8192, "high": 24576}
_PRO_MAX, _FLASH_MAX, _PRO_MIN = 32768, 24576, 128


def _family(model: str) -> tuple[tuple[int, int], bool]:
    """((major, minor), is_pro) of a Gemini model name; (0, 0) when it is not a Gemini 2.5 / 3 name."""
    found = re.search(r"gemini-(\d+)(?:\.(\d+))?", model.lower())
    if not found:
        return (0, 0), False
    return (int(found.group(1)), int(found.group(2) or 0)), "pro" in model.lower()


def _thinking(model: str, effort: str, budget: int | None, include: bool | None) -> dict[str, Any]:
    """thinkingConfig. An explicit budget (connection setting) wins over the effort; the effort is
    mapped only for the families that take it (2.5, 3), like SillyTavern."""
    version, pro = _family(model)
    known = version >= (2, 5)
    config: dict[str, Any] = {}
    if budget is not None:
        config["thinkingBudget"] = budget
    elif version >= (3, 0) and effort in _LEVELS_PRO:
        config["thinkingLevel"] = (_LEVELS_PRO if pro else _LEVELS_FLASH)[effort]
    elif version == (2, 5) and effort:
        if effort == "auto":
            config["thinkingBudget"] = -1
        elif effort in ("none", "minimal"):
            config["thinkingBudget"] = _PRO_MIN if pro else 0
        elif effort == "max":
            config["thinkingBudget"] = _PRO_MAX if pro else _FLASH_MAX
        else:
            config["thinkingBudget"] = _BUDGETS[effort]
    # Thoughts come back by default on thinking families (SillyTavern's «show thoughts» is on by default).
    if (include if include is not None else known) and config.get("thinkingBudget") != 0:
        config["includeThoughts"] = True
    return config


class GeminiProvider(Provider):
    kind = "gemini"

    def __init__(self, base_url: str, api_key: str = "", *, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or make_client(self.base_url)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def _model_path(self, model: str) -> str:
        if not model:
            raise ProviderError("Choose a Gemini model for this connection", reason="request")
        return model if model.startswith(("models/", "tunedModels/")) else f"models/{model}"

    def build_body(self, request: GenerationRequest) -> dict[str, Any]:
        extra = dict(request.extra)
        model = request.model.lower()
        # Gemma and picture models take no systemInstruction.
        use_system = extra.pop("use_sysprompt", None) is not False and "gemma" not in model and "image" not in model
        system, contents = _contents(request.messages, use_system=use_system)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": text} for text in system]}
        config: dict[str, Any] = {"candidateCount": 1}
        if request.max_tokens:
            config["maxOutputTokens"] = request.max_tokens
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.top_p is not None:
            config["topP"] = request.top_p
        stops = [s for s in request.stop if 1 <= len(s) <= MAX_STOP_LENGTH][:MAX_STOPS]
        if stops:
            config["stopSequences"] = stops
        for ours, theirs in _CONFIG_NAMES.items():
            if ours in extra:
                value = extra.pop(ours)
                if ours in ("seed", "top_k") and (not isinstance(value, (int, float)) or value < (0 if ours == "seed" else 1)):
                    continue  # seed -1 = random; top_k 0 = off
                config[theirs] = value
        budget = extra.pop("thinking_budget", None)
        include = extra.pop("include_thoughts", None)
        thinking = _thinking(model, P.effort(extra.pop("reasoning_effort", None)),
                             int(budget) if isinstance(budget, (int, float)) else None,
                             include if isinstance(include, bool) else None)
        if thinking:
            config["thinkingConfig"] = thinking
        if request.response_format:
            config["responseMimeType"] = "application/json"
            if request.response_format.get("type") == "json_schema":
                config["responseJsonSchema"] = request.response_format["schema"]
        if config:
            body["generationConfig"] = config
        if extra.pop("web_search", False):
            body["tools"] = [{"google_search": {}}]
        # Filters OFF unless the connection says otherwise: SillyTavern always sends them OFF.
        if extra.pop("safety_off", True) is not False:
            body["safetySettings"] = [{"category": c, "threshold": "OFF"} for c in SAFETY_CATEGORIES]
        for name in _NOT_SENT:
            extra.pop(name, None)
        raw = extra.pop("extra_body", None)
        # Leftover settings belong to other kinds (stale names in a connection); Gemini rejects
        # unknown fields, so they are dropped rather than sent.
        if extra:
            log.debug("Gemini ignores settings: %s", ", ".join(sorted(extra)))
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key == "generationConfig" and isinstance(value, dict):
                    body.setdefault("generationConfig", {}).update(value)
                else:
                    body[key] = value
        return body

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request)
        url = f"{self.base_url}/{self._model_path(request.model)}:streamGenerateContent"
        usage: Usage | None = None
        finish = ""
        wrote = False
        try:
            async with self._client.stream(
                "POST", url, params={"alt": "sse"}, json=body, headers=self._headers()
            ) as response:
                if response.status_code >= 400:
                    raise await _error_from(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        chunk = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        log.debug("Skipping non-JSON stream line: %r", line[:80])
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    blocked = (chunk.get("promptFeedback") or {}).get("blockReason")
                    if blocked:
                        raise ProviderError(f"Gemini blocked the request: {blocked}", reason="refused")
                    if chunk.get("usageMetadata"):
                        usage = _usage(chunk["usageMetadata"])
                    for candidate in chunk.get("candidates") or []:
                        for part in (candidate.get("content") or {}).get("parts") or []:
                            text = part.get("text")
                            if not isinstance(text, str) or not text:
                                continue
                            if part.get("thought"):
                                yield StreamEvent("reasoning", text)
                            else:
                                wrote = True
                                yield StreamEvent("text", text)
                        if candidate.get("finishReason"):
                            finish = str(candidate["finishReason"])
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Timed out talking to {self.base_url}: {exc!r}", reason="network") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}", reason="network") from exc
        if finish in REFUSAL_REASONS and not wrote:
            raise ProviderError(f"Gemini refused to answer: {finish}", reason="refused")
        yield StreamEvent("done", usage=usage, finish_reason=finish.lower())

    async def _model_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        token = ""
        for _ in range(20):  # pages; a real list has a few hundred models at most
            query: dict[str, Any] = {"pageSize": 1000}
            if token:
                query["pageToken"] = token
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
            entries += [m for m in data.get("models") or [] if isinstance(m, dict) and m.get("name")]
            token = data.get("nextPageToken") or ""
            if not token:
                break
        return [m for m in entries if "generateContent" in (m.get("supportedGenerationMethods") or ["generateContent"])]

    async def list_models(self) -> list[str]:
        return sorted(_short(m["name"]) for m in await self._model_entries())

    async def describe_models(self) -> list[ModelInfo]:
        return sorted((_model_info(m) for m in await self._model_entries()), key=lambda m: m.id)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _short(name: str) -> str:
    return name.removeprefix("models/")


def _contents(messages: list[ChatMessage], *, use_system: bool = True) -> tuple[list[str], list[dict[str, Any]]]:
    """(systemInstruction texts, contents). With `use_system` the leading system messages are lifted out,
    one part each, while at least one message is left; every other system message is a user turn (Gemini
    has no system role inside the conversation). A turn of the same role as the one before is joined into
    its first text part with a blank line; pictures go in as their own parts."""
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        text = spoken(message)  # a named example line gets its speaker in front (Gemini has no names)
        if use_system and message.role == "system" and not contents and index < len(messages) - 1:
            system.append(text)
            continue
        role = "model" if message.role == "assistant" else "user"
        pictures = [{"inlineData": {"mimeType": mime, "data": data}} for mime, data in message.images]
        if contents and contents[-1]["role"] == role:
            parts = contents[-1]["parts"]
            first = next((p for p in parts if "text" in p), None)
            if text and first is not None:
                first["text"] = f"{first['text']}\n\n{text}" if first["text"] else text
            elif text:
                parts.append({"text": text})
            parts += pictures
            continue
        parts = [*pictures]
        if text or not parts:
            parts.append({"text": text})
        contents.append({"role": role, "parts": parts})
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "."}]})  # Gemini needs at least one turn
    return system, contents


def _usage(raw: dict[str, Any]) -> Usage:
    out = int(raw.get("candidatesTokenCount") or 0) + int(raw.get("thoughtsTokenCount") or 0)
    return Usage(int(raw.get("promptTokenCount") or 0), out)


def _model_info(raw: dict[str, Any]) -> ModelInfo:
    info = ModelInfo(_short(raw["name"]), name=str(raw.get("displayName") or ""))
    info.context = int(raw.get("inputTokenLimit") or 0)
    info.max_output = int(raw.get("outputTokenLimit") or 0)
    accepted = {"temperature", "top_p", "max_tokens", "stop", "seed", "safety_off", "web_search", "extra_body"}
    if raw.get("topK") is not None:
        accepted.add("top_k")
    if raw.get("thinking"):
        accepted |= {"thinking_budget", "include_thoughts", "reasoning_effort"}
    info.params = [p["id"] for p in P.PARAMS if p["id"] in accepted or not p.get("send", True)]
    if raw.get("maxTemperature") is not None:
        info.limits["temperature"] = [0, raw["maxTemperature"]]
    defaults = {k: raw[k] for k in ("temperature", "topP", "topK") if raw.get(k) is not None}
    if defaults:
        info.notes.append("По умолчанию: " + ", ".join(f"{k} {v}" for k, v in defaults.items()))
    if info.max_output:
        info.limits["max_tokens"] = [1, info.max_output]
    return info


async def _error_from(response: httpx.Response) -> ProviderError:
    await response.aread()
    message = response.text[:300].strip() or response.reason_phrase or "empty response"
    try:
        body = response.json()
        err = body[0].get("error") if isinstance(body, list) and body else body.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
    except (ValueError, AttributeError):
        pass
    status = response.status_code
    reason = reason_for_status(status)
    if status == 400 and "api key" in message.lower():
        reason = "auth"
    return ProviderError(f"Gemini returned {status}: {message}", reason=reason, upstream_status=status)
