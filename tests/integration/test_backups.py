"""Stage 9а: whole-data copies (what goes in, when, how many, where), restore into the running app
and what happens when something fails; plugin versions; the doctor."""

from __future__ import annotations

import errno
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from tavern.context import open_context
from tavern.plugins.manifest import files_hash
from tavern.services import backups as backups_module
from tavern.services import doctor
from tavern.services.backups import BackupError
from tests.integration.test_plugins_api import write_plugin, zipped


def names(ctx) -> list[str]:
    return [a.name for a in ctx.backups.archives()]


def test_a_copy_holds_data_files_but_never_keys(ctx):
    ctx.paths.secrets.write_text('{"keys": "sk-secret"}', encoding="utf-8")
    (ctx.paths.assets / "cat.png").write_bytes(b"\x89PNG fake")
    (ctx.paths.themes / "mine" / ".versions").mkdir(parents=True)
    (ctx.paths.themes / "mine" / "theme.css").write_text("body{}", encoding="utf-8")
    (ctx.paths.themes / "mine" / ".versions" / "old.css").write_text("x", encoding="utf-8")
    made = ctx.backups.create("manual")
    with zipfile.ZipFile(ctx.paths.backups / made["name"]) as zf:
        inside = set(zf.namelist())
        blob = b"".join(zf.read(n) for n in inside)
    assert {"manifest.json", "tavern.sqlite3", "assets/cat.png", "themes/mine/theme.css"} <= inside
    assert not any(".versions" in n for n in inside) and b"sk-secret" not in blob


def test_no_new_copy_without_changes_and_pruning_by_kind(ctx):
    assert ctx.backups.create("start") is not None
    assert ctx.backups.create("start") is None  # nothing changed
    ctx.backups.save_settings({"keep": {"hourly": 2}})
    for i in range(4):
        ctx.settings.set("probe", i)
        ctx.backups.create("hourly")
    ctx.backups.create("manual", force=True)
    kinds = [a.kind for a in ctx.backups.archives()]
    assert kinds.count("hourly") == 2 and kinds.count("manual") == 1 and kinds.count("start") == 1


