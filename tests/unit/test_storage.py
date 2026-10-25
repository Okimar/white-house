import sqlite3

import pytest

from tavern.errors import StorageError
from tavern.storage.backup import (
    create_backup,
    list_backups,
    prune_backups,
    resolve_backup,
    restore_backup,
)
from tavern.storage.db import Database, split_sql
from tavern.storage.migrations import Migration, load_migrations


def migrations(*sqls: str) -> list[Migration]:
    return [Migration(i, f"m{i}", sql) for i, sql in enumerate(sqls, start=1)]


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.sqlite3")
    yield database
    database.close()


def test_shipped_migrations_apply_cleanly(db):
    shipped = load_migrations()
    assert db.migrate(shipped) == [m.version for m in shipped]
    assert db.schema_version == shipped[-1].version
    assert db.migrate(shipped) == []
    assert db.fetchone("PRAGMA foreign_keys")[0] == 1
    assert db.fetchone("PRAGMA journal_mode")[0] == "wal"


def test_migration_failure_rolls_back_whole_step(db):
    good = "CREATE TABLE a (x INTEGER);"
    bad = "CREATE TABLE b (x INTEGER);\nINSERT INTO missing VALUES (1);"
    db.migrate(migrations(good))
    with pytest.raises(StorageError, match="0002_m2 failed and was rolled back"):
        db.migrate(migrations(good, bad))
    assert db.schema_version == 1
    tables = {r[0] for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "b" not in tables


def test_before_upgrade_runs_only_for_existing_schema(db):
    calls = []
    db.migrate(migrations("CREATE TABLE a (x);"), before_upgrade=lambda *a: calls.append(a))
    assert calls == []  # fresh database: nothing to protect
    db.migrate(
        migrations("CREATE TABLE a (x);", "CREATE TABLE b (x);"),
        before_upgrade=lambda *a: calls.append(a),
    )
    assert calls == [(1, 2)]


def test_newer_schema_is_refused(db):
    db.execute("PRAGMA user_version = 99")
    with pytest.raises(StorageError, match="newer than this app supports"):
        db.migrate(migrations("CREATE TABLE a (x);"))


def test_load_migrations_requires_contiguous_numbers(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0003_c.sql").write_text("SELECT 1;")
    with pytest.raises(RuntimeError, match="expected 0002"):
        load_migrations(tmp_path)


def test_split_sql_handles_semicolons_in_strings_and_comments():
    script = "-- header; comment\nCREATE TABLE t (x TEXT);\nINSERT INTO t VALUES ('a;b');\n-- tail"
    assert split_sql(script) == ["-- header; comment\nCREATE TABLE t (x TEXT);",
                                 "INSERT INTO t VALUES ('a;b');"]
    with pytest.raises(StorageError, match="Incomplete"):
        split_sql("CREATE TABLE t (x")


def test_transaction_rolls_back_on_error(db):
    db.execute("CREATE TABLE t (x INTEGER)")
    with pytest.raises(ValueError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise ValueError
    assert db.fetchone("SELECT count(*) FROM t")[0] == 0


def test_backup_restore_roundtrip(tmp_path, db):
    backups = tmp_path / "backups"
    db.execute("CREATE TABLE t (x INTEGER)")
    db.execute("INSERT INTO t VALUES (1)")
    snap = create_backup(db.path, backups, "Before Big Change!")
    assert snap.name.endswith("-before-big-change.sqlite3")
    db.execute("INSERT INTO t VALUES (2)")

    safety = restore_backup(db.path, backups, snap.name)
    assert db.fetchone("SELECT count(*) FROM t")[0] == 1
    assert safety is not None and safety.name.endswith("pre-restore.sqlite3")
    check = sqlite3.connect(safety)
    assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 2
    check.close()


def test_prune_keeps_newest(tmp_path, db):
    backups = tmp_path / "backups"
    names = [create_backup(db.path, backups, f"n{i}").name for i in range(4)]
    removed = prune_backups(backups, keep=2)
    assert sorted(removed) == sorted(names[:2])
    assert [b.name for b in list_backups(backups)] == names[:1:-1]


@pytest.mark.parametrize("name", ["../tavern.sqlite3", "x.sqlite3", "tavern-1.sqlite3"])
def test_resolve_backup_rejects_non_snapshot_names(tmp_path, name):
    with pytest.raises(StorageError, match="Not a backup name"):
        resolve_backup(tmp_path, name)


def test_restore_refuses_corrupt_snapshot(tmp_path, db):
    backups = tmp_path / "backups"
    snap = create_backup(db.path, backups)
    snap.write_bytes(b"not a database at all" * 100)
    with pytest.raises(StorageError, match="not a valid database"):
        restore_backup(db.path, backups, snap.name)
