"""SillyTavern goldens: the same inputs through our real import paths give the request ST recorded.

Each case in `tests/fixtures/st_goldens/C*` holds what SillyTavern 1.18.0 sent to an OpenAI-compatible endpoint
(`request.json`) for a card, a world switched on globally, a chat-completion preset and a short chat. Here the
same files go in the way a person would bring them (card JSON, world file + global use, ST preset import with the
model settings accepted into the connection, the chat typed in), the last user message is sent to the fake model,
and the body we POSTed is compared with ST's by `tavern.devtools.compare_requests`.

Owner's rule (2026-11-14): where ours is already better it stays, and the comparison ignores exactly that
(`ALLOWED`, one line of why each); what is not decided waits in `UNDECIDED`; where ST's way is needed the
difference is in `WAVES` with the wave of the parity plan that will fix it (docs/ST_PARITY.md). The first test
ignores all three and guards what already matches; the second ignores only the first two and is a strict xfail
while a wave is pending, so it turns red the day the wave lands and the `WAVES` entry is then removed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tavern.devtools.compare_requests import compare, report

# Marked here, not only in conftest: a parametrized async test needs the mark at collection time.
pytestmark = pytest.mark.anyio

GOLDENS = Path(__file__).resolve().parents[1] / "fixtures" / "st_goldens"
CASES = sorted((p.name for p in GOLDENS.iterdir() if p.is_dir()), key=lambda n: int(n[1:]))
FAKE_REPLY = "ok"

Body = dict[str, Any]


def _load(case: str, name: str) -> Any:
    return json.loads((GOLDENS / case / name).read_text(encoding="utf-8"))


def _without_keys(body: Body, *keys: str, only_value: Any = ...) -> Body:
    return {k: v for k, v in body.items() if k not in keys or (only_value is not ... and v != only_value)}


def _without_message(body: Body, role: str, content: str) -> Body:
    return {**body, "messages": [m for m in body["messages"] if (m.get("role"), m.get("content")) != (role, content)]}


# (b) Deliberate differences: ours is better and stays. Each takes (golden, ours) and returns both with exactly
# that difference taken out, so nothing else is hidden.
ALLOWED: dict[str, tuple[str, Any]] = {
    "include_usage": (
        "we ask a streamed reply for its token counts (stream_options.include_usage) so spending is real, not guessed",
        lambda st, ours: (st, _without_keys(ours, "stream_options", only_value={"include_usage": True}))),
}

# (c) Not decided: listed for the owner with this example; ignored here until he says which way.
UNDECIDED: dict[str, tuple[str, Any]] = {
    "zero_penalties": (
        "ST always sends presence_penalty 0 / frequency_penalty 0; our ST import treats 0 as «off» and sends nothing "
        "(formats/st_preset.py OFF_VALUES: same effect on most services, some Gemini routes reject the fields)",
        lambda st, ours: (_without_keys(st, "presence_penalty", "frequency_penalty", only_value=0), ours)),
}

# (a) SillyTavern's way is needed, not done yet: the wave of the parity plan that will do it (docs/ST_PARITY.md).
# Each takes the golden only and removes what we do not send yet; a case it changes is a strict xfail in the full test.
WAVES: dict[str, tuple[str, Any]] = {
    "new_chat_line": (
        "wave 1: history starts with the system line «[Start a new Chat]» (preset new_chat_prompt), never squashed",
        lambda st: _without_message(st, "system", "[Start a new Chat]")),
}


def _ignore(golden: Body, ours: Body, *, waves: bool) -> tuple[Body, Body]:
    for _, change in (*ALLOWED.values(), *UNDECIDED.values()):
        golden, ours = change(golden, ours)
    if waves:
        for _, change in WAVES.values():
            golden = change(golden)
    return golden, ours


def _waiting_waves(case: str) -> list[str]:
    golden = _load(case, "request.json")
    return [why for why, change in WAVES.values() if change(golden) != golden]


async def _ok(response, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


async def our_request(client, ctx, fake_llm, case: str) -> dict[str, Any]:
    """Bring the case's files in through the app's own routes and return the body we sent for the last message."""
    golden = _load(case, "request.json")
    chat_log = _load(case, "chat.json")
    conn = await _ok(await client.post("/api/connections", json={
        "name": "fake", "base_url": "http://llm/v1", "model": golden["model"]}), 201)
    preset = await _ok(await client.post(
        "/api/presets/import?name=preset.json",
        content=(GOLDENS / case / "preset.json").read_bytes()), 201)
    if preset.get("model_params"):  # the page's question «Применить их к подключению?» answered yes
        current = (await _ok(await client.get("/api/connections")))["connections"]
        params = next(c for c in current if c["id"] == conn["id"])["params"]
        await _ok(await client.put(f"/api/connections/{conn['id']}",
                                   json={"params": {**params, **preset["model_params"]}}))
    character = await _ok(await client.post("/api/characters", json=_load(case, "card.json")), 201)
    world = _load(case, "lorebook.json")
    if world.get("entries"):
        book = await _ok(await client.post("/api/lorebooks/import?name=JB.json",
                                           content=json.dumps(world).encode()), 201)
        await _ok(await client.put("/api/lorebook-uses",
                                   json={"scope": "global", "target": None, "lorebook_ids": [book["id"]]}))
    chat_id = (await _ok(await client.post("/api/chats", json={"character_id": character["id"]}), 201))["chat"]["id"]
    await _ok(await client.put(f"/api/chats/{chat_id}/meta", json={"preset_id": preset["id"]}))
    messages = (await _ok(await client.get(f"/api/chats/{chat_id}/messages")))["messages"]
    assert [m["content"] for m in messages] == [chat_log[0]["text"]]  # the greeting made with the chat
    for item in chat_log[1:-1]:
        await _ok(await client.post(f"/api/chats/{chat_id}/messages",
                                    json={"text": item["text"], "role": item["role"]}), 201)
    fake_llm.queue(FAKE_REPLY)
    started = await _ok(await client.post(f"/api/chats/{chat_id}/send", json={"text": chat_log[-1]["text"]}), 202)
    await asyncio.wait([ctx.runs.get(started["run_id"]).task], timeout=10)
    return fake_llm.requests[-1]


def _differences(golden: Body, ours: Body) -> str:
    found = compare(golden, ours)
    return "" if not found else "A = SillyTavern, B = ours\n" + report(found)


@pytest.mark.parametrize("golden_case", CASES)
async def test_our_request_matches_the_st_golden_but_for_known_differences(client, ctx, fake_llm, golden_case):
    """Guards what already matches: anything new that differs is a regression (or a new finding to classify)."""
    golden, ours = _ignore(_load(golden_case, "request.json"), await our_request(client, ctx, fake_llm, golden_case),
                           waves=True)
    assert not _differences(golden, ours), _differences(golden, ours)


def _full_case(case: str) -> Any:
    waiting = _waiting_waves(case)
    if not waiting:
        return case
    return pytest.param(case, marks=pytest.mark.xfail(strict=True, reason="; ".join(waiting)))


@pytest.mark.parametrize("golden_case", [_full_case(c) for c in CASES])
async def test_our_request_matches_the_st_golden(client, ctx, fake_llm, golden_case):
    """The whole golden, with only the deliberate (and undecided) differences ignored. Strict xfail while a wave
    is pending: when the wave lands this turns red, and its WAVES entry is removed."""
    golden, ours = _ignore(_load(golden_case, "request.json"), await our_request(client, ctx, fake_llm, golden_case),
                           waves=False)
    assert not _differences(golden, ours), _differences(golden, ours)


def test_the_known_differences_are_used_by_some_case():
    """An entry no golden needs any more is stale and goes."""
    goldens = [_load(c, "request.json") for c in CASES]
    for name, (_, change) in WAVES.items():
        assert any(change(g) != g for g in goldens), name
    for name, (_, change) in UNDECIDED.items():
        assert any(change(g, {})[0] != g for g in goldens), name
