"""Google Gemini through its own API (AI Studio, or a proxy that speaks the same protocol).

`base_url` is the API root with the version, e.g. https://generativelanguage.googleapis.com/v1beta.
Streaming uses `models/{model}:streamGenerateContent?alt=sse`. The key goes in the
`x-goog-api-key` header, never in the address, so it cannot leak into logs with a URL.

System messages become `systemInstruction`; the rest become `contents` with roles "user"/"model",
neighbours of the same role merged (Gemini wants turns to alternate). Thought parts (`"thought":
true`) are sent as "reasoning" events, never mixed into the reply text.
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
_CONFIG_NAMES = {
    "temperature": "temperature", "top_p": "topP", "top_k": "topK", "max_tokens": "maxOutputTokens",
    "presence_penalty": "presencePenalty", "frequency_penalty": "frequencyPenalty", "seed": "seed",
}
_EFFORT_LEVELS = {"minimal": "low", "low": "low", "medium": "high", "high": "high"}


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
        system, contents = _contents(request.messages)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        config: dict[str, Any] = {}
        if request.max_tokens:
            config["maxOutputTokens"] = request.max_tokens
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.top_p is not None:
            config["topP"] = request.top_p
        if request.stop:
            config["stopSequences"] = request.stop[:5]  # Gemini accepts at most 5
        for ours, theirs in _CONFIG_NAMES.items():
            if ours in extra:
                value = extra.pop(ours)
                if ours == "seed" and value == -1:
                    continue  # -1 means "random" in the settings window
                if ours == "top_k" and not value:
                    continue
                config[theirs] = value
        thinking: dict[str, Any] = {}
        if "thinking_budget" in extra:
            thinking["thinkingBudget"] = int(extra.pop("thinking_budget"))
        effort = extra.pop("reasoning_effort", None)
        if effort == "none":
            thinking["thinkingBudget"] = 0
        elif effort in _EFFORT_LEVELS and "thinkingBudget" not in thinking:
            thinking["thinkingLevel"] = _EFFORT_LEVELS[effort]
        if extra.pop("include_thoughts", False):
            thinking["includeThoughts"] = True
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
        if extra.pop("safety_off", False):
            body["safetySettings"] = [{"category": c, "threshold": "OFF"} for c in SAFETY_CATEGORIES]
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


def _contents(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    """Leading system messages -> systemInstruction; later ones become user turns (Gemini has no
    system role inside the conversation)."""
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system" and not contents:
            system.append(message.content)
            continue
        role = "model" if message.role == "assistant" else "user"
        parts: list[dict[str, Any]] = [{"inlineData": {"mimeType": mime, "data": data}} for mime, data in message.images]
        if message.content or not parts:
            parts.append({"text": message.content})
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"] += parts
        else:
            contents.append({"role": role, "parts": parts})
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "."}]})  # Gemini needs at least one turn
    return "\n\n".join(system), contents


def _usage(raw: dict[str, Any]) -> Usage:
    out = int(raw.get("candidatesTokenCount") or 0) + int(raw.get("thoughtsTokenCount") or 0)
    return Usage(int(raw.get("promptTokenCount") or 0), out)


def _model_info(raw: dict[str, Any]) -> ModelInfo:
    info = ModelInfo(_short(raw["name"]), name=str(raw.get("displayName") or ""))
    info.context = int(raw.get("inputTokenLimit") or 0)
    info.max_output = int(raw.get("outputTokenLimit") or 0)
    accepted = {"temperature", "top_p", "max_tokens", "stop", "presence_penalty", "frequency_penalty",
                "seed", "safety_off", "web_search", "extra_body"}
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
