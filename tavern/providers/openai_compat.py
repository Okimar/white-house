"""OpenAI-compatible chat completions: OpenAI, OpenRouter, llama.cpp server, Ollama /v1, LM Studio,
KoboldCpp /v1, text-generation-webui and anything else that speaks `/chat/completions`.

`base_url` is the API root including `/v1` (e.g. https://api.openai.com/v1).

With an instruct template (connection setting `api_mode` = "text") the conversation is rendered
into one text (core/templates.py) and sent to `/completions` instead of `/chat/completions`;
the template's stop strings are added to the connection's own.

From the preset (SillyTavern's «custom» keys, docs/ST_PARITY.md §11a), read from `request.extra` and never
sent as fields themselves: `prompt_post_processing` (merge / semi / strict / single: what the messages look
like), `include_body` (fields added), `exclude_body` (fields removed, last of all) and `include_headers`
(extra headers; authorization, key, cookie, host and length headers are never taken from there). A seed
below 0 means random and is not sent; `n` is never sent (only one answer is read).
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import logging
import re
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import httpx

from tavern.core.templates import render
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

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 120.0  # max silence between chunks, not total time
IMAGE_TIMEOUT = 300.0  # picture models can take minutes
MAX_IMAGE_BYTES = 20 * 1024 * 1024


class OpenAICompatProvider(Provider):
    kind = "openai"

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        client: httpx.AsyncClient | None = None,
        template: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.template = template
        self._client = client or make_client(self.base_url)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def build_body(self, request: GenerationRequest, *, stream: bool) -> dict[str, Any]:
        if self.template is not None:
            prompt, stops = render(self.template, request.messages)
            body: dict[str, Any] = {"prompt": prompt, "stream": stream}
            request = replace(request, stop=[*request.stop, *(s for s in stops if s not in request.stop)])
        else:
            mode = request.extra.get("prompt_post_processing")
            body = {"messages": [_message(m) for m in post_process(request.messages, mode)], "stream": stream}
        if request.model:
            body["model"] = request.model
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.stop:
            # OpenAI's own API accepts at most 4; local text servers take more.
            body["stop"] = request.stop if self.template is not None else request.stop[:4]
        if request.response_format:
            body["response_format"] = _response_format(request.response_format)
        if stream:
            body["stream_options"] = {"include_usage": True}
        extra = dict(request.extra)
        raw = extra.pop("extra_body", None)
        include = loose_mapping(extra.pop("include_body", None))
        exclude = loose_keys(extra.pop("exclude_body", None))
        for name in (*P.PRESET_EXTRAS, "include_thoughts", "n"):
            extra.pop(name, None)  # ours, or (n) more answers than the one that is read
        seed = extra.get("seed")
        if "seed" in extra and (isinstance(seed, bool) or not isinstance(seed, (int, float)) or seed < 0):
            del extra["seed"]  # below 0 means "random"
        if "reasoning_effort" in extra:
            effort = P.effort(extra["reasoning_effort"])
            if effort in ("", "auto"):
                del extra["reasoning_effort"]  # "auto": the model decides, nothing is sent
            else:
                extra["reasoning_effort"] = "high" if effort == "max" else effort
        if extra.pop("web_search", False):
            # OpenRouter adds search as a plugin; OpenAI's own search models take web_search_options.
            if "openrouter.ai" in self.base_url:
                body["plugins"] = [{"id": "web"}]
            else:
                body["web_search_options"] = {}
        body.update(extra)
        body.update(include)  # the preset's own fields (SillyTavern's custom_include_body)
        if isinstance(raw, dict):
            body.update(raw)  # the connection's «Свои поля запроса», on top of everything
        for key in exclude:  # SillyTavern's custom_exclude_body: removed last of all
            body.pop(key, None)
        return body

    def request_headers(self, request: GenerationRequest) -> dict[str, str]:
        """Our headers plus the preset's extra ones (never over ours: those names are refused)."""
        return {**extra_headers(request.extra.get("include_headers")), **self._headers()}

    async def stream(self, request: GenerationRequest) -> AsyncIterator[StreamEvent]:
        body = self.build_body(request, stream=True)
        usage: Usage | None = None
        finish = ""
        try:
            async with self._client.stream(
                "POST", f"{self.base_url}/{'completions' if self.template is not None else 'chat/completions'}",
                json=body, headers=self.request_headers(request),
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
                        if not isinstance(choice, dict) or choice.get("index", 0) not in (0, None):
                            continue  # extra answers (an `n` in «Свои поля») would mix into the reply
                        if isinstance(choice.get("text"), str):  # text completion
                            if choice["text"]:
                                yield StreamEvent("text", choice["text"])
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
                            continue
                        part = choice.get("delta") or {}
                        thought = part.get("reasoning_content") or part.get("reasoning")
                        if isinstance(thought, str) and thought:
                            yield StreamEvent("reasoning", thought)
                        delta = part.get("content")
                        if delta:
                            yield StreamEvent("text", delta)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Timed out talking to {self.base_url}: {exc!r}", reason="network") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}", reason="network") from exc
        yield StreamEvent("done", usage=usage, finish_reason=finish)

    async def _model_entries(self) -> list[dict[str, Any]]:
        try:
            response = await self._client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}", reason="network") from exc
        if response.status_code >= 400:
            raise await _error_from(response)
        try:
            data = response.json().get("data", [])
        except (ValueError, AttributeError) as exc:
            raise ProviderError("The server returned a non-JSON model list") from exc
        return [m for m in data if isinstance(m, dict) and "id" in m]

    async def list_models(self) -> list[str]:
        return sorted(str(m["id"]) for m in await self._model_entries())

    async def describe_models(self) -> list[ModelInfo]:
        """OpenRouter-style lists also tell context size, accepted settings and prices."""
        return sorted((_model_info(m) for m in await self._model_entries()), key=lambda m: m.id)

    async def generate_image(self, prompt: str, *, model: str = "", size: str = "1024x1024") -> bytes:
        """POST /images/generations. The answer carries the picture inline (b64_json) or as a URL."""
        body: dict[str, Any] = {"prompt": prompt, "n": 1, "size": size}
        if model:
            body["model"] = model
        timeout = httpx.Timeout(IMAGE_TIMEOUT, connect=CONNECT_TIMEOUT)
        try:
            response = await self._client.post(
                f"{self.base_url}/images/generations", json=body, headers=self._headers(), timeout=timeout
            )
            if response.status_code >= 400:
                raise await _error_from(response)
            try:
                item = (response.json().get("data") or [{}])[0]
            except (ValueError, AttributeError, IndexError):
                raise ProviderError("The server returned an unexpected image response") from None
            if not isinstance(item, dict):
                raise ProviderError("The server returned an unexpected image response")
            if item.get("b64_json"):
                try:
                    return base64.b64decode(item["b64_json"], validate=True)
                except (ValueError, binascii.Error):
                    raise ProviderError("The server returned a damaged picture") from None
            url = item.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                raise ProviderError("The server returned no picture")
            picture = await self._client.get(url, timeout=timeout)
            if picture.status_code >= 400:
                raise ProviderError(f"Downloading the picture failed with {picture.status_code}")
            if len(picture.content) > MAX_IMAGE_BYTES:
                raise ProviderError("The generated picture is too large")
            return picture.content
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Timed out waiting for a picture from {self.base_url}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}") from exc

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        """POST /embeddings: one vector per text, in order."""
        body: dict[str, Any] = {"input": texts}
        if model:
            body["model"] = model
        try:
            response = await self._client.post(f"{self.base_url}/embeddings", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ProviderError(f"Cannot reach {self.base_url}: {exc!r}", reason="network") from exc
        if response.status_code >= 400:
            raise await _error_from(response)
        try:
            items = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in item["embedding"]] for item in items]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError("The server returned an unexpected embeddings response") from None
        if len(vectors) != len(texts):
            raise ProviderError("The server returned the wrong number of vectors")
        return vectors

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# OpenRouter's names for settings that are named differently here.
_ALIASES = {"reasoning": "reasoning_effort", "web_search_options": "web_search"}


