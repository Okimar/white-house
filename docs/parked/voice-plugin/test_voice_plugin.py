"""«Голос» (built-in plugin): recognition and speech through fake services, keys that never leave the
server, limits, the cache, the plain errors. The services are fakes (httpx.MockTransport): no network."""

from __future__ import annotations

import base64
import json
import logging
import sys

import httpx
import pytest

KEY = "sk-secret-KEY-1234567890"
ELEVEN = "xi-secret-ELEVEN-0987654321"
AUDIO = base64.b64encode(b"RIFF" + bytes(400)).decode()
MP3 = b"ID3" + bytes(300)


class Fake:
    """Records every request and answers by the path; `status` >= 400 makes every answer an error."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status >= 400:
            return httpx.Response(self.status, text=self.body)
        path = request.url.path
        if path.endswith("/audio/transcriptions") or path.endswith("/speech-to-text"):
            return httpx.Response(200, json={"text": " привет мир "})
        if path.endswith("/audio/speech") or "/text-to-speech/" in path:
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
        if path.endswith("/v1/voices"):
            return httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "Ада", "category": "premade"}]})
        return httpx.Response(404, text="no such path")


@pytest.fixture
async def voice(client, ctx, monkeypatch):
    assert (await client.post("/api/plugins/voice/enable", json={})).status_code == 200
    engines = sys.modules["tavern_plugin_voice.engines"]
    fake = Fake()
    monkeypatch.setattr(engines, "transport", httpx.MockTransport(fake))
    engines._voices_cache.clear()

    async def settings(**values):
        r = await client.put("/api/plugins/voice/settings", json=values)
        assert r.status_code == 200, r.text
        return r.json()

    async def act(name, payload=None):
        return await client.post(f"/api/plugins/voice/actions/{name}", json=payload or {})

    fake.settings, fake.act = settings, act
    return fake


async def test_manifest_loads_and_keys_are_secret_fields(client):
    plugins = (await client.get("/api/plugins")).json()["plugins"]
    plugin = next(p for p in plugins if p["id"] == "voice")
    types = {f["key"]: f["type"] for f in plugin["settings"]}
    assert types["stt_key"] == types["eleven_key"] == types["tts_key"] == "secret"
    assert types["stt_test"] == "action" and types["voice"] == "choice"
    assert next(f for f in plugin["settings"] if f["key"] == "voice")["choices_from"] == "voices"
    assert plugin["builtin"] and not plugin["enabled"]
    assert {"microphone", "secrets"} <= set(plugin["uses"])


async def test_secret_setting_never_comes_back_or_into_the_database(client, ctx, voice, caplog):
    caplog.set_level(logging.DEBUG)
    answer = await voice.settings(stt_key=KEY, eleven_key=ELEVEN, tts_key="tts-secret-9999")
    assert answer["secrets_set"] == {"stt_key": True, "eleven_key": True, "tts_key": True}
    assert answer["values"]["stt_key"] == ""
    for text in (json.dumps(answer), (await client.get("/api/plugins")).text, (await client.get("/api/plugins/voice")).text):
        assert KEY not in text and ELEVEN not in text and "tts-secret-9999" not in text
    assert KEY not in json.dumps(ctx.plugin_repo.get("voice")["settings"])  # not in the settings row
    assert ctx.secrets.get("plugin.voice.stt_key") == KEY
    cleared = await voice.settings(stt_key=None)
    assert cleared["secrets_set"]["stt_key"] is False and not ctx.secrets.has("plugin.voice.stt_key")
    assert KEY not in caplog.text


async def test_transcribe_with_an_openai_style_service(client, voice):
    await voice.settings(stt_service="openai", stt_url="https://stt.example/v1/", stt_model="whisper-large-v3-turbo",
                         stt_key=KEY, stt_language="ru")
    r = await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/webm;codecs=opus", "seconds": 3})
    assert r.status_code == 200 and r.json()["result"] == {"text": "привет мир", "send": False}
    request = voice.requests[-1]
    assert str(request.url) == "https://stt.example/v1/audio/transcriptions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = request.read()
    assert b'name="model"' in body and b"whisper-large-v3-turbo" in body
    assert b'name="language"' in body and b'filename="audio.webm"' in body
    assert KEY not in r.text


async def test_transcribe_with_elevenlabs_scribe(client, voice):
    await voice.settings(stt_service="elevenlabs", eleven_key=ELEVEN, stt_send=True)
    r = await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/mp4", "seconds": 2})
    assert r.json()["result"] == {"text": "привет мир", "send": True}
    request = voice.requests[-1]
    assert request.url.host == "api.elevenlabs.io" and request.url.path == "/v1/speech-to-text"
    assert request.headers["xi-api-key"] == ELEVEN
    assert b"scribe_v1" in request.read()


async def test_recording_limits_and_bad_input(client, voice):
    await voice.settings(stt_service="openai", stt_key=KEY)
    big = "A" * (10 * 1024 * 1024 * 4 // 3 + 100)
    r = await voice.act("transcribe", {"audio": big, "mime": "audio/webm"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "voice_too_big"
    assert (await voice.act("transcribe", {"audio": "", "mime": "audio/webm"})).json()["error"]["code"] == "voice_empty"
    assert (await voice.act("transcribe", {"audio": "!!!not base64", "mime": "audio/webm"})).status_code == 400
    tiny = base64.b64encode(b"x" * 20).decode()
    assert (await voice.act("transcribe", {"audio": tiny, "mime": "audio/webm"})).json()["error"]["code"] == "voice_empty"
    odd = await voice.act("transcribe", {"audio": AUDIO, "mime": "video/x-flv"})
    assert odd.status_code == 400 and "не поддерживается" in odd.json()["error"]["message"]
    assert not voice.requests  # nothing reached the service


async def test_off_and_missing_key_are_explained(client, voice):
    r = await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/webm"})
    assert r.status_code == 409 and "выключено" in r.json()["error"]["message"]
    await voice.settings(stt_service="openai")
    r = await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/webm"})
    assert "ключ" in r.json()["error"]["message"]
    await voice.settings(tts_service="elevenlabs")
    assert "ключ" in (await voice.act("speak", {"text": "Привет"})).json()["error"]["message"]
    assert not voice.requests
    # a service on this computer may work without a key
    await voice.settings(stt_url="http://127.0.0.1:9000/v1")
    assert (await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/webm"})).status_code == 200
    assert "authorization" not in voice.requests[-1].headers


@pytest.mark.parametrize(("status", "body", "expect"), [
    (401, '{"detail":{"status":"invalid_api_key"}}', "ключ не подошёл"),
    (401, '{"detail":{"status":"quota_exceeded"}}', "не хватает денег"),
    (429, '{"error":{"code":"insufficient_quota"}}', "не хватает денег"),
    (402, "payment required", "не хватает денег"),
    (429, "slow down", "слишком много запросов"),
    (404, "model not found", "Проверьте адрес"),
    (500, "boom", "не работает"),
    (400, "bad file " + KEY, "не принял запрос"),
])
async def test_service_errors_become_plain_messages_without_the_key(client, voice, caplog, status, body, expect):
    caplog.set_level(logging.DEBUG)
    await voice.settings(stt_service="openai", stt_key=KEY)
    voice.status, voice.body = status, body
    r = await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/webm"})
    message = r.json()["error"]["message"]
    assert expect in message and KEY not in r.text and KEY not in caplog.text


async def test_no_network_is_explained(client, voice, monkeypatch):
    def broken(request):
        raise httpx.ConnectError("connection refused for " + KEY)

    monkeypatch.setattr(sys.modules["tavern_plugin_voice.engines"], "transport", httpx.MockTransport(broken))
    await voice.settings(stt_service="openai", stt_key=KEY)
    r = await voice.act("transcribe", {"audio": AUDIO, "mime": "audio/webm"})
    assert "Нет связи" in r.json()["error"]["message"] and KEY not in r.text


async def test_connection_tests_report_instead_of_failing(client, voice):
    await voice.settings(stt_service="openai", stt_key=KEY, tts_service="openai", tts_key=KEY)
    ok = (await voice.act("test_stt")).json()["result"]
    assert ok["ok"] and "ключ подходит" in ok["message"]
    assert (await voice.act("test_tts")).json()["result"]["ok"]
    voice.status, voice.body = 401, "no"
    bad = (await voice.act("test_stt")).json()["result"]
    assert bad["ok"] is False and "ключ не подошёл" in bad["message"]
    await voice.settings(stt_service="off")
    assert (await voice.act("test_stt")).json()["result"]["ok"] is False


async def test_speak_elevenlabs_uses_the_chosen_voice_and_caches(client, ctx, voice):
    await voice.settings(tts_service="elevenlabs", eleven_key=ELEVEN, voice="v1", eleven_model="eleven_multilingual_v2",
                         tts_price=0.3)
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    message = ctx.chats.add_message(chat["id"], role="assistant", content="Привет! *улыбается* Как дела?")
    r = await voice.act("speak", {"message_id": message["id"], "part": 0})
    result = r.json()["result"]
    assert base64.b64decode(result["audio"]) == MP3 and result["mime"] == "audio/mpeg"
    assert result["parts"] == 1 and result["cached"] is False
    request = voice.requests[-1]
    assert request.url.path == "/v1/text-to-speech/v1" and request.headers["xi-api-key"] == ELEVEN
    sent = json.loads(request.read())
    assert sent["text"] == "Привет! Как дела?" and sent["model_id"] == "eleven_multilingual_v2"  # the action is not read
    again = (await voice.act("speak", {"message_id": message["id"], "part": 0})).json()["result"]
    assert again["cached"] is True and len(voice.requests) == 1  # a replay costs nothing
    await voice.settings(voice="v2")
    fresh = (await voice.act("speak", {"message_id": message["id"], "part": 0})).json()["result"]
    assert fresh["cached"] is False and len(voice.requests) == 2  # another voice is another piece
    # spending: requests are counted, tokens are not, and the sum comes from the price the user typed
    rows = ctx.spending.purpose("plugin:voice")
    assert rows["requests"] == 2 and rows["prompt_tokens"] == 0 and rows["cost"] == pytest.approx(0.3 * 17 / 1000 * 2)


async def test_cache_can_be_off_and_is_cleared(client, ctx, voice):
    await voice.settings(tts_service="openai", tts_key=KEY, cache_mb=0)
    for _ in range(2):
        await voice.act("speak", {"text": "Привет, мир."})
    assert len(voice.requests) == 2
    await voice.settings(cache_mb=5)
    await voice.act("speak", {"text": "Привет, мир."})
    assert (await voice.act("speak", {"text": "Привет, мир."})).json()["result"]["cached"] is True
    assert (await voice.act("clear_cache")).json()["result"]["ok"]
    assert (await voice.act("speak", {"text": "Привет, мир."})).json()["result"]["cached"] is False


async def test_openai_speech_request_and_character_voice(client, ctx, voice):
    await voice.settings(tts_service="openai", tts_url="https://tts.example/v1", tts_model="tts-1", tts_key=KEY,
                         voice="alloy", voices=[{"character": "char-1", "voice": "nova"}])
    await voice.act("speak", {"text": "Один.", "character_id": "char-1"})
    request = voice.requests[-1]
    assert str(request.url) == "https://tts.example/v1/audio/speech"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.read()) == {"model": "tts-1", "input": "Один.", "voice": "nova", "response_format": "mp3"}
    await voice.act("speak", {"text": "Два.", "character_id": "someone-else"})
    assert json.loads(voice.requests[-1].read())["voice"] == "alloy"


async def test_long_text_comes_in_pieces_and_is_capped(client, voice):
    await voice.settings(tts_service="openai", tts_key=KEY, max_chars=100)
    text = " ".join(f"Это предложение номер {i}." for i in range(40))
    first = (await voice.act("speak", {"text": text, "part": 0})).json()["result"]
    assert first["truncated"] is True and first["parts"] == 1
    assert (await voice.act("speak", {"text": text, "part": 1})).json()["error"]["code"] == "voice_bad_part"
    await voice.settings(max_chars=5000)
    many = (await voice.act("speak", {"text": text, "part": 0})).json()["result"]
    assert many["parts"] == 2 and many["truncated"] is False


async def test_nothing_worth_saying_and_browser_voice(client, ctx, voice):
    await voice.settings(tts_service="openai", tts_key=KEY)
    empty = (await voice.act("speak", {"text": "<thinking>думаю</thinking> *кивает* ```print(1)```"})).json()["result"]
    assert empty == {"empty": True, "parts": 0} and not voice.requests
    await voice.settings(tts_service="browser", tts_lang="ru-RU")
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    message = ctx.chats.add_message(chat["id"], role="assistant", content="Первое. Второе.")
    browser = (await voice.act("speak", {"message_id": message["id"]})).json()["result"]
    assert browser["browser"] is True and browser["parts"] == ["Первое. Второе."] and browser["lang"] == "ru-RU"
    assert not voice.requests  # the browser's voice never touches the network
    await voice.act("browser_voices", {"voices": [{"name": "Milena", "lang": "ru-RU"}, {"nope": 1}]})
    assert (await voice.act("voices")).json()["result"] == [["Milena", "Milena (ru-RU)"]]


async def test_voice_lists(client, ctx, voice):
    await voice.settings(tts_service="elevenlabs", eleven_key=ELEVEN)
    assert (await voice.act("voices")).json()["result"] == [["v1", "Ада (premade)"]]
    assert voice.requests[-1].headers["xi-api-key"] == ELEVEN
    await voice.settings(tts_service="openai")
    assert ["nova", "nova"] in (await voice.act("voices")).json()["result"]
    await voice.settings(tts_service="elevenlabs", eleven_key=None)
    assert "ключ" in (await voice.act("voices")).json()["error"]["message"]
    made = ctx.characters.create({"name": "Ада"}) if hasattr(ctx.characters, "create") else None
    listed = (await voice.act("characters")).json()["result"]
    assert made is None or any(row[1] == "Ада" for row in listed)


async def test_status_and_quick_switch(client, voice):
    status = (await voice.act("status")).json()["result"]
    assert status["stt"]["service"] == "off" and status["limits"]["max_seconds"] == 120
    await voice.settings(tts_service="elevenlabs")
    assert (await voice.act("status")).json()["result"]["tts"]["ready"] is False  # no key yet
    await voice.settings(eleven_key=ELEVEN)
    assert (await voice.act("status")).json()["result"]["tts"]["ready"] is True
    on = (await voice.act("set_read", {"place": "assistant", "on": True})).json()["result"]
    assert on["tts"]["read_assistant"] is True and on["tts"]["read_chat"] is False
    assert (await voice.act("set_read", {"place": "x", "on": True})).status_code == 400


async def test_voice_failure_does_not_touch_the_chat(client, ctx, voice):
    await voice.settings(tts_service="openai", tts_key=KEY, stt_service="openai", stt_key=KEY, read_chat=True)
    voice.status, voice.body = 500, "down"
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    message = ctx.chats.add_message(chat["id"], role="assistant", content="Привет")
    assert (await voice.act("speak", {"message_id": message["id"]})).status_code == 502
    assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 200  # the app goes on as usual
    assert (await client.get("/api/plugins")).status_code == 200
