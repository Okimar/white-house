"""Row snapshots (storage/snapshot.py): what goes with a row on delete, and putting it back."""

from __future__ import annotations

import json

from tavern.storage import snapshot
from tavern.storage.db import Database

SCHEMA = """
CREATE TABLE owners (id TEXT PRIMARY KEY, name TEXT NOT NULL, pic BLOB) STRICT;
CREATE TABLE items (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE,
    parent TEXT REFERENCES items(id) ON DELETE CASCADE,
    up TEXT GENERATED ALWAYS AS (upper(id)) VIRTUAL
) STRICT;
CREATE TABLE notes (id INTEGER PRIMARY KEY, owner TEXT REFERENCES owners(id) ON DELETE SET NULL, text TEXT) STRICT;
CREATE TABLE pairs (a TEXT NOT NULL REFERENCES owners(id) ON DELETE CASCADE, b TEXT NOT NULL,
                    PRIMARY KEY (a, b)) STRICT;
"""


def make_db(tmp_path) -> Database:
    db = Database(tmp_path / "t.sqlite")
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
    db.execute("INSERT INTO owners VALUES ('o1', 'Ann', x'00ff')")
    # The child sorts before its parent ('c' < 'p'): restore must not mind the order.
    db.execute("INSERT INTO items (id, owner, parent) VALUES ('p', 'o1', NULL)")
    db.execute("INSERT INTO items (id, owner, parent) VALUES ('c', 'o1', 'p')")
    db.execute("INSERT INTO notes (id, owner, text) VALUES (1, 'o1', 'старый текст')")
    db.execute("INSERT INTO pairs VALUES ('o1', 'x')")
    return db


def state(db: Database) -> dict:
    return {table: [dict(r) for r in db.fetchall(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("owners", "items", "notes", "pairs")}


def test_delete_and_restore_brings_back_everything_that_went_with_it(tmp_path):
    db = make_db(tmp_path)
    before = state(db)
    snap = snapshot.capture(db, "owners", {"id": "o1"}, cascade=True)
    snap = json.loads(json.dumps(snap))  # it is stored as JSON
    db.execute("DELETE FROM owners WHERE id = 'o1'")
    assert db.fetchone("SELECT owner FROM notes")["owner"] is None
    db.execute("UPDATE notes SET text = 'новый текст'")  # edited after the delete
    snapshot.restore(db, snap)
    after = state(db)
    assert after["owners"] == before["owners"] and after["items"] == before["items"]
    assert after["pairs"] == before["pairs"]
    # A row that only pointed here gets its link back, not its old text.
    assert after["notes"] == [{"id": 1, "owner": "o1", "text": "новый текст"}]


def test_update_restore_and_created_rows(tmp_path):
    db = make_db(tmp_path)
    snap = snapshot.capture(db, "owners", {"id": "o1"})
    assert len(snap["rows"]) == 1  # no cascade: only the row itself
    db.execute("UPDATE owners SET name = 'Bob'")
    db.execute("INSERT INTO owners VALUES ('o2', 'New', NULL)")
    db.execute("INSERT INTO items (id, owner) VALUES ('n', 'o2')")
    snapshot.created(snap, "owners", {"id": "o2"})
    snapshot.restore(db, snap)
    assert [dict(r) for r in db.fetchall("SELECT id, name FROM owners")] == [{"id": "o1", "name": "Ann"}]
    assert db.fetchone("SELECT 1 FROM items WHERE id = 'n'") is None  # went with the created row


def test_a_relink_is_skipped_when_the_row_is_gone(tmp_path):
    db = make_db(tmp_path)
    snap = snapshot.capture(db, "owners", {"id": "o1"}, cascade=True)
    db.execute("DELETE FROM owners WHERE id = 'o1'")
    db.execute("DELETE FROM notes")
    snapshot.restore(db, snap)
    assert db.fetchall("SELECT * FROM notes") == []
    assert db.fetchone("SELECT name FROM owners")["name"] == "Ann"


def test_recorder_catches_any_change_and_undoes_it(tmp_path):
    from tavern.storage.changelog import Recorder

    db = make_db(tmp_path)
    db.execute("CREATE TABLE plain (n INTEGER) STRICT")  # no declared key: rows are told apart by rowid
    db.execute("INSERT INTO plain VALUES (1)")
    before = state(db)
    with Recorder(db) as rec:
        db.execute("UPDATE owners SET name = 'Bob', pic = x'0102'")
        db.execute("UPDATE owners SET name = 'Carl'")  # the first old version is what comes back
        db.execute("DELETE FROM items WHERE id = 'c'")
        db.execute("INSERT INTO owners VALUES ('o2', 'New', NULL)")
        db.execute("INSERT INTO items (id, owner) VALUES ('n', 'o2')")
        db.execute("UPDATE plain SET n = 5")
    assert rec.changed and rec.tables() == {"owners", "items", "plain"}
    db.execute("INSERT INTO notes (text) VALUES ('после')")  # not recorded: the recorder is closed
    snap = json.loads(json.dumps(rec.snapshot()))
    snapshot.restore(db, snap)
    after = state(db)
    assert after["owners"] == before["owners"] and after["items"] == before["items"]
    assert db.fetchone("SELECT n FROM plain")["n"] == 1
    assert len(after["notes"]) == 2
    assert db.fetchall("SELECT name FROM sqlite_temp_master WHERE type = 'trigger'") == []