def _message(message: Any) -> dict[str, Any]:
    named = {"name": message.name} if getattr(message, "name", "") else {}  # SillyTavern's example lines
    if not message.images:
        return {"role": message.role, "content": message.content, **named}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}] if message.content else []
    parts += [{"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}
              for mime, data in message.images]
    return {"role": message.role, "content": parts, **named}


PLACEHOLDER = "Let's get started."  # the user turn strict mode puts first (as SillyTavern)
POST_PROCESSING = {"merge": "merge", "semi": "semi", "semi-strict": "semi", "semi_strict": "semi",
                   "strict": "strict", "single": "single", "single_user": "single", "single-user": "single"}


def post_process(messages: list[ChatMessage], mode: Any) -> list[ChatMessage]:
    """SillyTavern's prompt post-processing (preset `custom_prompt_post_processing`), for servers that want
    simple conversations. merge: names into the text, neighbours of the same role joined with a blank line;
    semi: also every system message after the first one becomes user; strict: also a user turn
    ("Let's get started.") right after a leading system message or at the start; single: everything in one
    user message. The tool variants ("merge_tools" …) are the same here: no tools are sent. Anything else:
    the messages as they are."""
    key = str(mode or "").strip().lower().removesuffix("_tools").removesuffix("-tools")
    mode = POST_PROCESSING.get(key, "")
    if not mode:
        return messages
    plain = [replace(m, content=spoken(m), name="", speaker="") for m in messages]
    if mode == "single":
        return [ChatMessage("user", "\n\n".join(m.content for m in plain if m.content),
                            images=tuple(i for m in plain for i in m.images))]
    if mode in ("semi", "strict"):
        plain = [replace(m, role="user") if i and m.role == "system" else m for i, m in enumerate(plain)]
    merged: list[ChatMessage] = []
    for message in plain:
        if merged and merged[-1].role == message.role:
            before = merged[-1]
            text = "\n\n".join(t for t in (before.content, message.content) if t)
            merged[-1] = replace(before, content=text, images=before.images + message.images)
        else:
            merged.append(message)
    if mode == "strict":
        at = 1 if merged and merged[0].role == "system" else 0
        if at >= len(merged) or merged[at].role != "user":
            merged.insert(at, ChatMessage("user", PLACEHOLDER))
    return merged


def loose_mapping(raw: Any) -> dict[str, Any]:
    """An object from a preset field: a dict, JSON text, or flat YAML ("key: value" lines, values read as
    JSON when they can be). Nested YAML is not read (logged, nothing taken)."""
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        pass
    else:
        return value if isinstance(value, dict) else {}
    out: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace():
            log.warning("Only flat YAML is read in a preset's custom body / headers; this one is skipped")
            return {}
        key, sep, value = line.partition(":")
        if sep and key.strip():
            out[_unquote(key.strip())] = _scalar(value.strip())
    return out


def loose_keys(raw: Any) -> list[str]:
    """Field names from a preset field: a list, an object's keys, JSON text, or lines ("- key" / "key: …")."""
    if isinstance(raw, dict):
        return [str(k) for k in raw]
    if isinstance(raw, list):
        return [str(k) for k in raw if isinstance(k, (str, int))]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        pass
    else:
        return loose_keys(value) if isinstance(value, (dict, list)) else []
    keys: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("- ").strip()
        keys += [_unquote(k.strip()) for k in line.partition(":")[0].split(",") if k.strip()]
    return keys


def _unquote(text: str) -> str:
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'" else text


def _scalar(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return _unquote(text)


# Headers a preset may never set: ours (the key), and the ones that steer the connection itself.
_REFUSED_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "x-goog-api-key", "api-key", "cookie",
                    "host", "content-length", "content-type", "transfer-encoding", "connection"}
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}$")


