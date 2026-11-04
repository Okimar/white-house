"""Regression tests: V3 cards, the warning line, the token and the log, copies, theme approval,
the narrator's thinking, fallbacks and thinking, the live test stand."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from types import SimpleNamespace

from tavern.cli import _announce
from tavern.devtools import assistant_bench as bench
from tavern.formats.cards import PNG_SIGNATURE, _png_chunk, card_to_png, decode_png_card
from tavern.providers.base import ChatMessage, GenerationRequest, Provider, StreamEvent, Usage
from tavern.providers.fallback import FallbackProvider, check_steps
from tavern.services import skills
from tavern.services.chat import via_line

J = lambda **kw: json.dumps(kw, ensure_ascii=False)  # noqa: E731


def png_with(*chunks: tuple[bytes, dict]) -> bytes:
    body = b"".join(_png_chunk(b"tEXt", key + b"\x00" + base64.b64encode(json.dumps(card).encode()))
                    for key, card in chunks)
    return PNG_SIGNATURE + body + _png_chunk(b"IEND", b"")


V3 = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "Вера", "description": "из ccv3"}}
V2 = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "Старая", "description": "из chara"}}


def test_a_v3_card_with_only_its_own_chunk_opens():
    assert decode_png_card(png_with((b"ccv3", V3)))[1]["description"] == "из ccv3"


def test_the_newer_chunk_wins_and_an_old_card_still_opens():
    assert decode_png_card(png_with((b"chara", V2), (b"ccv3", V3)))[1]["description"] == "из ccv3"
    assert decode_png_card(card_to_png({"name": "Ann", "description": "d"}))[1]["name"] == "Ann"


def test_a_broken_new_chunk_falls_back_to_the_old_one():
    broken = PNG_SIGNATURE + _png_chunk(b"tEXt", b"ccv3\x00not base64!") + \
        _png_chunk(b"tEXt", b"chara\x00" + base64.b64encode(json.dumps(V2).encode())) + _png_chunk(b"IEND", b"")
    assert decode_png_card(broken)[1]["description"] == "из chara"


async def test_the_warning_line_goes_away_once_the_problem_is_fixed(client, ctx):
    ctx.backups.warnings = [{"id": "plugin:x", "level": "warn", "title": "было", "detail": "", "fix": False}]
    assert (await client.get("/api/backups/status")).json()["warnings"]
    assert (await client.post("/api/doctor/fix", json={"ids": []})).status_code == 200
    assert (await client.get("/api/backups/status")).json()["warnings"] == []


async def test_a_real_problem_stays_in_the_warning_line(client, ctx, monkeypatch):
    still = [{"id": "space", "level": "error", "title": "Почти нет места", "detail": "", "fix": False}]
    ctx.backups.warnings = still
    monkeypatch.setattr("tavern.services.doctor.quick", lambda _ctx: still)
    await client.post("/api/doctor/fix", json={"ids": []})
    assert (await client.get("/api/backups/status")).json()["warnings"] == still


def test_the_access_token_is_shown_but_not_written_to_the_log(caplog, capsys):
    ctx = SimpleNamespace(sandbox=False, config=SimpleNamespace(remote_enabled=True),
                          secrets=SimpleNamespace(access_token=lambda: "SECRET-TOKEN"))
    with caplog.at_level(logging.INFO):
        _announce(SimpleNamespace(started=True, should_exit=False), "http://x/", ctx, 0.0, False)
    assert "SECRET-TOKEN" not in caplog.text and "Remote access is ON" in caplog.text
    assert "login?token=SECRET-TOKEN" in capsys.readouterr().err


async def test_a_copy_is_made_again_after_the_newest_one_was_deleted(ctx):
    first = ctx.backups.create("manual", force=True)
    assert ctx.backups.create("daily") is None  # nothing changed
    ctx.backups.delete(first["name"])
    assert ctx.backups.create("daily") is not None


async def test_restoring_keeps_past_versions_of_plugins_and_themes(client, ctx):
    name = (await client.post("/api/backups", json={})).json()["name"]
    for top in ("plugins", "themes"):
        kept = ctx.paths.root / top / ".versions" / "one" / "file.txt"
        kept.parent.mkdir(parents=True)
        kept.write_text("old", encoding="utf-8")
    assert (await client.post(f"/api/backups/{name}/restore")).status_code == 200
    for top in ("plugins", "themes"):
        assert (ctx.paths.root / top / ".versions" / "one" / "file.txt").read_text(encoding="utf-8") == "old"


async def test_a_large_copy_uploads_through_the_stream(client, ctx):
    name = (await client.post("/api/backups", json={})).json()["name"]
    blob = ctx.backups.find(name).read_bytes()
    ctx.backups.delete(name)
    made = await client.post("/api/backups/upload", content=blob)
    assert made.status_code == 201 and made.json()["name"]
    assert (await client.post("/api/backups/upload", content=b"")).status_code == 400
    bad = await client.post("/api/backups/upload", content=b"not a copy")
    assert bad.status_code >= 400
    assert not [p for p in (ctx.paths.root / ".backup-tmp").glob("*")]  # no leftovers


async def test_an_approved_script_needs_approval_again_when_a_file_it_loads_changes(client, ctx):
    theme = ctx.paths.root / "themes" / "t"
    theme.mkdir(parents=True)
    (theme / "theme.json").write_text(json.dumps({"id": "t", "name": "T", "script": "main.js"}), encoding="utf-8")
    (theme / "main.js").write_text("import './helper.js';", encoding="utf-8")

    async def sha():
        return next(t for t in (await client.get("/api/themes")).json()["themes"] if t["id"] == "t")["script_sha256"]

    alone = await sha()
    import hashlib
    assert alone == hashlib.sha256(b"import './helper.js';").hexdigest()  # approvals of one-file themes stay valid
    (theme / "helper.js").write_text("one", encoding="utf-8")
    with_helper = await sha()
    (theme / "helper.js").write_text("two", encoding="utf-8")
    assert with_helper != alone and await sha() != with_helper


async def test_the_narrators_thinking_is_kept_with_the_scene(client, ctx, fake_llm):
    await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1", "model": "m"})
    ann = (await client.post("/api/characters", json={"name": "Ann"})).json()
    bob = (await client.post("/api/characters", json={"name": "Bob"})).json()
    chat_id = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]["id"]
    await client.post(f"/api/chats/{chat_id}/members", json={"character_id": bob["id"]})
    await client.put(f"/api/chats/{chat_id}/speakers", json={"order": "all", "narrator": {"enabled": True, "name": "Голос"}})
    fake_llm.queue("I heal.", "I forge.", "<thinking>свожу вместе</thinking>Сцена.")
    run_id = (await client.post(f"/api/chats/{chat_id}/send", json={"text": "go"})).json()["run_id"]
    await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
    scene = (await client.get(f"/api/chats/{chat_id}/messages")).json()["messages"][-1]
    assert scene["content"] == "Сцена." and scene["swipe_meta"]["reasoning"] == "свожу вместе"


def test_the_via_line_keeps_the_fallback_when_requests_run_side_by_side():
    assert via_line("Main · m (основной)", "Запас · x", "Main · m") == "Main · m (основной) ↪ Запас · x"
    assert via_line("Main · m", "Запас · x", "Main · m") == "Main · m ↪ Запас · x"
    assert via_line("", "Запас · x", "Main · m") == "Запас · x"
    assert via_line("Main · m (основной)", "", "Main · m") == "Main · m (основной)"
    assert via_line("Other · y", "Запас · x", "Main · m") == "Other · y"  # another request won the race
    assert via_line("", "", "Main · m") == ""


class Scripted(Provider):
    kind = "scripted"

    def __init__(self, *events: StreamEvent) -> None:
        self.events = events

    async def stream(self, request):
        for event in self.events:
            yield event

    async def list_models(self):
        return []


async def test_thinking_goes_live_while_a_refusal_is_watched_and_does_not_block_the_fallback():
    main = Scripted(StreamEvent("reasoning", "думаю"), StreamEvent("text", "I can't help with that."),
                    StreamEvent("done", usage=Usage(1, 1)))
    spare = Scripted(StreamEvent("text", "Ответ"), StreamEvent("done", usage=Usage(1, 1)))
    chain = FallbackProvider(main, check_steps([{"on": ["refused"], "model": "x"}]), lambda step: (spare, "x", "запас"))
    request = GenerationRequest(messages=[ChatMessage("user", "hi")], model="m")
    events = [e async for e in chain.stream(request)]
    assert events[0].kind == "reasoning" and events[0].text == "думаю"
    assert "".join(e.text for e in events if e.kind == "text") == "Ответ" and chain.used == "запас"


async def test_the_stand_answers_both_rounds_of_questions(client, ctx, fake_llm, tmp_path, monkeypatch):
    conn = (await client.post("/api/connections", json={"name": "Main", "base_url": "http://llm/v1", "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    (tmp_path / "people.md").write_text("---\nname: people\ndescription: Making a character\n---\nГолос.\n## Plan\nплан"
                                        "\n## More questions\nещё\n## Write\nпиши\n", encoding="utf-8")
    monkeypatch.setattr(skills, "BUILTIN_DIR", tmp_path)
    question = J(say="Вопрос:", questions=[{"text": "Тон?", "options": ["А", "Б"], "default": 0}])
    fake_llm.queue(J(say="", actions=[], big=True), J(skill="people"), question, question,
                   J(say="План:", goal="Цель", decisions=[], items=[{"title": "Пункт", "detail": "д"}]),
                   J(say="Сделано", actions=[]), J(say="Итог."))
    result = await bench.play(ctx, {"name": "t", "turns": [{"text": "Сделай персонажа", "answers": {}}]})
    assert [t["step"] for t in result["timings"]] == ["«Сделай персонажа»", "ответы", "ответы", "давай"]
