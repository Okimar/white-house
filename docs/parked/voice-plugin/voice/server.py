"""🎙 Голос: speech to text and text to speech through outside services (no own recognition).

Everything the page needs is an action: `status`, `transcribe` (a recording in, text out),
`speak` (a message or a text in, spoken pieces out), `voices`, `characters`, tests, `set_read`.
Audio travels as base64 inside the JSON of an action, so the page needs no new permission: it plays
a `blob:` URL (already allowed by the page CSP, see ADR 0019). The keys are "secret" settings:
read here with api.secret(), never sent to the page, never put in a message or a log.
A failure of voice is an error of that one action: sending and receiving messages never go through here.
"""

from __future__ import annotations

import base64
import binascii
import time
from typing import Any

from tavern.errors import AppError

from . import engines
from .cache import AudioCache, make_key
from .speech_text import chunks, clean, shorten

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # a recording; two minutes of speech is under 2 MB
MAX_SECONDS = 120
PIECE_CHARS = {"elevenlabs": 900, "openai": 1500}
BROWSER_PIECE = 180  # browsers cut long utterances short
TEST_TEXT = "Проверка голоса."


def silent_wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    """A one-second silent recording: enough to see that a recognition service accepts our request."""
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    header = (b"RIFF" + (36 + len(data)).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
              + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + rate.to_bytes(4, "little")
              + (rate * 2).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
              + b"data" + len(data).to_bytes(4, "little"))
    return header + data


