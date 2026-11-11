"""Updates must not leave an old custom look breaking the new interface; safe mode from the console (ADR 0014)."""

from __future__ import annotations

import pytest

from tavern.cli import main
from tavern.services import look


def custom_look(ctx):
    mine = ctx.themes.create("Mine")["id"]
    ctx.settings.set("theme", mine)
    ctx.settings.set("character_themes", {"c1": mine, "c2": "sakura"})
    ctx.settings.set("trusted_theme_scripts", {mine: "abc"})
    ctx.settings.set(look.KEY_STAMP, "old")
    return mine


def test_an_update_switches_the_custom_look_off_and_deletes_nothing(ctx):
    mine = custom_look(ctx)
    done = look.check_upgrade(ctx, "new")
    assert done and done["theme"] == mine
    assert ctx.settings.get("theme") is None                        # the built-in default takes over
    assert ctx.settings.get("character_themes") == {"c2": "sakura"}  # built-in picks stay
    assert ctx.settings.get(look.KEY_STAMP) == "new"
    assert ctx.themes.get(mine)["id"] == mine                       # the theme itself is untouched
    assert (ctx.paths.themes / mine / "theme.css").is_file()
    assert ctx.settings.get("trusted_theme_scripts") == {mine: "abc"}


def test_the_parked_look_comes_back_with_one_action(ctx):
    mine = custom_look(ctx)
    look.check_upgrade(ctx, "new")
    assert look.status(ctx)["parked"]["theme"] == mine
    assert look.restore(ctx) == {"restored": True}
    assert ctx.settings.get("theme") == mine
    assert ctx.settings.get("character_themes") == {"c1": mine, "c2": "sakura"}
    assert look.status(ctx)["parked"] is None


def test_without_an_update_nothing_changes(ctx):
    mine = custom_look(ctx)
    ctx.settings.set(look.KEY_STAMP, "same")
    assert look.check_upgrade(ctx, "same") is None
    assert ctx.settings.get("theme") == mine and look.parked(ctx) is None


def test_a_first_run_with_nothing_custom_just_records_the_stamp(ctx):
    assert look.check_upgrade(ctx, "v1") is None
    assert ctx.settings.get(look.KEY_STAMP) == "v1" and look.parked(ctx) is None
    ctx.settings.set("theme", "sakura")  # built-in choices survive updates
    assert look.check_upgrade(ctx, "v2") is None and ctx.settings.get("theme") == "sakura"


def test_data_from_before_the_stamp_existed_counts_as_updated(ctx):
    mine = ctx.themes.create("Old")["id"]
    ctx.settings.set("theme", mine)  # no stamp stored
    assert look.check_upgrade(ctx, "v1") is not None
    assert ctx.settings.get("theme") is None


def test_restore_skips_a_theme_that_is_gone(ctx):
    mine = custom_look(ctx)
    look.check_upgrade(ctx, "new")
    ctx.themes.delete(mine)
    look.restore(ctx)
    assert ctx.settings.get("theme") is None


def test_the_stamp_follows_the_interface_files(tmp_path):
    (tmp_path / "a.js").write_text("one")
    first = look.interface_stamp(tmp_path)
    assert look.interface_stamp(tmp_path) == first
    (tmp_path / "a.js").write_text("two")
    assert look.interface_stamp(tmp_path) != first


async def test_look_over_http(client, ctx):
    mine = custom_look(ctx)
    look.check_upgrade(ctx, "new")
    state = (await client.get("/api/look")).json()
    assert state["safe_mode"] is False and state["parked"]["theme"] == mine and not state["parked"]["seen"]
    await client.post("/api/look/dismiss")
    assert (await client.get("/api/look")).json()["parked"]["seen"] is True
    assert (await client.post("/api/look/restore")).json() == {"restored": True}
    assert (await client.get("/api/settings")).json()["theme"] == mine
    on = (await client.post("/api/look/safe", json={"on": True})).json()
    assert on["safe_mode"] and on["safe_persistent"]
    assert not (await client.post("/api/look/safe", json={"on": False})).json()["safe_mode"]


@pytest.fixture
def run(data_paths, capsys):
    def invoke(*args: str) -> tuple[int, str]:
        code = main(["--data-dir", str(data_paths.root), *args])
        return code, capsys.readouterr().out

    return invoke


def test_safe_mode_command_is_persistent(run, data_paths):
    assert "выключен" in run("safe-mode", "status")[1]
    code, out = run("safe-mode", "on")
    assert code == 0 and "ВКЛЮЧЁН" in out
    assert "ВКЛЮЧЁН" in run("safe-mode", "status")[1]  # a new process sees it
    assert "выключен" in run("safe-mode", "off")[1]
    assert "выключен" in run("safe-mode", "status")[1]


def test_safe_flag_is_a_serve_option():
    from tavern.cli import build_parser

    assert build_parser().parse_args(["serve", "--safe"]).safe is True
    assert build_parser().parse_args(["serve"]).safe is False


def plugin_with_css(ctx, plugin_id: str = "mine"):
    import json

    folder = ctx.paths.plugins / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps(
        {"id": plugin_id, "name": "Свой", "version": "1", "web": "web.js", "style": "style.css"}), encoding="utf-8")
    (folder / "web.js").write_text("export default function setup(t) {}\n", encoding="utf-8")
    (folder / "style.css").write_text(".x { color: red; }\n", encoding="utf-8")


