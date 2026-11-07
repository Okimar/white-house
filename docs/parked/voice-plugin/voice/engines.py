"""Requests to the voice services: speech to text and text to speech.

Services: any OpenAI-compatible endpoint (OpenAI, Groq, a local whisper server ...) and ElevenLabs.
The keys come in as arguments, go into a header and nowhere else; every message that leaves this
module has the key scrubbed from it. Failures are explained in plain Russian: no key, wrong key,
out of money, too many requests, no network.
`transport` is swapped by tests for a fake server; nothing here needs the internet then.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from tavern.errors import AppError

ELEVEN_URL = "https://api.elevenlabs.io"
SCRIBE_MODEL = "scribe_v1"
TIMEOUT = httpx.Timeout(90.0, connect=10.0)
MAX_AUDIO_OUT = 25 * 1024 * 1024  # the biggest spoken piece we accept back
transport: httpx.AsyncBaseTransport | None = None  # tests swap the network out

SERVICE_NAMES = {"openai": "сервис распознавания", "elevenlabs": "ElevenLabs", "openai_tts": "сервис озвучки"}
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"]
AUDIO_FILES = {  # recording type -> file name and type to send
    "audio/webm": ("audio.webm", "audio/webm"),
    "audio/ogg": ("audio.ogg", "audio/ogg"),
    "audio/mp4": ("audio.mp4", "audio/mp4"),
    "audio/x-m4a": ("audio.m4a", "audio/mp4"),
    "audio/aac": ("audio.m4a", "audio/mp4"),
    "audio/mpeg": ("audio.mp3", "audio/mpeg"),
    "audio/wav": ("audio.wav", "audio/wav"),
    "audio/x-wav": ("audio.wav", "audio/wav"),
}
VOICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MONEY = re.compile(r"quota|insufficient|credit|billing|balance|payment|exceeded your", re.I)
_voices_cache: dict[str, tuple[float, list[list[str]]]] = {}


class VoiceFailed(AppError):
    code = "voice_failed"
    status = 502


class VoiceNotReady(AppError):
    """Nothing is set up yet (no key, no address): the message says what to do."""

    code = "voice_not_ready"
    status = 409


def scrub(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


def needs_key(url: str) -> bool:
    """A service on this computer (a local whisper server) may work without a key."""
    return (urlparse(url).hostname or "") not in ("localhost", "127.0.0.1", "::1")


def base_url(url: str, suffixes: tuple[str, ...]) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise VoiceNotReady("Адрес сервиса не похож на адрес: он должен начинаться с https:// (или http://).")
    for suffix in suffixes:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def explain(status: int, body: str, name: str, *secrets: str) -> str:
    """A plain sentence for an error answer of a service."""
    detail = scrub(" ".join(body.split())[:200], *secrets)
    if status == 402 or (status in (401, 403, 429) and _MONEY.search(body)):
        return f"{name}: на счёте не хватает денег или кончился лимит. Пополните счёт у сервиса."
    if status in (401, 403):
        return f"{name}: ключ не подошёл (неверный, отозван или без нужных прав). Проверьте ключ."
    if status == 429:
        return f"{name}: слишком много запросов, подождите минуту."
    if status == 404:
        return f"{name}: не найдено. Проверьте адрес, название модели и голоса. {detail}".strip()
    if status == 413:
        return f"{name}: запись слишком большая для сервиса."
    if status in (400, 422):
        return f"{name} не принял запрос: {detail or 'без пояснений'}"
    if status >= 500:
        return f"{name} сейчас не работает (ошибка {status}). Попробуйте позже."
    return f"{name} ответил ошибкой {status}. {detail}".strip()


async def _send(method: str, url: str, name: str, secrets: tuple[str, ...], **kwargs: Any) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise VoiceFailed(f"{name} не ответил вовремя. Попробуйте ещё раз.") from None
    except httpx.HTTPError as exc:
        raise VoiceFailed(scrub(f"Нет связи с «{name}»: {type(exc).__name__}. Проверьте интернет и адрес.",
                                *secrets)) from None
    if response.status_code >= 400:
        raise VoiceFailed(explain(response.status_code, response.text[:2000], name, *secrets))
    return response


def audio_file(mime: str) -> tuple[str, str]:
    kind = (mime or "").split(";")[0].strip().lower()
    if kind not in AUDIO_FILES:
        raise VoiceFailed(f"Формат записи {kind or '?'} не поддерживается. Нужен webm, ogg, mp4, wav или mp3.",
                          status=400)
    return AUDIO_FILES[kind]


def _language(code: str) -> str:
    code = (code or "").strip().lower()
    return code if re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", code) else ""


# -- speech to text ------------------------------------------------------------------------------


async def transcribe(service: str, *, url: str, model: str, key: str, audio: bytes, mime: str,
                     language: str = "") -> str:
    filename, content_type = audio_file(mime)
    if service == "elevenlabs":
        if not key:
            raise VoiceNotReady("Не задан ключ ElevenLabs. Впишите его в настройках дополнения «Голос».")
        data = {"model_id": SCRIBE_MODEL}
        if _language(language):
            data["language_code"] = _language(language)
        response = await _send("POST", f"{ELEVEN_URL}/v1/speech-to-text", "ElevenLabs", (key,),
                               headers={"xi-api-key": key}, data=data,
                               files={"file": (filename, audio, content_type)})
    else:
        root = base_url(url, ("/audio/transcriptions",))
        if not key and needs_key(root):
            raise VoiceNotReady("Не задан ключ сервиса распознавания. Впишите его в настройках дополнения «Голос».")
        data = {"model": model or "whisper-1", "response_format": "json"}
        if _language(language):
            data["language"] = _language(language).split("-")[0]
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = await _send("POST", f"{root}/audio/transcriptions", SERVICE_NAMES["openai"], (key,),
                               headers=headers, data=data, files={"file": (filename, audio, content_type)})
    try:
        text = response.json().get("text", "")
    except (ValueError, AttributeError):
        raise VoiceFailed("Сервис распознавания прислал непонятный ответ.") from None
    return text.strip() if isinstance(text, str) else ""


# -- text to speech ------------------------------------------------------------------------------


async def eleven_voices(key: str) -> list[list[str]]:
    """[[voice id, label]] of the account, kept for a minute."""
    if not key:
        raise VoiceNotReady("Не задан ключ ElevenLabs. Впишите его в настройках дополнения «Голос».")
    stamp = hashlib.sha256(key.encode()).hexdigest()
    found = _voices_cache.get(stamp)
    if found and time.monotonic() - found[0] < 60:
        return found[1]
    response = await _send("GET", f"{ELEVEN_URL}/v1/voices", "ElevenLabs", (key,), headers={"xi-api-key": key})
    try:
        rows = response.json().get("voices", [])
        voices = [[str(v["voice_id"]), f"{v.get('name', '?')}" + (f" ({v['category']})" if v.get("category") else "")]
                  for v in rows if isinstance(v, dict) and v.get("voice_id")]
    except (ValueError, AttributeError, TypeError):
        raise VoiceFailed("ElevenLabs прислал непонятный список голосов.") from None
    _voices_cache[stamp] = (time.monotonic(), voices)
    return voices


async def synthesize(service: str, *, url: str, model: str, key: str, voice: str, text: str) -> tuple[bytes, str]:
    """(audio bytes, mime type) of `text` spoken."""
    if service == "elevenlabs":
        if not key:
            raise VoiceNotReady("Не задан ключ ElevenLabs. Впишите его в настройках дополнения «Голос».")
        if not VOICE_ID.match(voice or ""):
            voices = await eleven_voices(key)
            if not voices:
                raise VoiceNotReady("У этого ключа нет голосов. Добавьте голос в ElevenLabs и выберите его в настройках.")
            voice = voices[0][0]
        response = await _send(
            "POST", f"{ELEVEN_URL}/v1/text-to-speech/{voice}", "ElevenLabs", (key,),
            params={"output_format": "mp3_44100_64"}, headers={"xi-api-key": key, "Accept": "audio/mpeg"},
            json={"text": text, "model_id": model or "eleven_multilingual_v2"})
    else:
        root = base_url(url, ("/audio/speech",))
        if not key and needs_key(root):
            raise VoiceNotReady("Не задан ключ сервиса озвучки. Впишите его в настройках дополнения «Голос».")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        response = await _send(
            "POST", f"{root}/audio/speech", SERVICE_NAMES["openai_tts"], (key,), headers=headers,
            json={"model": model or "tts-1", "input": text, "voice": voice or "alloy", "response_format": "mp3"})
    data = response.content
    if not data:
        raise VoiceFailed("Сервис озвучки прислал пустой ответ.")
    if len(data) > MAX_AUDIO_OUT:
        raise VoiceFailed("Сервис озвучки прислал слишком большой файл.")
    kind = response.headers.get("content-type", "audio/mpeg").split(";")[0].strip().lower()
    return data, kind if kind.startswith("audio/") else "audio/mpeg"
