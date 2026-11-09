"""The assistant's screenshots (ADR 0020): a notice to the open page, the page's upload, the picture to the model."""

from __future__ import annotations

import asyncio
import json

import pytest

from tavern.devtools.fake_llm import _pattern_png
from tavern.services import assistant_screenshots as shots
from tests.integration.test_assistant_web import J, act, ask, conversation, setup

PNG = _pattern_png("screen")


@pytest.fixture(autouse=True)
def _no_mini_plan(monkeypatch):
    from tavern.services import assistant_engine as engine

    monkeypatch.setattr(engine, "MINI_PLAN", False)


def open_page(ctx, client, *, upload: bytes | None = PNG, seen: list | None = None):
    """A fake open page: listens to the notices and answers capture requests like web/core/capture.js."""
    ready = asyncio.Event()

    async def run() -> None:
        stream = ctx.notices.subscribe()
        ready.set()
        async for event in stream:
            if event["type"] != "assistant_capture":
                continue
            if seen is not None:
                seen.append(event)
            if upload is not None:
                await client.post(f"/api/assistant/screenshots/{event['id']}", content=upload,
                                  headers={"content-type": "image/png"})

    task = asyncio.ensure_future(run())
    return task, ready


async def listening(ctx, task, ready) -> None:
    await ready.wait()
    for _ in range(200):
        if ctx.notices.listeners:
            return
        await asyncio.sleep(0.01)
    task.cancel()
    raise AssertionError("page did not subscribe")


async def test_tool_sends_a_notice_and_the_upload_resolves_it(client, ctx):
    seen: list = []
    task, ready = open_page(ctx, client, seen=seen)
    await listening(ctx, task, ready)
    media, data = await shots.request(ctx, "phone", timeout=5)
    task.cancel()
    assert media == "image/png" and data
    assert seen[0]["what"] == "phone" and seen[0]["long_side"] == 1280 and len(seen[0]["id"]) >= 16


async def test_no_open_page_is_a_clear_error(ctx):
    with pytest.raises(shots.NoPage, match="Откройте приложение"):
        await shots.request(ctx, "screen")


async def test_timeout_is_a_clear_error(client, ctx):
    task, ready = open_page(ctx, client, upload=None)
    await listening(ctx, task, ready)
    with pytest.raises(shots.NoPage, match="не ответила"):
        await shots.request(ctx, "screen", timeout=0.2)
    task.cancel()


async def test_bad_what_is_refused(ctx):
    with pytest.raises(shots.NoPage, match="what"):
        await shots.request(ctx, "desktop")


async def test_unknown_and_reused_ids_are_refused(client, ctx):
    unknown = await client.post("/api/assistant/screenshots/nope", content=PNG)
    assert unknown.status_code == 404
    task, ready = open_page(ctx, client, upload=None)
    await listening(ctx, task, ready)
    pending = asyncio.ensure_future(shots.request(ctx, "screen", timeout=5))
    for _ in range(200):
        if shots._pending(ctx):
            break
        await asyncio.sleep(0.01)
    ident = next(iter(shots._pending(ctx)))
    assert (await client.post(f"/api/assistant/screenshots/{ident}", content=PNG)).status_code == 201
    await pending
    again = await client.post(f"/api/assistant/screenshots/{ident}", content=PNG)  # used: gone
    assert again.status_code == 404
    task.cancel()


async def test_size_and_type_limits(client, ctx):
    task, ready = open_page(ctx, client, upload=None)
    await listening(ctx, task, ready)
    pending = asyncio.ensure_future(shots.request(ctx, "screen", timeout=2))
    for _ in range(200):
        if shots._pending(ctx):
            break
        await asyncio.sleep(0.01)
    ident = next(iter(shots._pending(ctx)))
    big = PNG + b"\0" * shots.MAX_BYTES
    assert (await client.post(f"/api/assistant/screenshots/{ident}", content=big)).status_code == 413
    assert (await client.post(f"/api/assistant/screenshots/{ident}", content=b"<svg></svg>")).status_code == 400
    refused = await client.post(f"/api/assistant/screenshots/{ident}?failed=boom")
    assert refused.status_code == 200
    with pytest.raises(shots.NoPage, match="boom"):
        await pending
    task.cancel()


async def test_the_assistant_cannot_upload_to_itself(client, ctx, fake_llm):
    await setup(client)
    conv = await conversation(client)
    fake_llm.queue(J(say="Загружаю", actions=[act("app_call", method="POST", path="/assistant/screenshots/x")]),
                   J(say="Не вышло", actions=[]))
    state = await ask(client, ctx, conv, "загрузи снимок")
    assert "closed" in json.dumps(state["messages"], ensure_ascii=False).lower() or "закрыт" in json.dumps(
        state["messages"], ensure_ascii=False)


async def test_the_picture_reaches_the_model_and_stays_out_of_the_conversation(client, ctx, fake_llm):
    await setup(client)
    task, ready = open_page(ctx, client)
    await listening(ctx, task, ready)
    conv = await conversation(client)
    fake_llm.queue(J(say="Смотрю", actions=[act("screenshot", what="phone")]),
                   J(say="Тема читается.", actions=[]))
    state = await ask(client, ctx, conv, "Проверь, как выглядит тема на телефоне")
    task.cancel()
    assert "image_url" in json.dumps(fake_llm.requests[1]["messages"])  # the next call carried the picture
    assert "image_url" not in json.dumps(fake_llm.requests[0]["messages"])
    stored = json.dumps(state["messages"], ensure_ascii=False)
    assert "iVBOR" not in stored  # no picture data in the conversation's rows
    assert "comes to you as an image" in json.dumps(fake_llm.requests[1]["messages"])  # the tool result says so


async def test_no_page_open_the_model_gets_the_error_to_tell_the_user(client, ctx, fake_llm):
    await setup(client)
    conv = await conversation(client)
    fake_llm.queue(J(say="Смотрю", actions=[act("screenshot")]), J(say="Нужна открытая страница.", actions=[]))
    await ask(client, ctx, conv, "сделай снимок")
    assert "Откройте приложение в браузере" in json.dumps(fake_llm.requests[1]["messages"], ensure_ascii=False)


async def test_only_the_capture_address_can_be_framed(client):
    page = await client.get("/")
    assert page.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in page.headers["content-security-policy"]
    framed = await client.get("/?capture=1")
    assert framed.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in framed.headers["content-security-policy"]
    assert framed.headers["content-security-policy"].count("default-src") == 1


def test_the_vendored_library_is_there_with_its_licence_and_parses():
    import shutil
    import subprocess
    from pathlib import Path

    vendor = Path(__file__).resolve().parents[2] / "web" / "vendor"
    assert (vendor / "LICENSE.modern-screenshot").read_text(encoding="utf-8").startswith("The MIT License")
    assert "modern-screenshot" in (vendor / "README.md").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node:
        done = subprocess.run([node, "--check", str(vendor / "modern-screenshot.mjs")], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
