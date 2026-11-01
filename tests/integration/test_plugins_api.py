"""Plugins (stage 8а) through the HTTP API: list, approval, crashes, install (file and GitHub),
commands, actions, storage, model access (permission, limits, asking), reply writers, web files."""

from __future__ import annotations

import asyncio
import io
import json
import textwrap
import zipfile
from pathlib import Path

import httpx

from tavern.context import open_context
from tavern.paths import BUILTIN_PLUGINS_DIR

PROBE_SERVER = textwrap.dedent('''
    from tavern.core.generation import GenerationJob


    class Writer:
        id = "probe-writer"

        async def generate(self, job: GenerationJob):
            result = await job.llm.generate(await job.build_messages())
            yield "probe: " + result.text


    def setup(api):
        api.add_generator(Writer(), "Проба пишет")
        api.command("echo", lambda chat_id, args: "эхо " + args)

        def store(payload):
            box = api.storage(payload.get("chat_id", ""))
            box.set("n", box.get("n", 0) + 1)
            return box.all()

        async def ask_model(payload):
            result = await api.llm(chat_id=payload.get("chat_id")).generate([])
            return result.text

        api.action("store", store)
        api.action("ask_model", ask_model)
        api.action("boom", lambda payload: 1 / 0)
''')


def write_plugin(root: Path, plugin_id: str = "probe", *, server: str = PROBE_SERVER, **manifest) -> Path:
    folder = root / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {"id": plugin_id, "name": "Проба", "version": "1", "server": "server.py", "web": "web.js",
            "uses": ["models", "generator"], **manifest}
    (folder / "plugin.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (folder / "server.py").write_text(server, encoding="utf-8")
    (folder / "web.js").write_text("export default function setup(t) {}\n", encoding="utf-8")
    return folder


def zipped(folder: Path, top: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, top + path.relative_to(folder).as_posix())
    return buffer.getvalue()


async def plugin_of(client, plugin_id: str) -> dict:
    return next(p for p in (await client.get("/api/plugins")).json()["plugins"] if p["id"] == plugin_id)


async def chat_with_connection(client) -> dict:
    conn = (await client.post("/api/connections", json={
        "name": "Main", "base_url": "http://llm/v1", "model": "fake-model"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})
    return (await client.post("/api/chats", json={})).json()["chat"]


# -- list, approval, crashes ----------------------------------------------------------------------


async def test_built_ins_and_new_plugins_start_off(client, ctx):
    write_plugin(ctx.paths.plugins)
    body = (await client.get("/api/plugins")).json()
    by_id = {p["id"]: p for p in body["plugins"]}
    assert {"dice", "translate", "probe"} <= set(by_id)
    assert not any(p["enabled"] for p in body["plugins"])
    assert by_id["dice"]["builtin"] and not by_id["probe"]["builtin"]
    assert by_id["probe"]["access"]["models"] is False  # model use is off until allowed
    assert [g["id"] for g in body["generators"]] == ["default"]


async def test_a_plugin_needs_approval_and_a_built_in_does_not(client, ctx):
    write_plugin(ctx.paths.plugins)
    refused = await client.post("/api/plugins/probe/enable", json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "needs_approval"
    on = (await client.post("/api/plugins/probe/enable", json={"approve": True})).json()
    assert on["enabled"] and on["active"] and on["commands"] == ["echo"]
    assert any(g["id"] == "probe-writer" for g in (await client.get("/api/plugins")).json()["generators"])
    assert (await client.post("/api/plugins/dice/enable", json={})).json()["active"]

    off = (await client.post("/api/plugins/probe/disable")).json()
    assert not off["enabled"] and off["commands"] == []
    assert "probe-writer" not in ctx.generators.ids


async def test_changed_files_switch_a_plugin_off_until_approved_again(data_paths):
    ctx = open_context(data_paths)
    folder = write_plugin(ctx.paths.plugins)
    ctx.plugins.enable("probe", approve=True)
    ctx.close()
    (folder / "server.py").write_text(PROBE_SERVER + "\n# changed\n", encoding="utf-8")

    ctx = open_context(data_paths)
    ctx.plugins.start()
    plugin = ctx.plugins.get("probe")
    assert not plugin["enabled"] and "изменились" in plugin["error"]
    assert "probe" not in ctx.plugins.loaded
    ctx.close()


async def test_a_crashing_plugin_stays_off_and_the_rest_works(client, ctx):
    write_plugin(ctx.paths.plugins, "broken", server="def setup(api):\n    raise RuntimeError('нет')\n")
    write_plugin(ctx.paths.plugins, "noasync", server="async def setup(api):\n    pass\n")
    broken = (await client.post("/api/plugins/broken/enable", json={"approve": True})).json()
    assert not broken["enabled"] and "RuntimeError: нет" in broken["error"]
    noasync = (await client.post("/api/plugins/noasync/enable", json={"approve": True})).json()
    assert "обычной функцией" in noasync["error"]
    assert (await client.post("/api/plugins/dice/enable", json={})).json()["active"]


async def test_no_plugins_mode_skips_server_parts(data_paths):
    ctx = open_context(data_paths)
    ctx.plugins.enable("dice")
    ctx.close()
    ctx = open_context(data_paths, load_plugins=False)
    ctx.plugins.start()
    assert ctx.plugins.loaded == {} and ctx.plugins.commands == {}
    dice = ctx.plugins.get("dice")
    assert dice["server_off"] and not dice["active"]
    ctx.close()


# -- commands, actions, storage, web files ----------------------------------------------------------


async def test_dice_command_posts_a_message(client, ctx):
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    unknown = (await client.post("/api/plugins/command", json={"chat_id": chat["id"], "text": "/roll 2d6"})).json()
    assert unknown == {"handled": False}  # dice is still off
    await client.post("/api/plugins/dice/enable", json={})
    result = (await client.post("/api/plugins/command", json={"chat_id": chat["id"], "text": "/roll 2d6+1"})).json()
    assert result["handled"] and result["text"].startswith("🎲 2d6+1:")
    last = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert last["role"] == "system" and last["owner"] == "dice" and last["content"] == result["text"]
    assert not last["exclude_from_prompt"]

    await client.put("/api/plugins/dice/settings", json={"to_model": False})
    await client.post("/api/plugins/command", json={"chat_id": chat["id"], "text": "/r"})
    last = (await client.get(f"/api/chats/{chat['id']}/messages")).json()["messages"][-1]
    assert last["exclude_from_prompt"] and "1d20" in last["content"]  # the default formula

    bad = await client.post("/api/plugins/command", json={"chat_id": chat["id"], "text": "/roll xyz"})
    assert bad.status_code == 400 and "Не понимаю" in bad.json()["error"]["message"]


async def test_actions_storage_and_failures(client, ctx):
    write_plugin(ctx.paths.plugins)
    await client.post("/api/plugins/probe/enable", json={"approve": True})
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    for expected in (1, 2):
        result = (await client.post("/api/plugins/probe/actions/store", json={"chat_id": chat["id"]})).json()
        assert result == {"result": {"n": expected}}
    assert (await plugin_of(client, "probe"))["data_count"] == 1
    boom = await client.post("/api/plugins/probe/actions/boom", json={})
    assert boom.status_code == 500 and "ZeroDivisionError" in boom.json()["error"]["message"]
    assert (await client.post("/api/plugins/probe/actions/nope", json={})).status_code == 404
    echo = (await client.post("/api/plugins/command", json={"chat_id": chat["id"], "text": "/echo да"})).json()
    assert echo == {"handled": True, "text": "эхо да"}

    await client.delete(f"/api/chats/{chat['id']}")
    assert (await plugin_of(client, "probe"))["data_count"] == 0  # the chat's data went with it


async def test_web_files_only_of_active_plugins_and_never_python(client, ctx):
    write_plugin(ctx.paths.plugins)
    assert (await client.get("/plugins/probe/web.js")).status_code == 404
    await client.post("/api/plugins/probe/enable", json={"approve": True})
    web = await client.get("/plugins/probe/web.js")
    assert web.status_code == 200 and "javascript" in web.headers["content-type"]
    assert (await client.get("/plugins/probe/server.py")).status_code == 404
    assert (await client.get("/plugins/probe/..%2Fplugin.json")).status_code in (400, 404)


# -- install, update, delete ---------------------------------------------------------------------


async def test_install_from_a_file(client, ctx, tmp_path):
    blob = zipped(write_plugin(tmp_path / "src"), top="probe-main/")
    created = await client.post("/api/plugins/install", content=blob)
    assert created.status_code == 201
    plugin = created.json()
    assert plugin["id"] == "probe" and not plugin["enabled"] and plugin["source"]["kind"] == "file"
    again = await client.post("/api/plugins/install", content=blob)
    assert again.status_code == 409 and again.json()["error"]["code"] == "plugin_exists"

    await client.post("/api/plugins/probe/enable", json={"approve": True})
    replaced = (await client.post("/api/plugins/install?replace=1", content=blob)).json()
    assert not replaced["enabled"]  # new files: approve again
    assert "probe" not in ctx.plugins.loaded

    builtin = zipped(write_plugin(tmp_path / "fake", "dice"))
    refused = await client.post("/api/plugins/install", content=builtin)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "plugin_builtin"
    assert (BUILTIN_PLUGINS_DIR / "dice" / "server.py").read_text(encoding="utf-8").startswith('"""🎲')


async def test_install_and_update_from_github(client, ctx, tmp_path):
    folder = write_plugin(tmp_path / "src", version="1")
    shas = ["a" * 40]
    archives: list[str] = []

    def github(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/me/pack":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/me/pack/commits/main":
            return httpx.Response(200, json={"sha": shas[-1]})
        if path.startswith("/me/pack/zip/"):
            archives.append(path)
            return httpx.Response(200, content=zipped(folder, top="pack-x/plugins/probe/"))
        return httpx.Response(404)

    ctx.plugins.transport = httpx.MockTransport(github)
    created = await client.post("/api/plugins/install-url",
                                json={"url": "https://github.com/me/pack/tree/main/plugins/probe"})
    assert created.status_code == 201, created.text
    source = created.json()["source"]
    assert source["kind"] == "github" and source["sha"] == "a" * 40 and source["subdir"] == "plugins/probe"
    assert archives == ["/me/pack/zip/" + "a" * 40]

    assert (await client.get("/api/plugins/probe/update")).json()["update"] is False
    shas.append("b" * 40)
    (folder / "plugin.json").write_text(json.dumps({**json.loads((folder / "plugin.json").read_text(
        encoding="utf-8")), "version": "2"}), encoding="utf-8")
    assert (await client.get("/api/plugins/probe/update")).json() == {
        "current": "a" * 40, "latest": "b" * 40, "update": True}
    updated = (await client.post("/api/plugins/probe/update")).json()
    assert updated["version"] == "2" and updated["source"]["sha"] == "b" * 40 and not updated["enabled"]

    missing = await client.post("/api/plugins/install-url", json={"url": "https://github.com/me/none"})
    assert missing.status_code == 404
    bad = await client.post("/api/plugins/install-url", json={"url": "https://example.com/x"})
    assert bad.status_code == 400


async def test_delete_with_or_without_data(client, ctx):
    write_plugin(ctx.paths.plugins)
    await client.post("/api/plugins/probe/enable", json={"approve": True})
    await client.post("/api/plugins/probe/actions/store", json={})
    assert (await client.delete("/api/plugins/probe")).status_code == 204
    assert not (ctx.paths.plugins / "probe").exists()
    assert ctx.plugin_repo.data_count("probe") == 1  # kept: installing it again finds its data
    write_plugin(ctx.paths.plugins)
    assert (await client.delete("/api/plugins/probe?data=1")).status_code == 204
    assert ctx.plugin_repo.data_count("probe") == 0
    assert (await client.delete("/api/plugins/dice")).status_code == 409


# -- model access ------------------------------------------------------------------------------


async def test_model_access_permission_limits_and_spending(client, ctx, fake_llm):
    write_plugin(ctx.paths.plugins)
    chat = await chat_with_connection(client)
    await client.post("/api/plugins/probe/enable", json={"approve": True})
    ask = lambda: client.post("/api/plugins/probe/actions/ask_model", json={"chat_id": chat["id"]})  # noqa: E731

    refused = await ask()
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "plugin_no_models"
    assert fake_llm.requests == []

    await client.put("/api/plugins/probe/access", json={"models": True})
    fake_llm.queue("ответ модели")
    assert (await ask()).json() == {"result": "ответ модели"}
    rows = ctx.db.fetchall("SELECT purpose, chat_id FROM spending")
    assert [(r["purpose"], r["chat_id"]) for r in rows] == [("plugin:probe", chat["id"])]
    spent = (await plugin_of(client, "probe"))["spent"]
    assert spent["day"]["requests"] == 1 and spent["month"]["requests"] == 1
    report = (await client.get("/api/spending/report")).json()["plugins"]
    assert [(r["plugin"], r["name"], r["requests"]) for r in report] == [("probe", "Проба", 1)]

    ctx.db.execute("UPDATE spending SET cost = 0.5")
    await client.put("/api/plugins/probe/access", json={"day": 0.4})
    limited = await ask()
    assert limited.status_code == 409 and limited.json()["error"]["code"] == "plugin_limit"
    assert "день" in limited.json()["error"]["message"]
    bad = await client.put("/api/plugins/probe/access", json={"day": -1})
    assert bad.status_code == 400


async def test_asking_each_time(client, ctx, fake_llm):
    write_plugin(ctx.paths.plugins)
    chat = await chat_with_connection(client)
    await client.post("/api/plugins/probe/enable", json={"approve": True})
    await client.put("/api/plugins/probe/access", json={"models": True, "ask": True})

    # No page open to answer: refused at once instead of waiting.
    nobody = await client.post("/api/plugins/probe/actions/ask_model", json={"chat_id": chat["id"]})
    assert nobody.json()["error"]["code"] == "plugin_denied"

    async def page(allow: bool):
        async for event in ctx.notices.subscribe():
            if event["type"] == "plugin_ask":
                asks = (await client.get("/api/plugins/asks")).json()["asks"]
                assert asks[0]["name"] == "Проба" and asks[0]["chat_id"] == chat["id"]
                assert (await client.post(f"/api/plugins/asks/{asks[0]['id']}", json={"allow": allow})
                        ).status_code == 204
            if event["type"] == "plugin_asked":
                return

    for allow, expected in ((True, 200), (False, 409)):
        fake_llm.queue("можно")
        listener = asyncio.create_task(page(allow))
        await asyncio.sleep(0)
        response = await client.post("/api/plugins/probe/actions/ask_model", json={"chat_id": chat["id"]})
        await asyncio.wait_for(listener, 5)
        assert response.status_code == expected
    assert (await client.post("/api/plugins/asks/gone", json={"allow": True})).status_code == 404


async def test_a_plugin_writes_replies_under_its_rules(client, ctx, fake_llm):
    write_plugin(ctx.paths.plugins)
    chat = await chat_with_connection(client)
    await client.post("/api/plugins/probe/enable", json={"approve": True})
    assert (await client.put(f"/api/chats/{chat['id']}/meta", json={"generator": "probe-writer"})).status_code == 200

    async def send() -> list[dict]:
        response = await client.post(f"/api/chats/{chat['id']}/send", json={"text": "привет"})
        run_id = response.json()["run_id"]
        await asyncio.wait([ctx.runs.get(run_id).task], timeout=10)
        body = (await client.get(f"/api/runs/{run_id}/events")).text
        return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]

    events = await send()
    assert events[-1]["type"] == "error" and events[-1]["error"]["code"] == "plugin_no_models"

    await client.put("/api/plugins/probe/access", json={"models": True, "per_reply": 1})
    fake_llm.queue("мысль")
    events = await send()
    assert events[-1]["type"] == "done" and events[-1]["message"]["content"] == "probe: мысль"
    assert [r["purpose"] for r in ctx.db.fetchall("SELECT purpose FROM spending")] == ["plugin:probe"]

    await client.post("/api/plugins/probe/disable")
    fake_llm.queue("обычный ответ")
    events = await send()  # its writer is gone: the chat falls back to the usual way
    assert events[-1]["message"]["content"] == "обычный ответ"


async def test_translate_action_keeps_translations(client, ctx):
    import sys

    await client.post("/api/plugins/translate/enable", json={})
    module = sys.modules["tavern_plugin_translate"]
    module.transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[[["Привет", "Hello", None]]]))
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    message = ctx.chats.add_message(chat["id"], role="assistant", content="Hello")
    result = (await client.post("/api/plugins/translate/actions/translate", json={"message_id": message["id"]})).json()
    assert result["result"] == {"key": f"{message['id']}:0", "text": "Привет", "to": "ru"}
    kept = (await client.post("/api/plugins/translate/actions/list", json={"chat_id": chat["id"]})).json()
    assert kept["result"] == {f"{message['id']}:0": {"text": "Привет", "to": "ru"}}