def test_disk_full_leaves_no_torn_copy_and_does_not_block_actions(ctx, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(backups_module, "write_archive", full)
    with pytest.raises(BackupError):
        ctx.backups.create("manual", force=True)
    assert "Копия не сделана" in ctx.backups.last_error
    ctx.backups.before("delete-chat")  # the risky action goes on
    assert names(ctx) == [] and not list(ctx.paths.backups.glob("*.part"))


def test_own_folder_and_falling_back_when_it_is_gone(ctx, tmp_path):
    own = tmp_path / "drive"
    ctx.backups.save_settings({"dir": str(own)})
    made = ctx.backups.create("manual")
    assert (own / made["name"]).is_file()
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x", encoding="utf-8")
    ctx.backups.save_settings({"dir": str(blocker / "sub")})
    made = ctx.backups.create("manual", force=True)
    assert (ctx.paths.backups / made["name"]).is_file() and "недоступна" in made["warning"]
    assert any(f["id"] == "folder" for f in doctor.quick(ctx))


def test_schedule_daily_and_every_n_hours(ctx):
    ctx.backups.save_settings({"daily_hour": 4, "every_hours": 6})
    early = datetime.now().replace(hour=3)
    assert ctx.backups.tick(early, mono=0) == ["hourly"]  # no hourly copy yet
    ctx.settings.set("probe", 1)
    assert ctx.backups.tick(early.replace(hour=5), mono=60) == ["daily"]
    assert ctx.backups.tick(early.replace(hour=6), mono=120) == []  # daily done, hourly not due
    assert ctx.backups.tick(early.replace(hour=7), mono=6 * 3600) == ["hourly"]


async def test_restore_brings_everything_back_and_keeps_backup_settings(client, ctx):
    chat = (await client.post("/api/chats", json={})).json()["chat"]
    (ctx.paths.assets / "cat.png").write_bytes(b"one")
    made = (await client.post("/api/backups")).json()

    ctx.settings.set("probe", 1)  # without a change since the last copy no new one is needed
    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 200
    assert any(a.kind == "risky" and a.reason == "delete-chat" for a in ctx.backups.archives())
    (ctx.paths.assets / "cat.png").write_bytes(b"two")
    (ctx.paths.assets / "new.png").write_bytes(b"new")
    await client.put("/api/backups/settings", json={"every_hours": 3})

    done = (await client.post(f"/api/backups/{made['name']}/restore")).json()
    assert done["safety"]
    assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 200
    assert (ctx.paths.assets / "cat.png").read_bytes() == b"one"
    assert not (ctx.paths.assets / "new.png").exists()
    assert (await client.get("/api/backups/status")).json()["settings"]["every_hours"] == 3


def test_a_failed_restore_leaves_everything_as_it_was(ctx, monkeypatch):
    ctx.settings.set("probe", "old")
    (ctx.paths.assets / "cat.png").write_bytes(b"old")
    made = ctx.backups.create("manual")
    ctx.settings.set("probe", "new")
    (ctx.paths.assets / "cat.png").write_bytes(b"new")

    real = ctx.backups._load_db
    calls = []

    def broken(source):
        calls.append(source)
        if len(calls) == 1:
            raise OSError("disk went away")
        real(source)

    monkeypatch.setattr(ctx.backups, "_load_db", broken)
    with pytest.raises(OSError):
        ctx.backups.restore(made["name"])
    assert ctx.settings.get("probe") == "new"
    assert (ctx.paths.assets / "cat.png").read_bytes() == b"new"


async def test_damaged_and_foreign_copies_are_refused(client, ctx):
    made = ctx.backups.create("manual")
    path = ctx.paths.backups / made["name"]
    path.write_bytes(path.read_bytes()[:200])
    assert (await client.post(f"/api/backups/{made['name']}/restore")).status_code == 400
    assert (await client.post("/api/backups/upload", content=b"not a zip")).status_code == 400
    assert (await client.post("/api/backups/secrets.json/restore")).status_code == 404


async def test_upload_download_and_delete(client, ctx):
    made = ctx.backups.create("manual")
    blob = (await client.get(f"/api/backups/{made['name']}")).content
    uploaded = (await client.post("/api/backups/upload", content=blob)).json()
    assert uploaded["name"].endswith("-upload-manual.zip")
    assert (await client.delete(f"/api/backups/{uploaded['name']}")).status_code == 204
    listed = (await client.get("/api/backups")).json()["backups"]
    assert [b["name"] for b in listed if b["kind"] != "migrate"] == [made["name"]]


# -- plugins ----------------------------------------------------------------------------------------

async def test_plugin_update_keeps_the_old_version_and_rollback_stays_approved(client, ctx, tmp_path):
    await client.post("/api/plugins/install", content=zipped(write_plugin(tmp_path / "v1")))
    assert (await client.post("/api/plugins/probe/enable", json={"approve": True})).json()["active"]
    await client.post("/api/plugins/install?replace=1", content=zipped(write_plugin(tmp_path / "v2", version="2")))

    versions = (await client.get("/api/plugins/probe/versions")).json()["versions"]
    assert [v["version"] for v in versions] == ["1"] and versions[0]["approved"]
    back = (await client.post("/api/plugins/probe/rollback", json={})).json()
    assert back["version"] == "1" and back["enabled"] and back["active"]
    assert [v["version"] for v in ctx.plugins.versions("probe")] == ["2"]  # the newer one is kept too


async def test_a_plugin_that_crashes_at_start_switches_itself_off(data_paths):
    ctx = open_context(data_paths)
    folder = write_plugin(ctx.paths.plugins)
    ctx.plugins.enable("probe", approve=True)
    (folder / "server.py").write_text("def setup(api):\n    raise RuntimeError('сломалось')\n", encoding="utf-8")
    ctx.plugin_repo.save("probe", approved_hash=files_hash(folder))
    ctx.close()

    ctx = open_context(data_paths)
    ctx.plugins.start()
    plugin = ctx.plugins.get("probe")
    assert not plugin["enabled"] and plugin["error"].startswith("Выключено само") and "сломалось" in plugin["error"]
    assert any(f["id"] == "plugin:probe" for f in doctor.quick(ctx))
    ctx.close()


# -- doctor -----------------------------------------------------------------------------------------

def test_doctor_finds_lost_pictures_and_old_copies_and_fixes_with_a_copy(ctx, monkeypatch):
    ctx.settings.set("probe", "see /assets/gone.png here")
    findings = {f["id"]: f for f in doctor.full(ctx)}
    assert "gone.png" in findings["assets"]["detail"]
    assert findings["copy_age"]["fix"]
    assert doctor.fix(ctx, ["copy_age"])["fixed"] == ["copy_age"] and names(ctx)

    later = datetime.now(timezone.utc) + timedelta(days=3)
    monkeypatch.setattr(doctor, "datetime", type("Clock", (), {"now": staticmethod(lambda tz=None: later)}))
    assert "дн." in {f["id"]: f for f in doctor.full(ctx)}["copy_age"]["title"]


async def bubbles_without_keywords(client):
    """Ann with a green «Школа» holding one bare green sub-bubble; the rest must not be found."""
    ann = (await client.post("/api/characters", json={"name": "Ann"})).json()
    conn = (await client.post("/api/connections", json={"name": "fake", "base_url": "http://llm/v1",
                                                        "model": "m"})).json()
    await client.put("/api/settings", json={"active_connection": conn["id"]})

    async def add(**fields):
        return (await client.post(f"/api/characters/{ann['id']}/blocks", json=fields)).json()

    school = await add(title="Школа", content="Академия.", keywords=["академия"])
    bare = await add(title="Описание", content="Ночью колдовать нельзя.", parent_id=school["id"])
    await add(title="Устав", content="Клятва.", parent_id=school["id"], mode="always")
    secret = await add(title="Тайна", content="x", parent_id=school["id"], mode="off")
    await add(title="Под тайной", content="y", parent_id=secret["id"])  # off above: never goes anyway
    await add(title="Сверху", content="z")  # not nested: its parent rule did not change
    return ann, conn, bare


async def test_doctor_gives_bare_sub_bubbles_keywords_and_a_clear_title(client, ctx, fake_llm):
    ann, conn, bare = await bubbles_without_keywords(client)
    findings = {f["id"]: f for f in (await client.get("/api/doctor")).json()["findings"]}
    layers = findings[f"layers:{ann['id']}"]
    assert layers["fix"] and layers["title"].endswith(": 1") and "«Описание»" in layers["detail"]

    await client.put("/api/settings", json={"embedding_connection": conn["id"]})
    fake_llm.queue('{"title": "Ночной запрет", "keywords": ["ночью", "колдовать", "запрет", "Ночной запрет"]}')
    done = (await client.post("/api/doctor/fix", json={"ids": [layers["id"]]})).json()
    assert done["fixed"] == [layers["id"]] and names(ctx)  # a copy was taken first
    assert done["notes"] == ["«Ann»: ключевые слова получили 1, новых названий 1, векторов 4."]  # every green one lacking a vector
    block = ctx.blocks.get(bare["id"])
    assert block["mode"] == "search"  # never switched to «Всегда»
    assert block["title"] == "Ночной запрет" and block["keywords"] == ["ночью", "колдовать", "запрет"]
    assert "Описание" in fake_llm.requests[-1]["messages"][-1]["content"]
    assert not any(f["id"].startswith("layers:") for f in done["findings"])
    assert ctx.history.last_step(ann["id"])["what"] == "«Описание»: изменён"  # one undo step


async def test_doctor_without_meaning_search_says_vectors_wait(client, ctx, fake_llm):
    ann, _, bare = await bubbles_without_keywords(client)
    fake_llm.queue('{"title": "Описание", "keywords": ["ночь"]}')
    done = (await client.post("/api/doctor/fix", json={"ids": [f"layers:{ann['id']}"]})).json()
    assert "векторы не сделаны" in done["notes"][0] and "названий" not in done["notes"][0]
    assert ctx.blocks.get(bare["id"])["title"] == "Описание"

    # the command line leaves them for the page
    assert doctor.fix(ctx, [f"layers:{ann['id']}"])["later"] == []  # already fixed
