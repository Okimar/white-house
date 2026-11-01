"""Stage 8в: the tree of thoughts plugin's logic (who thinks, what each is told) and the new kinds of
plugin settings it needs (a list of rows, a connection)."""

from __future__ import annotations

import importlib.util
import json
from types import SimpleNamespace

import pytest

from tavern.core.assembly import Member
from tavern.paths import BUILTIN_PLUGINS_DIR
from tavern.plugins.manifest import PluginError, check_settings, normalize, with_defaults


def load_thoughts():
    spec = importlib.util.spec_from_file_location("test_builtin_thoughts", BUILTIN_PLUGINS_DIR / "thoughts" / "server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manifest() -> dict:
    raw = json.loads((BUILTIN_PLUGINS_DIR / "thoughts" / "plugin.json").read_text(encoding="utf-8"))
    return normalize(raw, folder_id="thoughts")


def test_the_built_in_manifest_has_its_thinkers_by_default():
    fields = manifest()["settings"]
    values = with_defaults(fields, {})
    assert [row["name"] for row in values["thinkers"]] == ["Мир", "Сюжет"]
    assert values["rounds"] == 1 and values["show"] == "under" and values["connection"] == ""
    by_key = {f["key"]: f for f in fields}
    assert by_key["character_task"]["multiline"] is True
    assert [f["key"] for f in by_key["thinkers"]["fields"]] == ["on", "name", "task"]


def test_list_settings_fill_rows_and_refuse_bad_ones():
    fields = manifest()["settings"]
    saved = check_settings(fields, {"thinkers": [{"name": "Критик"}]}, {})
    assert saved["thinkers"] == [{"on": True, "name": "Критик", "task": ""}]  # missing fields take defaults
    saved = check_settings(fields, {"thinkers": [{"name": "Шут", "extra": 1, "on": False}]}, {})
    assert saved["thinkers"] == [{"on": False, "name": "Шут", "task": ""}]  # unknown keys are dropped
    for bad in ("Критик", [1], [{"on": "да"}], [{}] * 101):
        with pytest.raises(PluginError):
            check_settings(fields, {"thinkers": bad}, {})
    assert check_settings(fields, {"connection": "abc"}, {})["connection"] == "abc"
    with pytest.raises(PluginError):
        check_settings(fields, {"connection": 5}, {})


def test_a_list_setting_needs_its_fields():
    raw = {"id": "x", "name": "X", "version": "1", "settings": [{"key": "rows", "type": "list"}]}
    with pytest.raises(PluginError, match="fields"):
        normalize(raw)
    raw["settings"][0]["fields"] = [{"key": "inner", "type": "list", "fields": [{"key": "a"}]}]
    with pytest.raises(PluginError, match="неизвестен"):  # no lists inside lists
        normalize(raw)


def job_with(members: list[Member]):
    return SimpleNamespace(prompt_input=SimpleNamespace(members=members))


def test_who_thinks():
    module = load_thoughts()
    settings = with_defaults(manifest()["settings"], {})
    members = [Member("a", "Аня", {}), Member("me", "Я", {}, is_me=True), Member("b", "Борис", {})]
    names = [(t.name, t.kind) for t in module.thinkers(settings, job_with(members))]
    assert names == [("Аня", "character"), ("Борис", "character"), ("Мир", "custom"), ("Сюжет", "custom")]

    settings["characters"] = False
    settings["thinkers"] = [{"on": True, "name": " Критик ", "task": "ищи слабые места"},
                            {"on": False, "name": "Шут", "task": ""}, {"on": True, "name": " ", "task": "x"}]
    assert [(t.name, t.task) for t in module.thinkers(settings, job_with(members))] == [("Критик", "ищи слабые места")]


def test_what_a_thinker_is_told():
    module = load_thoughts()
    character = module.Thinker("Аня", "Чего ты хочешь?", "character")
    text = module.instruction(character, 50, [])
    assert "внутренний голос персонажа Аня" in text and "Чего ты хочешь?" in text and "50 слов" in text
    assert "прошлом круге" not in text

    world = module.Thinker("Мир", "", "custom")
    text = module.instruction(world, 80, [{"name": "Аня", "text": "хочу уйти"}, {"name": "Сюжет", "text": ""}])
    assert "«Мир»" in text and "[Аня]: хочу уйти" in text and "[Сюжет]" not in text

    note = module.closing_note([{"name": "Мир", "text": "идёт дождь"}, {"name": "Сюжет", "text": ""}])
    assert "[Мир]: идёт дождь" in note and "[Сюжет]" not in note