async def test_an_update_parks_user_plugin_styles_but_not_their_work_or_built_in_ones(client, ctx):
    plugin_with_css(ctx)
    ctx.plugins.enable("mine", approve=True)
    ctx.settings.set(look.KEY_STAMP, "old")
    assert (await client.get("/api/plugins/mine")).json()["style"] == "style.css"
    assert (await client.get("/plugins/mine/style.css")).status_code == 200

    done = look.check_upgrade(ctx, "new")
    assert done and done["plugin_css"] == ["mine"]
    mine = (await client.get("/api/plugins/mine")).json()
    assert mine["style"] == "" and mine["style_parked"] is True
    assert mine["enabled"] and mine["web"] == "web.js"                     # the plugin itself keeps working
    assert (await client.get("/plugins/mine/web.js")).status_code == 200
    assert (await client.get("/plugins/mine/style.css")).status_code == 404
    assert (await client.get("/plugins/mine/style.css?v=1")).status_code == 404
    assert (await client.get("/api/look")).json()["parked"]["plugin_css"] == 1
    assert (await client.get("/api/settings")).json().get("theme") is None
    # built-in plugins are not touched
    for p in (await client.get("/api/plugins")).json()["plugins"]:
        if p["builtin"]:
            assert not p.get("style_parked")

    assert (await client.post("/api/look/restore")).json() == {"restored": True}
    mine = (await client.get("/api/plugins/mine")).json()
    assert mine["style"] == "style.css" and not mine.get("style_parked")
    assert (await client.get("/plugins/mine/style.css")).status_code == 200
    assert look.check_upgrade(ctx, "new") is None                           # not parked again on the same version


def test_a_later_update_keeps_what_an_earlier_one_parked(ctx):
    mine = custom_look(ctx)
    look.check_upgrade(ctx, "v2")
    plugin_with_css(ctx)
    look.check_upgrade(ctx, "v3")
    saved = look.parked(ctx)
    assert saved["theme"] == mine and saved["plugin_css"] == ["mine"]
    look.restore(ctx)
    assert ctx.settings.get("theme") == mine and look.css_off(ctx) == set()


def test_nothing_parked_without_user_plugin_styles(ctx):
    ctx.plugins.start()
    assert look.check_upgrade(ctx, "v1") is None and look.css_off(ctx) == set()


def test_a_theme_picked_again_by_hand_is_no_longer_reported_as_switched_off(ctx):
    # Regression: the notice said «отключено, сейчас включён стандартный вид» while the old theme showed. The theme
    # had been turned on again by hand (or by the assistant, an undo, a copy) and a later start that parks nothing
    # new left the notice as it was.
    mine = custom_look(ctx)
    look.check_upgrade(ctx, "new")
    ctx.settings.set("theme", mine)                        # picked again in «Оформление»
    parked = look.status(ctx)["parked"]
    assert parked["theme"] is None and parked["characters"] == 1   # the character's own theme is still off
    ctx.settings.set("character_themes", {"c1": mine, "c2": "sakura"})
    assert look.status(ctx)["parked"] is None and look.parked(ctx) is None
    assert look.check_upgrade(ctx, "new") is None           # same files: nothing parked again
    assert look.status(ctx)["parked"] is None


def test_the_notice_names_the_theme_that_is_on_instead(ctx):
    custom_look(ctx)
    look.check_upgrade(ctx, "new")
    parked = look.status(ctx)["parked"]
    assert parked["now_theme"] == "default"
    ctx.settings.set("theme", "sakura")                    # another theme, not the parked one
    parked = look.status(ctx)["parked"]
    assert parked["theme"] and parked["now_theme"] == "sakura" and parked["now_theme_name"] == "Сакура"


def test_plugin_styles_keep_the_notice_until_restored(ctx):
    mine = custom_look(ctx)
    plugin_with_css(ctx)
    look.check_upgrade(ctx, "new")
    ctx.settings.set("theme", mine)
    ctx.settings.set("character_themes", {"c1": mine, "c2": "sakura"})
    parked = look.status(ctx)["parked"]
    assert parked and parked["plugin_css"] == 1 and parked["theme"] is None
    assert look.css_off(ctx) == {"mine"}
    look.restore(ctx)
    assert look.css_off(ctx) == set() and ctx.settings.get("theme") == mine


async def test_after_the_update_the_page_gets_the_standard_look_and_restore_brings_it_back(client, ctx):
    mine = custom_look(ctx)
    look.check_upgrade(ctx, "new")
    settings = (await client.get("/api/settings")).json()
    assert "theme" not in settings and settings.get("character_themes") == {"c2": "sakura"}
    state = (await client.get("/api/look")).json()["parked"]
    assert state["theme"] == mine and state["now_theme"] == "default"
    await client.post("/api/look/dismiss")                 # «Понятно»: the standard look stays
    assert "theme" not in (await client.get("/api/settings")).json()
    await client.put("/api/settings", json={"theme": mine})  # picked again by hand
    assert (await client.get("/api/look")).json()["parked"]["theme"] is None
