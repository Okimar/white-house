"""Model settings live only in the connection (stage after 10е): the one-time move of an older
preset's settings, old preset files, and that the preset no longer wins over the connection."""

from __future__ import annotations

from tavern.core.presets import default_preset, normalize_preset
from tavern.services import presets as preset_service


def _old_preset(ctx, name, params, max_context=0):
    data = {**default_preset(), "params": params, "max_context_tokens": max_context}
    return ctx.presets.create(name, normalize_preset(data))


async def _connection(client, **params):
    body = {"name": "fake", "base_url": "http://llm/v1", "model": "m", "params": params}
    return (await client.post("/api/connections", json=body)).json()


async def test_move_copies_the_active_presets_settings_into_the_active_connection_once(client, ctx):
    conn = await _connection(client, temperature=0.5, top_p=0.8)
    chosen = _old_preset(ctx, "Mine", {"temperature": 0.3, "max_tokens": 77}, max_context=9000)
    _old_preset(ctx, "Other", {"temperature": 1.5})  # per-chat only: logged, never merged
    ctx.settings.set("active_preset", chosen["id"])
    before = len(ctx.backups.archives())

    moved = preset_service.move_model_settings(ctx)

    assert moved == {"temperature": 0.3, "max_tokens": 77, "max_context_tokens": 9000}
    assert len(ctx.backups.archives()) == before + 1  # a whole-data copy was taken first
    params = ctx.connections.get(conn["id"])["params"]
    assert params == {"temperature": 0.3, "top_p": 0.8, "max_tokens": 77, "max_context_tokens": 9000}

    ctx.connections.update(conn["id"], params={"temperature": 0.9})  # the person changes it afterwards
    assert preset_service.move_model_settings(ctx) == {}  # second start does nothing
    assert ctx.connections.get(conn["id"])["params"] == {"temperature": 0.9}
    assert len(ctx.backups.archives()) == before + 1


async def test_move_waits_for_a_connection_and_ignores_the_builtin_preset(client, ctx):
    chosen = _old_preset(ctx, "Mine", {"top_p": 0.7})
    ctx.settings.set("active_preset", chosen["id"])
    assert preset_service.move_model_settings(ctx) == {}
    assert not ctx.settings.get(preset_service.MOVED_PARAMS)  # no connection yet: tried again next start
    conn = await _connection(client)
    assert preset_service.move_model_settings(ctx) == {"top_p": 0.7}
    assert ctx.connections.get(conn["id"])["params"] == {"top_p": 0.7}


async def test_nothing_to_move_just_sets_the_flag(client, ctx):
    conn = await _connection(client, temperature=0.4)
    assert preset_service.move_model_settings(ctx) == {}
    assert ctx.settings.get(preset_service.MOVED_PARAMS) is True
    assert ctx.connections.get(conn["id"])["params"] == {"temperature": 0.4}


async def test_old_preset_files_with_params_still_load(client, ctx):
    old = {"tavern_preset": 1, "name": "Old", "data": {**default_preset(), "params": {"temperature": 0.2},
                                                       "max_context_tokens": 4000}}
    import json
    created = await client.post("/api/presets/import?name=old.json", content=json.dumps(old).encode())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["model_params"] == {"temperature": 0.2, "max_context_tokens": 4000}
    assert body["data"]["params"] == {}
    got = (await client.get(f"/api/presets/{body['id']}")).json()  # and a stored old one still resolves
    assert got["data"]["max_context_tokens"] == 0
    stored = _old_preset(ctx, "Stored", {"temperature": 0.2})
    assert (await client.get(f"/api/presets/{stored['id']}")).json()["data"]["params"] == {"temperature": 0.2}
