"""Plugin-owned tables (ADR 0019 section 2.2-B): migrations applied before setup, names must start with p_<id>_,
a bad file rolls back and leaves only that plugin off, versions, drop with data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavern.plugins import schema
from tavern.plugins.manifest import PluginError, normalize

SERVER = "def setup(api):\n    api.storage().set('seen', api.ctx.db.fetchone('SELECT COUNT(*) AS n FROM p_demo_things')['n'])\n"
FIRST = """
CREATE TABLE p_demo_things (
    id TEXT PRIMARY KEY,
    chat_id TEXT REFERENCES chats(id) ON DELETE CASCADE,
    note TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX p_demo_things_chat ON p_demo_things(chat_id);
"""


def write(ctx, files: dict[str, str], plugin_id: str = "demo", migrations: str = "migrations") -> Path:
    folder = ctx.paths.plugins / plugin_id
    manifest = {"id": plugin_id, "name": "Демо", "version": "1", "server": "server.py", "migrations": migrations}
    (folder / "migrations").mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "server.py").write_text(SERVER, encoding="utf-8")
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def tables(ctx) -> set[str]:
    return {r["name"] for r in ctx.db.fetchall("SELECT name FROM sqlite_master WHERE name LIKE 'p\\_%' ESCAPE '\\'")}


def test_the_manifest_names_the_folder_and_refuses_odd_ones():
    assert normalize({"id": "demo", "migrations": "migrations"})["migrations"] == "migrations"
    assert normalize({"id": "demo"})["migrations"] == ""
    for bad in ("../x", "/abs", "a b", 5):
        with pytest.raises(PluginError):
            normalize({"id": "demo", "migrations": bad})


def test_migrations_run_before_setup_and_the_version_is_kept(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST})
    info = ctx.plugins.enable("demo", approve=True)
    assert info["error"] == "" and info["active"] is True
    assert "p_demo_things" in tables(ctx)
    assert info["schema_version"] == 1 and info["schema_latest"] == 1
    assert ctx.plugin_repo.data_get("demo", "", "seen") == 0  # setup could already read the table


def test_a_restart_does_not_apply_a_migration_twice(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST})
    ctx.plugins.enable("demo", approve=True)
    ctx.plugins.stop()
    ctx.plugins.start()  # would fail with "table exists" if it ran again
    assert ctx.plugins.get("demo")["active"] is True


def test_a_new_file_in_a_later_release_applies_on_top(ctx):
    folder = write(ctx, {"migrations/0001_things.sql": FIRST})
    ctx.plugins.enable("demo", approve=True)
    ctx.db.execute("INSERT INTO p_demo_things (id, note) VALUES ('a', 'kept')")
    (folder / "migrations" / "0002_more.sql").write_text(
        "ALTER TABLE p_demo_things ADD COLUMN extra TEXT NOT NULL DEFAULT 'x';\n", encoding="utf-8")
    ctx.plugins.enable("demo", approve=True)
    assert ctx.plugins.get("demo")["schema_version"] == 2
    row = ctx.db.fetchone("SELECT note, extra FROM p_demo_things")
    assert (row["note"], row["extra"]) == ("kept", "x")


def test_a_stray_name_rolls_the_file_back_and_leaves_the_plugin_off_with_the_error(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST + "\nCREATE TABLE sneaky (id TEXT) STRICT;\n"})
    info = ctx.plugins.enable("demo", approve=True)
    assert info["active"] is False and info["enabled"] is False
    assert "sneaky" in info["error"] and "p_demo_" in info["error"]
    assert tables(ctx) == set()  # the whole file rolled back, also its good tables
    assert ctx.db.fetchone("SELECT name FROM sqlite_master WHERE name = 'sneaky'") is None
    assert ctx.plugin_repo.get("demo")["schema_version"] == 0


def test_a_file_that_changes_a_core_table_is_refused(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST + "\nALTER TABLE messages ADD COLUMN spy TEXT;\n"})
    info = ctx.plugins.enable("demo", approve=True)
    assert info["active"] is False and "чужой" in info["error"]
    assert "spy" not in {r["name"] for r in ctx.db.fetchall("PRAGMA table_info(messages)")}


def test_a_failing_second_file_keeps_the_first_and_stops_there(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST, "migrations/0002_bad.sql": "CREATE TABL oops;\n"})
    info = ctx.plugins.enable("demo", approve=True)
    assert info["active"] is False and "0002_bad" in info["error"]
    assert ctx.plugin_repo.get("demo")["schema_version"] == 1 and "p_demo_things" in tables(ctx)


def test_data_newer_than_the_files_is_refused(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST})
    ctx.plugins.enable("demo", approve=True)
    ctx.plugin_repo.save("demo", schema_version=5)
    ctx.plugins.stop()
    info = ctx.plugins.enable("demo", approve=True)
    assert info["active"] is False and "новее" in info["error"]


def test_the_doctor_notes_a_plugin_whose_tables_are_not_the_folders_version(ctx):
    from tavern.services import doctor

    write(ctx, {"migrations/0001_things.sql": FIRST})
    ctx.plugins.enable("demo", approve=True)
    assert not [f for f in doctor.full(ctx) if f["id"].startswith("plugin-schema:")]
    ctx.plugin_repo.save("demo", schema_version=0)
    assert [f for f in doctor.full(ctx) if f["id"] == "plugin-schema:demo"]


def test_a_chat_that_goes_takes_the_plugins_rows_along(ctx):
    write(ctx, {"migrations/0001_things.sql": FIRST})
    ctx.plugins.enable("demo", approve=True)
    chat = ctx.chats.create_chat(character_id=None, title="t")
    ctx.db.execute("INSERT INTO p_demo_things (id, chat_id) VALUES ('a', ?)", (chat["id"],))
    ctx.chats.delete_chat(chat["id"])
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM p_demo_things")["n"] == 0


def test_delete_with_data_drops_the_tables_without_data_keeps_them_and_reinstall_continues(ctx):
    folder = write(ctx, {"migrations/0001_things.sql": FIRST})
    ctx.plugins.enable("demo", approve=True)
    ctx.db.execute("INSERT INTO p_demo_things (id, note) VALUES ('a', 'mine')")
    ctx.plugins.delete("demo", data=False)
    assert "p_demo_things" in tables(ctx)  # the data stays
    write(ctx, {"migrations/0001_things.sql": FIRST})  # installed again, the same files
    info = ctx.plugins.enable("demo", approve=True)
    assert info["active"] is True and info["schema_version"] == 1  # no "table exists" failure
    assert ctx.db.fetchone("SELECT note FROM p_demo_things")["note"] == "mine"
    ctx.plugins.delete("demo", data=True)
    assert tables(ctx) == set()
    assert folder.exists() is False


def test_schema_apply_checks_names_with_hyphenated_ids(ctx):
    ctx.plugin_repo.save("my-plugin")
    sql = "CREATE TABLE p_my_plugin_a (id TEXT PRIMARY KEY) STRICT;"
    from tavern.storage.migrations import Migration

    assert schema.apply(ctx.db, "my-plugin", [Migration(1, "a", sql)], 0) == [1]
    with pytest.raises(PluginError):
        schema.apply(ctx.db, "my-plugin", [Migration(2, "b", "CREATE TABLE p_other_a (id TEXT) STRICT;")], 1)
    assert schema.drop_all(ctx.db, "my-plugin") >= 1
    assert tables(ctx) == set()


def test_sql_files_are_part_of_the_approved_hash_but_never_served_to_the_browser(ctx):
    from tavern.plugins.manifest import WEB_EXTENSIONS, files_hash

    folder = write(ctx, {"migrations/0001_things.sql": FIRST})
    before = files_hash(folder)
    (folder / "migrations" / "0001_things.sql").write_text(FIRST + "\n-- edited\n", encoding="utf-8")
    assert files_hash(folder) != before
    assert ".sql" not in WEB_EXTENSIONS