def setup(api: Any) -> None:
    cache = AudioCache(api.ctx.paths.cache / "voice")

    def cfg() -> dict[str, Any]:
        return api.settings

    def stt_ready(s: dict[str, Any]) -> bool:
        if s["stt_service"] == "elevenlabs":
            return bool(api.secret("eleven_key"))
        if s["stt_service"] == "openai":
            return bool(api.secret("stt_key")) or not engines.needs_key(s["stt_url"] or "http://x")
        return False

    def tts_ready(s: dict[str, Any]) -> bool:
        service = s["tts_service"]
        if service == "browser":
            return True
        if service == "elevenlabs":
            return bool(api.secret("eleven_key"))
        if service == "openai":
            return bool(api.secret("tts_key")) or not engines.needs_key(s["tts_url"] or "http://x")
        return False

    def spend(kind: str, service: str, model: str, *, chars: int = 0, seconds: float = 0.0,
              chat_id: str = "", message_id: str = "") -> None:
        """One row on the «Расход» tab. There are no tokens here: only the requests are counted, and the
        sum when the user has typed a price. Never raises: a broken record must not cost a reply."""
        s = cfg()
        price = (s["tts_price"] * chars / 1000) if kind == "tts" else (s["stt_price"] * seconds / 60)
        try:
            api.ctx.spending.record(
                chat_id=chat_id or None, message_id=message_id or None, connection_id="",
                connection_name=f"Голос: {'ElevenLabs' if service == 'elevenlabs' else 'сервис'}",
                model=f"{kind}:{model or service}", purpose=f"plugin:{api.id}", prompt_tokens=0,
                completion_tokens=0, cost=price if price > 0 else None, estimated=True)
        except Exception:  # noqa: BLE001 - see the docstring
            api.log.exception("Could not record the spending of a voice request")

    def decode_audio(payload: dict[str, Any]) -> bytes:
        raw = payload.get("audio")
        if not isinstance(raw, str) or not raw:
            raise AppError("Запись пустая: ничего не записалось.", code="voice_empty")
        if len(raw) > MAX_AUDIO_BYTES * 4 // 3 + 8:
            raise AppError(f"Запись слишком большая (больше {MAX_AUDIO_BYTES // (1024 * 1024)} МБ). "
                           f"Говорите короче: до {MAX_SECONDS} секунд.", code="voice_too_big", status=413)
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise AppError("Запись повреждена при передаче.", code="voice_bad_audio") from None
        if len(data) < 200:
            raise AppError("Запись пустая: ничего не записалось.", code="voice_empty")
        return data

    # -- speech to text ----------------------------------------------------------------------------

    async def run_transcribe(audio: bytes, mime: str, seconds: float, *, language_override: str | None = None) -> str:
        s = cfg()
        service = s["stt_service"]
        if service == "off":
            raise engines.VoiceNotReady("Распознавание выключено. Выберите сервис в настройках дополнения «Голос».")
        key = api.secret("eleven_key" if service == "elevenlabs" else "stt_key")
        text = await engines.transcribe(
            service, url=s["stt_url"], model=s["stt_model"], key=key, audio=audio, mime=mime,
            language=s["stt_language"] if language_override is None else language_override)
        spend("stt", service, engines.SCRIBE_MODEL if service == "elevenlabs" else s["stt_model"], seconds=seconds)
        return text

    async def act_transcribe(payload: dict[str, Any]) -> dict[str, Any]:
        """{audio: base64, mime, seconds} -> {text, send}. Empty text = nothing was understood."""
        audio = decode_audio(payload)
        seconds = payload.get("seconds")
        seconds = float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 0.0
        text = await run_transcribe(audio, str(payload.get("mime", "")), min(max(seconds, 0.0), MAX_SECONDS * 2))
        return {"text": text, "send": bool(cfg()["stt_send"])}

    # -- text to speech ----------------------------------------------------------------------------

    def voice_for(character_id: str) -> str:
        s = cfg()
        if character_id:
            for row in s["voices"]:
                if row.get("character") == character_id and row.get("voice"):
                    return str(row["voice"])
        return str(s["voice"] or "")

    def tts_params(s: dict[str, Any]) -> tuple[str, str, str, str]:
        """(service, url, model, key) of the chosen speech service."""
        service = s["tts_service"]
        if service == "elevenlabs":
            return service, "", s["eleven_model"], api.secret("eleven_key")
        return service, s["tts_url"], s["tts_model"], api.secret("tts_key")

    def spoken_text(payload: dict[str, Any]) -> tuple[str, str, str, str]:
        """(text to speak, character id, chat id, message id) from {message_id} or {text}."""
        s = cfg()
        message_id = str(payload.get("message_id", ""))
        chat_id = str(payload.get("chat_id", ""))
        character_id = str(payload.get("character_id", ""))
        if message_id:
            message = api.ctx.chats.get_message(message_id)
            raw = message["content"]
            chat_id = message["chat_id"]
            character_id = message.get("character_id") or character_id
        else:
            raw = str(payload.get("text", ""))
        text = clean(raw, thinking=s["skip_thinking"], code=s["skip_code"], actions=s["skip_actions"])
        return text, character_id, chat_id, message_id

    async def act_speak(payload: dict[str, Any]) -> dict[str, Any]:
        """{message_id | text, part, character_id?} -> one spoken piece (or, for the browser's own voice,
        all the pieces to speak there). {"empty": true} when there is nothing worth saying."""
        s = cfg()
        service = s["tts_service"]
        if service == "off":
            raise engines.VoiceNotReady("Озвучка выключена. Выберите сервис в настройках дополнения «Голос».")
        text, character_id, chat_id, message_id = spoken_text(payload)
        text, cut = shorten(text, int(s["max_chars"]))
        if not text:
            return {"empty": True, "parts": 0}
        voice = voice_for(character_id)
        if service == "browser":
            return {"browser": True, "parts": chunks(text, BROWSER_PIECE), "voice": voice, "lang": s["tts_lang"],
                    "truncated": cut}
        pieces = chunks(text, PIECE_CHARS[service])
        part = payload.get("part", 0)
        if isinstance(part, bool) or not isinstance(part, int) or not 0 <= part < len(pieces):
            raise AppError("Нет такого куска озвучки.", code="voice_bad_part")
        piece = pieces[part]
        service, url, model, key = tts_params(s)
        slot = make_key(service, url, model, voice, piece)
        limit = int(s["cache_mb"]) * 1024 * 1024
        hit = cache.get(slot) if limit > 0 else None
        if hit:
            data, mime = hit
        else:
            data, mime = await engines.synthesize(service, url=url, model=model, key=key, voice=voice, text=piece)
            cache.put(slot, data, mime, limit)
            spend("tts", service, model, chars=len(piece), chat_id=chat_id, message_id=message_id)
        return {"audio": base64.b64encode(data).decode("ascii"), "mime": mime, "part": part,
                "parts": len(pieces), "cached": bool(hit), "truncated": cut}

    async def act_voices(_: dict[str, Any]) -> list[list[str]]:
        """[[value, label]] of the voices of the chosen service."""
        service = cfg()["tts_service"]
        if service == "elevenlabs":
            return await engines.eleven_voices(api.secret("eleven_key"))
        if service == "openai":
            return [[v, v] for v in engines.OPENAI_VOICES]
        if service == "browser":
            return [[str(v["name"]), f"{v['name']} ({v.get('lang', '')})"]
                    for v in api.storage().get("browser_voices", []) if isinstance(v, dict) and v.get("name")]
        return []

    def act_browser_voices(payload: dict[str, Any]) -> None:
        """The page tells which voices this browser has (only the browser knows)."""
        rows = payload.get("voices")
        if not isinstance(rows, list):
            return
        kept = [{"name": str(v.get("name", ""))[:120], "lang": str(v.get("lang", ""))[:20]}
                for v in rows[:300] if isinstance(v, dict) and v.get("name")]
        if kept != api.storage().get("browser_voices", []):
            api.storage().set("browser_voices", kept)

    def act_characters(_: dict[str, Any]) -> list[list[str]]:
        return [[c["id"], c["name"]] for c in api.ctx.characters.list()]

    def act_status(_: dict[str, Any]) -> dict[str, Any]:
        s = cfg()
        return {
            "stt": {"service": s["stt_service"], "ready": stt_ready(s), "send": bool(s["stt_send"])},
            "tts": {"service": s["tts_service"], "ready": tts_ready(s), "lang": s["tts_lang"],
                    "read_chat": bool(s["read_chat"]), "read_assistant": bool(s["read_assistant"])},
            "limits": {"max_seconds": MAX_SECONDS, "max_bytes": MAX_AUDIO_BYTES},
        }

    def act_set_read(payload: dict[str, Any]) -> dict[str, Any]:
        """{place: "chat" | "assistant", on: bool}: the quick switch beside the message box."""
        place = payload.get("place")
        if place not in ("chat", "assistant") or not isinstance(payload.get("on"), bool):
            raise AppError("Нужны place (chat или assistant) и on (да или нет).")
        api.ctx.plugins.save_settings(api.id, {f"read_{place}": payload["on"]})
        return act_status({})

    # -- tests and housekeeping --------------------------------------------------------------------

    async def guarded(fn: Any, done: str) -> dict[str, Any]:
        try:
            return {"ok": True, "message": await fn(done)}
        except AppError as exc:
            return {"ok": False, "message": exc.message}

    async def act_test_stt(_: dict[str, Any]) -> dict[str, Any]:
        async def go(done: str) -> str:
            started = time.monotonic()
            await run_transcribe(silent_wav(), "audio/wav", 1.0, language_override="")
            return f"{done} (ответ за {time.monotonic() - started:.1f} с)"
        return await guarded(go, "Сервис принял запись, ключ подходит")

    async def act_test_tts(_: dict[str, Any]) -> dict[str, Any]:
        async def go(done: str) -> str:
            s = cfg()
            service = s["tts_service"]
            if service == "off":
                raise engines.VoiceNotReady("Озвучка выключена: выберите сервис.")
            if service == "browser":
                return "Голос браузера проверяется на странице: нажмите 🔊 у любого сообщения."
            _, url, model, key = tts_params(s)
            data, _mime = await engines.synthesize(service, url=url, model=model, key=key, voice=voice_for(""),
                                                   text=TEST_TEXT)
            spend("tts", service, model, chars=len(TEST_TEXT))
            return f"{done} (получено {len(data) // 1024 + 1} КБ звука)"
        return await guarded(go, "Сервис озвучил тестовую фразу, ключ подходит")

    def act_clear_cache(_: dict[str, Any]) -> dict[str, Any]:
        freed = cache.clear()
        return {"ok": True, "message": f"Освобождено: {freed / (1024 * 1024):.1f} МБ"}

    api.action("status", act_status)
    api.action("transcribe", act_transcribe)
    api.action("speak", act_speak)
    api.action("voices", act_voices)
    api.action("characters", act_characters)
    api.action("browser_voices", act_browser_voices)
    api.action("set_read", act_set_read)
    api.action("test_stt", act_test_stt)
    api.action("test_tts", act_test_tts)
    api.action("clear_cache", act_clear_cache)
