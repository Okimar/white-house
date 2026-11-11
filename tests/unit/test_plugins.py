"""Plugins (stage 8а), pure parts: plugin.json, files and their hash, zips, GitHub links, access
settings, the built-in dice and translation helpers."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import random
import zipfile
from pathlib import Path

import httpx
import pytest

from tavern.core.notices import Notices
from tavern.paths import BUILTIN_PLUGINS_DIR
from tavern.plugins.install import parse_github, write_folder
from tavern.plugins.manager import check_access
from tavern.plugins.manifest import (
    PluginError, check_settings, files_hash, normalize, read_manifest, read_zip, with_defaults,
)


def load_builtin(name: str):
    spec = importlib.util.spec_from_file_location(f"test_builtin_{name}", BUILTIN_PLUGINS_DIR / name / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


MANIFEST = {"id": "demo", "name": "Демо", "version": "1.2", "server": "server.py",
            "uses": ["commands", "models", "unknown"],
            "settings": [{"key": "n", "label": "Число", "type": "number", "default": 3, "min": 1, "max": 9},
                         {"key": "lang", "type": "choice", "choices": [["ru", "Русский"], ["en", "English"]]}]}


# -- plugin.json ----------------------------------------------------------------------------------


def test_manifest_is_cleaned_and_checked():
    clean = normalize(MANIFEST)
    assert clean["uses"] == ["commands", "models"]  # unknown claims are dropped
    assert clean["settings"][1]["default"] == "ru"  # a choice defaults to its first option
    assert clean["web"] == "" and clean["style"] == ""
    with pytest.raises(PluginError):
        normalize({**MANIFEST, "id": "Bad Id"})
    with pytest.raises(PluginError):
        normalize(MANIFEST, folder_id="other")
    with pytest.raises(PluginError):
        normalize({**MANIFEST, "server": "../evil.py"})
    with pytest.raises(PluginError):
        normalize({**MANIFEST, "web": "web.py"})


def test_settings_are_coerced_and_checked():
    fields = normalize(MANIFEST)["settings"]
    assert with_defaults(fields, {}) == {"n": 3, "lang": "ru"}
    assert check_settings(fields, {"n": 5}, {"lang": "en", "gone": 1}) == {"lang": "en", "n": 5}
    for bad in ({"n": 10}, {"n": "5"}, {"lang": "de"}, {"nope": 1}):
        with pytest.raises(PluginError):
            check_settings(fields, bad, {})


def test_a_model_setting_names_its_connection_setting():
    """Type "model" (owner, 2026-11-11): a model of the connection chosen in `connection_key`; "" = its own model."""
    raw = {**MANIFEST, "settings": [{"key": "conn", "type": "connection"},
                                    {"key": "model", "type": "model", "connection_key": "conn"}]}
    fields = normalize(raw)["settings"]
    assert fields[1]["type"] == "model" and fields[1]["connection_key"] == "conn" and fields[1]["default"] == ""
    assert check_settings(fields, {"model": "gemini-lite"}, {}) == {"model": "gemini-lite"}
    with pytest.raises(PluginError):
        check_settings(fields, {"model": 5}, {})
    with pytest.raises(PluginError):  # connection_key must name a "connection" setting
        normalize({**MANIFEST, "settings": [{"key": "model", "type": "model", "connection_key": "nope"}]})


def test_memory_plugin_has_one_model_setting_for_every_task():
    manifest = read_manifest(BUILTIN_PLUGINS_DIR / "memory")
    keys = [f["key"] for f in manifest["settings"]]
    assert [k for k in keys if k.endswith(("_connection", "_model_name"))] == ["memory_connection", "memory_model_name"]
    model = next(f for f in manifest["settings"] if f["key"] == "memory_model_name")
    assert model["type"] == "model" and model["connection_key"] == "memory_connection"
    # settings stored by an older version (own model per task) are dropped, not an error
    old = {"summary_connection": "x", "summary_model_name": "big", "memory_model_name": "small"}
    assert check_settings(manifest["settings"], {}, old) == {"memory_model_name": "small"}


def test_access_settings_are_checked():
    assert check_access({"models": True, "per_reply": 3.0}, {})["per_reply"] == 3
    for bad in ({"models": 1}, {"day": -1}, {"day": True}, {"other": 1}):
        with pytest.raises(PluginError):
            check_access(bad, {})


def test_read_manifest_needs_the_named_files(tmp_path: Path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "plugin.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    with pytest.raises(PluginError, match="server"):
        read_manifest(folder)
    (folder / "server.py").write_text("def setup(api): pass\n", encoding="utf-8")
    assert read_manifest(folder)["name"] == "Демо"


def test_hash_follows_files_but_not_bytecode(tmp_path: Path):
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "server.py").write_text("x = 1\n")
    first = files_hash(folder)
    (folder / "__pycache__").mkdir()
    (folder / "__pycache__" / "server.cpython-311.pyc").write_bytes(b"junk")
    assert files_hash(folder) == first
    (folder / "server.py").write_text("x = 2\n")
    assert files_hash(folder) != first
    (folder / "server.py").write_text("x = 1\n")
    (folder / "extra.js").write_text("")
    assert files_hash(folder) != first  # a new file is a change too


# -- zips ----------------------------------------------------------------------------------------


def test_zip_of_a_github_archive_and_a_subfolder():
    blob = make_zip({
        "repo-abc/README.md": "hi",
        "repo-abc/plugin.json": json.dumps({"id": "top", "name": "Top"}),
        "repo-abc/plugins/demo/plugin.json": json.dumps({**MANIFEST, "server": "server.py"}),
        "repo-abc/plugins/demo/server.py": "def setup(api): pass\n",
        "repo-abc/plugins/demo/__pycache__/x.pyc": b"junk",
        "repo-abc/plugins/demo/.hidden": "x",
    })
    manifest, files = read_zip(blob)
    assert manifest["id"] == "top"  # the shallowest plugin.json wins
    manifest, files = read_zip(blob, subdir="plugins/demo")
    assert manifest["id"] == "demo"
    assert set(files) == {"plugin.json", "server.py"}


def test_zip_problems_are_reported():
    with pytest.raises(PluginError, match="zip"):
        read_zip(b"not a zip")
    with pytest.raises(PluginError, match="plugin.json"):
        read_zip(make_zip({"server.py": "x"}))
    with pytest.raises(PluginError):
        read_zip(make_zip({"plugin.json": json.dumps(MANIFEST), "server.py": "x", "../evil.py": "x"}))
    with pytest.raises(PluginError, match="server"):
        read_zip(make_zip({"plugin.json": json.dumps(MANIFEST)}))
    # files that are not part of a plugin (a build script) are skipped, code is not
    manifest, files = read_zip(make_zip({"plugin.json": json.dumps(MANIFEST), "server.py": "x", "build.sh": "x"}))
    assert "build.sh" not in files


def test_write_folder_replaces_the_whole_plugin(tmp_path: Path):
    write_folder(tmp_path, "demo", {"a.py": b"1", "web/b.js": b"2"})
    write_folder(tmp_path, "demo", {"a.py": b"3"})
    assert (tmp_path / "demo" / "a.py").read_bytes() == b"3"
    assert not (tmp_path / "demo" / "web").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]


def test_github_links():
    assert parse_github("https://github.com/me/dice") == {"owner": "me", "repo": "dice", "ref": "", "subdir": ""}
    assert parse_github("https://github.com/me/dice.git")["repo"] == "dice"
    assert parse_github("https://github.com/me/all/tree/v2/plugins/dice/") == {
        "owner": "me", "repo": "all", "ref": "v2", "subdir": "plugins/dice"}
    for bad in ("https://gitlab.com/me/dice", "github.com/me", "https://github.com/me"):
        with pytest.raises(PluginError):
            parse_github(bad)


# -- notices ---------------------------------------------------------------------------------


async def test_notices_reach_every_open_page():
    notices = Notices()
    got: list[dict] = []

    async def page():
        async with contextlib.aclosing(notices.subscribe()) as events:  # the page closes its stream
            async for event in events:
                got.append(event)
                if len(got) == 2:
                    return

    task = asyncio.create_task(page())
    await asyncio.sleep(0)
    assert notices.listeners == 1
    notices.emit("plugins")
    notices.emit("plugin", plugin="x", name="done", data={})
    await asyncio.wait_for(task, 1)
    assert [e["type"] for e in got] == ["plugins", "plugin"]
    assert notices.listeners == 0


# -- built-in: dice ----------------------------------------------------------------------------


def test_dice_rolls_and_describes():
    dice = load_builtin("dice")
    result = dice.roll("2d6+1", random.Random(1))
    assert result["formula"] == "2d6+1"
    rolls = result["parts"][0]["rolls"]
    assert len(rolls) == 2 and all(1 <= r <= 6 for r in rolls)
    assert result["total"] == sum(rolls) + 1
    assert dice.describe(result) == f"🎲 2d6+1: [{rolls[0]}, {rolls[1]}] + 1 = {result['total']}"
    assert dice.roll("3К6-2", random.Random(2))["formula"] == "3d6-2"  # Russian "к", any case
    assert dice.roll("d20", random.Random(3))["parts"][0]["dice"] == "1d20"
    assert dice.describe(dice.roll("-1", random.Random(0))) == "🎲 -1: − 1 = -1"


@pytest.mark.parametrize("formula", ["", "abc", "2d", "1000d6", "1d1", "1d5000", "1+" * 11 + "1"])
def test_dice_refuses_odd_formulas(formula):
    dice = load_builtin("dice")
    with pytest.raises(dice.BadFormula):
        dice.roll(formula)


# -- built-in: translation ---------------------------------------------------------------------


def test_translation_is_split_between_lines():
    translate = load_builtin("translate")
    text = "a" * 30 + "\n" + "b" * 30 + "\n" + "c" * 75
    parts = translate.pieces(text, size=40)
    assert "".join(parts) == text
    assert all(len(p) <= 40 for p in parts)
    assert parts[0] == "a" * 30 + "\n"


async def test_translation_talks_to_google():
    translate = load_builtin("translate")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[[["Привет. ", "Hello. ", None], ["Как дела?", "How are you?", None]],
                                         None, "en"])

    translate.transport = httpx.MockTransport(handler)
    assert await translate.translate("Hello. How are you?", "ru") == "Привет. Как дела?"
    assert seen[0].url.params["tl"] == "ru" and seen[0].url.params["client"] == "gtx"
    assert b"q=Hello" in seen[0].content
    assert await translate.translate("   ", "ru") == ""

    translate.transport = httpx.MockTransport(lambda r: httpx.Response(429))
    with pytest.raises(translate.TranslateFailed, match="временно"):
        await translate.translate("Hello", "ru")
    translate.transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(translate.TranslateFailed, match="непонятный"):
        await translate.translate("Hello", "ru")
