"""The "what the model saw" archive: pieces stored once, small records, exact old versions, cleanup."""

from __future__ import annotations

from tavern.storage.db import Database
from tavern.storage.prompt_log import MAX_CHAIN, PromptLog


def setup(tmp_path):
    db = Database(tmp_path / "t.sqlite3")
    db.migrate()
    db.execute("INSERT INTO chats (id, title) VALUES ('c', 'chat')")
    return db, PromptLog(db)


def prompt(card: str, n: int) -> list[dict]:
    entries = [{"role": "system", "text": card, "source": "character:ann:card", "label": "Ann", "msg": 0}]
    entries += [{"role": "user", "text": f"message {i}", "source": f"message:{i}", "label": f"#{i}", "msg": i + 1}
                for i in range(n)]
    return entries


def test_each_text_is_stored_once_and_records_only_add_the_difference(tmp_path):
    db, log = setup(tmp_path)
    for n in range(1, 21):
        log.record("c", f"reply{n}", 0, prompt("Ann, 20", n))
    assert db.fetchone("SELECT COUNT(*) FROM prompt_pieces")[0] == 21  # card + 20 messages
    biggest = max(len(r["entries"]) for r in db.fetchall("SELECT entries FROM prompt_records"))
    assert biggest < 200  # deltas, not whole prompts
    record = log.get(log.find("reply20", 0))
    assert [e["text"] for e in record["entries"]] == ["Ann, 20", *[f"message {i}" for i in range(20)]]


def test_old_records_keep_the_old_version_after_an_edit(tmp_path):
    _, log = setup(tmp_path)
    log.record("c", "r1", 0, prompt("Ann, 20", 1))
    log.record("c", "r2", 0, prompt("Ann, 25", 2))
    fresh = PromptLog(log.db)  # no cache: read from the database
    assert fresh.get(fresh.find("r1", 0))["entries"][0]["text"] == "Ann, 20"
    assert fresh.get(fresh.find("r2", 0))["entries"][0]["text"] == "Ann, 25"
    versions = fresh.versions("character:ann:card")
    assert [v["text"] for v in versions] == ["Ann, 20", "Ann, 25"]
    assert fresh.get(fresh.find("r2", 0))["entries"][0]["versions"] == 2


def test_chains_are_cut_so_reading_stays_cheap(tmp_path):
    db, log = setup(tmp_path)
    for n in range(1, MAX_CHAIN + 5):
        log.record("c", f"r{n}", 0, prompt("card", n))
    assert db.fetchone("SELECT MAX(chain) FROM prompt_records")[0] < MAX_CHAIN
    fresh = PromptLog(db)
    assert len(fresh.get(fresh.find(f"r{MAX_CHAIN + 4}", 0))["entries"]) == MAX_CHAIN + 5


def test_regenerated_swipe_shows_its_newest_prompt(tmp_path):
    _, log = setup(tmp_path)
    log.record("c", "r", 0, prompt("first", 1))
    log.record("c", "r", 0, prompt("second", 1))
    assert log.get(log.find("r", 0))["entries"][0]["text"] == "second"


def test_garbage_collection_after_chat_delete(tmp_path):
    db, log = setup(tmp_path)
    log.record("c", "r", 0, prompt("card", 3))
    db.execute("DELETE FROM chats WHERE id = 'c'")
    assert log.collect_garbage() == 4
    assert db.fetchone("SELECT COUNT(*) FROM prompt_piece_sources")[0] == 0