def extra_headers(raw: Any) -> dict[str, str]:
    """The preset's extra headers (SillyTavern's custom_include_headers), checked: a bad or refused name, or a
    value with a line break, is dropped (logged by name only, never the value)."""
    out: dict[str, str] = {}
    for name, value in loose_mapping(raw).items():
        name = str(name).strip()
        text = value if isinstance(value, str) else json.dumps(value)
        if not _HEADER_NAME.match(name) or name.lower() in _REFUSED_HEADERS or "\r" in text or "\n" in text:
            log.warning("A preset header is not sent: %s", name[:100])
            continue
        out[name] = text
    return out


def _model_info(raw: dict[str, Any]) -> ModelInfo:
    info = ModelInfo(str(raw["id"]), name=str(raw.get("name") or ""))
    info.context = _int(raw.get("context_length") or raw.get("max_model_len") or raw.get("context_window"))
    top = raw.get("top_provider")
    if isinstance(top, dict):
        info.max_output = _int(top.get("max_completion_tokens"))
        info.context = info.context or _int(top.get("context_length"))
    supported = raw.get("supported_parameters")
    if isinstance(supported, list):
        names = {_ALIASES.get(str(n), str(n)) for n in supported}
        info.params = [p["id"] for p in P.PARAMS if p["id"] in names or not p.get("send", True)
                       or p["id"] == "extra_body"]
    pricing = raw.get("pricing")
    if isinstance(pricing, dict):
        info.price_in = _per_million(pricing.get("prompt"))
        info.price_out = _per_million(pricing.get("completion"))
    return info


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _per_million(per_token: Any) -> float | None:
    try:
        value = float(per_token)
    except (TypeError, ValueError):
        return None
    return round(value * 1_000_000, 6) if value >= 0 else None


def make_client(base_url: str, proxy: str = "") -> httpx.AsyncClient:
    """An HTTP client for one backend. An explicit proxy (http://, socks5://) beats the system one."""
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    if proxy:
        return httpx.AsyncClient(timeout=timeout, proxy=proxy, trust_env=False)
    return httpx.AsyncClient(timeout=timeout, trust_env=not direct_connection(base_url))


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
        elif isinstance(response.json().get("detail"), str):  # Voyage and others
            message = response.json()["detail"]
    except (ValueError, AttributeError):
        pass
    status = response.status_code
    reason = reason_for_status(status)
    if status == 400 and _looks_like_quota(message):
        reason = "limit"
    return ProviderError(f"Provider returned {status}: {message}", reason=reason, upstream_status=status)


def _looks_like_quota(message: str) -> bool:
    low = message.lower()
    return any(w in low for w in ("quota", "rate limit", "insufficient", "credit", "billing"))
