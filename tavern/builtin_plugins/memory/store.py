"""SQL over the memory plugin's own tables (migrations/0001_memory.sql). Nothing else touches them.

`db` is the app's `Database` (`api.ctx.db`). Vectors are stored normalized as `array('f')` bytes (a dot product is
then the cosine) next to the hash of the text they were made from and the model's name: a vector counts only when
both still match its piece. `VectorCache` keeps a chat's vectors in memory between replies, so scoring does not
read thousands of BLOBs every time (ADR 0019 risk R1).
"""

from __future__ import annotations

import json
from array import array
from typing import Any

from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

from .pieces import Spec

PIECES, VECTORS, CHATS = "p_memory_pieces", "p_memory_vectors", "p_memory_chats"


# -- per-chat switch -------------------------------------------------------------------------------


def chat_row(db: Database, chat_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT chat_id, enabled, settings FROM {CHATS} WHERE chat_id = ?", (chat_id,))
    return None if row is None else {"chat_id": row["chat_id"], "enabled": bool(row["enabled"]),
                                     "settings": json.loads(row["settings"])}


def is_enabled(db: Database, chat_id: str) -> bool:
    row = db.fetchone(f"SELECT enabled FROM {CHATS} WHERE chat_id = ?", (chat_id,))
    return bool(row and row["enabled"])


def set_enabled(db: Database, chat_id: str, enabled: bool) -> None:
    db.execute(
        f"INSERT INTO {CHATS} (chat_id, enabled, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET enabled = excluded.enabled, updated_at = excluded.updated_at",
        (chat_id, int(enabled), now_iso()))


# -- pieces ----------------------------------------------------------------------------------------


def pieces(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every piece row of the chat (all branches): id, message id, part, hash, tokens, keywords."""
    rows = db.fetchall(
        f"SELECT id, first_id, part, text_hash, tokens, keywords FROM {PIECES} WHERE chat_id = ?", (chat_id,))
    return [{"id": r["id"], "message_id": r["first_id"], "part": r["part"], "hash": r["text_hash"],
             "tokens": r["tokens"], "keywords": json.loads(r["keywords"])} for r in rows]


def sync(db: Database, chat_id: str, wanted: list[Spec], path_ids: set[str]) -> dict[str, int]:
    """Make the pieces of the messages on `path_ids` equal `wanted`. Pieces of messages on other branches stay.
    Returns {added, changed, removed}. A changed piece keeps its id; its old vector goes stale by hash."""
    have = {(p["message_id"], p["part"]): p for p in pieces(db, chat_id) if p["message_id"] in path_ids}
    want = {(s.message_id, s.part): s for s in wanted}
    added = changed = removed = 0
    now = now_iso()
    with db.transaction() as conn:
        for key, piece in have.items():
            if key not in want:
                conn.execute(f"DELETE FROM {PIECES} WHERE id = ?", (piece["id"],))
                removed += 1
        for key, spec in want.items():
            piece = have.get(key)
            keywords = json.dumps(list(spec.keywords), ensure_ascii=False)
            if piece is None:
                conn.execute(
                    f"INSERT INTO {PIECES} (id, chat_id, first_id, last_id, part, text_hash, tokens, keywords, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), chat_id, spec.message_id, spec.message_id, spec.part, spec.hash, spec.tokens,
                     keywords, now))
                added += 1
            elif piece["hash"] != spec.hash:
                conn.execute(f"UPDATE {PIECES} SET text_hash = ?, tokens = ?, keywords = ? WHERE id = ?",
                             (spec.hash, spec.tokens, keywords, piece["id"]))
                changed += 1
    return {"added": added, "changed": changed, "removed": removed}


# -- vectors ---------------------------------------------------------------------------------------


def normalized(vector: list[float]) -> array:
    norm = sum(x * x for x in vector) ** 0.5
    return array("f", [x / norm for x in vector] if norm else vector)


def current_vector_ids(db: Database, chat_id: str, model: str) -> dict[str, str]:
    """item id -> text hash of the stored vector, for one model (no BLOBs read)."""
    rows = db.fetchall(f"SELECT item_id, text_hash FROM {VECTORS} WHERE chat_id = ? AND model = ?",
                       (chat_id, model))
    return {r["item_id"]: r["text_hash"] for r in rows}


def save_vectors(db: Database, chat_id: str, model: str, items: list[tuple[str, str, list[float]]]) -> None:
    """items: (piece id, text hash, vector). One transaction: a batch is kept whole or not at all."""
    with db.transaction() as conn:
        for item_id, digest, vector in items:
            conn.execute(
                f"INSERT INTO {VECTORS} (item_id, chat_id, model, text_hash, vector) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, model) DO UPDATE SET text_hash = excluded.text_hash, "
                "vector = excluded.vector, chat_id = excluded.chat_id",
                (item_id, chat_id, model, digest, normalized(vector).tobytes()))


def vector_count(db: Database, chat_id: str, model: str) -> int:
    row = db.fetchone(f"SELECT COUNT(*) AS n FROM {VECTORS} WHERE chat_id = ? AND model = ?", (chat_id, model))
    return int(row["n"])


class VectorCache:
    """A chat's vectors in memory: item id -> (text hash, array('f')). Reloaded when the table changed
    (count and the newest rowid differ)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[tuple[int, int], dict[str, tuple[str, array]]]] = {}

    def get(self, db: Database, chat_id: str, model: str) -> dict[str, tuple[str, array]]:
        stamp = db.fetchone(f"SELECT COUNT(*) AS n, COALESCE(MAX(rowid), 0) AS top FROM {VECTORS} "
                            "WHERE chat_id = ? AND model = ?", (chat_id, model))
        key = (int(stamp["n"]), int(stamp["top"]))
        cached = self._data.get((chat_id, model))
        if cached is not None and cached[0] == key:
            return cached[1]
        loaded: dict[str, tuple[str, array]] = {}
        for row in db.fetchall(f"SELECT item_id, text_hash, vector FROM {VECTORS} WHERE chat_id = ? AND model = ?",
                               (chat_id, model)):
            vector = array("f")
            vector.frombytes(row["vector"])
            loaded[row["item_id"]] = (row["text_hash"], vector)
        self._data[(chat_id, model)] = (key, loaded)
        return loaded

    def clear(self) -> None:
        self._data.clear()


# -- chapters (migrations/0002_chapters.sql) -------------------------------------------------------

CHAPTERS = "p_memory_chapters"
CHAPTER_FIELDS = ("id", "chat_id", "first_id", "last_id", "title", "state", "content_hash", "participants", "whole",
                  "border_by", "reason", "tries", "created_at", "updated_at", "error", "failed_at")


def _chapter(row: Any) -> dict[str, Any]:
    out = {key: row[key] for key in CHAPTER_FIELDS}
    out["participants"] = json.loads(out["participants"])
    out["whole"] = bool(out["whole"])
    return out


def chapter_rows(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every chapter row of the chat, all branches (which of them belong to the open branch: chapters.placed)."""
    rows = db.fetchall(f"SELECT {', '.join(CHAPTER_FIELDS)} FROM {CHAPTERS} WHERE chat_id = ? "
                       "ORDER BY created_at, id", (chat_id,))
    return [_chapter(r) for r in rows]


def get_chapter(db: Database, chapter_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {', '.join(CHAPTER_FIELDS)} FROM {CHAPTERS} WHERE id = ?", (chapter_id,))
    return None if row is None else _chapter(row)


def put_chapters(db: Database, rows: list[dict[str, Any]]) -> None:
    """Insert or update whole chapter rows (a new chapter, an edit, an undo): one transaction. An upsert, not
    REPLACE: replacing would delete the row and cascade away its sticky hits."""
    sets = ", ".join(f"{k} = excluded.{k}" for k in CHAPTER_FIELDS if k != "id")
    with db.transaction() as conn:
        for row in rows:
            values = [json.dumps(row[k], ensure_ascii=False) if k == "participants" else
                      (int(row[k]) if k == "whole" else row.get(k, "" if k in ("error", "failed_at") else None))
                      for k in CHAPTER_FIELDS]
            conn.execute(
                f"INSERT INTO {CHAPTERS} ({', '.join(CHAPTER_FIELDS)}) VALUES ({', '.join('?' * len(CHAPTER_FIELDS))}) "
                f"ON CONFLICT(id) DO UPDATE SET {sets}", values)


def delete_chapters(db: Database, ids: list[str]) -> None:
    with db.transaction() as conn:
        for chapter_id in ids:
            conn.execute(f"DELETE FROM {CHAPTERS} WHERE id = ?", (chapter_id,))


def new_chapter(chat_id: str, first_id: str, last_id: str, content_hash: str, participants: list[str], *,
                by: str = "script", reason: str = "", title: str = "") -> dict[str, Any]:
    now = now_iso()
    return {"id": new_id(), "chat_id": chat_id, "first_id": first_id, "last_id": last_id, "title": title,
            "state": "raw", "content_hash": content_hash, "participants": participants, "whole": False,
            "border_by": by, "reason": reason, "tries": 0, "created_at": now, "updated_at": now,
            "error": "", "failed_at": ""}


def existing_messages(db: Database, ids: list[str]) -> set[str]:
    """Which of these message ids are still in the database."""
    if not ids:
        return set()
    marks = ", ".join("?" * len(ids))
    return {r["id"] for r in db.fetchall(f"SELECT id FROM messages WHERE id IN ({marks})", tuple(ids))}


# -- summaries and the plot (migrations/0003_summaries.sql) -----------------------------------------

TEXTS, HITS, MEMBERS = "p_memory_texts", "p_memory_hits", "p_memory_members"
TEXT_FIELDS = "id, kind, target, anchor_id, made_from, text, made_by, locked, is_current, created_at"
KEEP_VERSIONS = 20  # older versions of one summary (or one chat's plot) are dropped past this


def _text(row: Any) -> dict[str, Any]:
    return {"id": row["id"], "kind": row["kind"], "target": row["target"], "anchor_id": row["anchor_id"],
            "made_from": row["made_from"], "text": row["text"], "by": row["made_by"], "locked": bool(row["locked"]),
            "current": bool(row["is_current"]), "created_at": row["created_at"]}


def current_summaries(db: Database, chat_id: str) -> dict[str, dict[str, Any]]:
    """chapter id -> its current summary row (text, made_from hash, locked, by)."""
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE chat_id = ? AND kind = 'summary' AND is_current = 1",
                       (chat_id,))
    return {r["target"]: _text(r) for r in rows}


def summary_versions(db: Database, chapter_id: str) -> list[dict[str, Any]]:
    """Every version of a chapter's summary, newest first."""
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE kind = 'summary' AND target = ? "
                       "ORDER BY created_at DESC, rowid DESC", (chapter_id,))
    return [_text(r) for r in rows]


def get_text(db: Database, text_id: str) -> dict[str, Any] | None:
    row = db.fetchone(f"SELECT {TEXT_FIELDS}, chat_id FROM {TEXTS} WHERE id = ?", (text_id,))
    return None if row is None else {**_text(row), "chat_id": row["chat_id"]}


def put_summary(db: Database, chat_id: str, chapter_id: str, text: str, made_from: str, *, by: str,
                locked: bool = False, title: str | None = None) -> str:
    """A new current version of a chapter's summary (the old one stays as a version). Clears the chapter's failure
    mark; `title` (the model's) is set only when the chapter has none yet."""
    text_id = new_id()
    with db.transaction() as conn:
        conn.execute(f"UPDATE {TEXTS} SET is_current = 0 WHERE kind = 'summary' AND target = ?", (chapter_id,))
        conn.execute(f"INSERT INTO {TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, locked, "
                     "is_current, created_at) VALUES (?, ?, 'summary', ?, NULL, ?, ?, ?, ?, 1, ?)",
                     (text_id, chat_id, chapter_id, made_from, text, by, int(locked), now_iso()))
        conn.execute(f"UPDATE {CHAPTERS} SET tries = 0, error = '', failed_at = '' WHERE id = ?", (chapter_id,))
        if title:
            conn.execute(f"UPDATE {CHAPTERS} SET title = ? WHERE id = ? AND title = ''", (title[:120], chapter_id))
        conn.execute(f"DELETE FROM {TEXTS} WHERE kind = 'summary' AND target = ? AND is_current = 0 AND id NOT IN "
                     f"(SELECT id FROM {TEXTS} WHERE kind = 'summary' AND target = ? "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS})", (chapter_id, chapter_id))
    return text_id


def restore_summary(db: Database, text_id: str) -> dict[str, Any]:
    """«Вернуть эту версию»: the version becomes the current one (it keeps its own lock flag)."""
    row = get_text(db, text_id)
    if row is None or row["kind"] != "summary":
        raise KeyError(text_id)
    with db.transaction() as conn:
        conn.execute(f"UPDATE {TEXTS} SET is_current = 0 WHERE kind = 'summary' AND target = ?", (row["target"],))
        conn.execute(f"UPDATE {TEXTS} SET is_current = 1 WHERE id = ?", (text_id,))
    return row


def set_summary_lock(db: Database, chapter_id: str, locked: bool) -> None:
    db.execute(f"UPDATE {TEXTS} SET locked = ? WHERE kind = 'summary' AND target = ? AND is_current = 1",
               (int(locked), chapter_id))


def restamp_summary(db: Database, chapter_id: str, made_from: str) -> None:
    """«Принять изменения»: the current summary now stands for the chapter's text as it is."""
    db.execute(f"UPDATE {TEXTS} SET made_from = ? WHERE kind = 'summary' AND target = ? AND is_current = 1",
               (made_from, chapter_id))


def delete_text(db: Database, text_id: str) -> None:
    db.execute(f"DELETE FROM {TEXTS} WHERE id = ?", (text_id,))


def plot_versions(db: Database, chat_id: str, path_ids: set[str]) -> list[dict[str, Any]]:
    """The plot versions of a branch (anchored on it), newest first: the first one is the branch's plot."""
    return [r for r in plot_rows(db, chat_id) if r["anchor_id"] in path_ids]


def drop_texts_of(db: Database, chapter_ids: list[str]) -> None:
    with db.transaction() as conn:
        for chapter_id in chapter_ids:
            conn.execute(f"DELETE FROM {TEXTS} WHERE kind = 'summary' AND target = ?", (chapter_id,))


def mark_failed(db: Database, chapter_id: str, error: str) -> None:
    db.execute(f"UPDATE {CHAPTERS} SET tries = tries + 1, error = ?, failed_at = ?, state = 'failed' WHERE id = ?",
               ((error or "ошибка")[:300], now_iso(), chapter_id))


def plot_rows(db: Database, chat_id: str) -> list[dict[str, Any]]:
    """Every version of the chat's plot (all branches), newest first."""
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE chat_id = ? AND kind = 'plot' "
                       "ORDER BY created_at DESC, rowid DESC", (chat_id,))
    return [_text(r) for r in rows]


def plot_for_path(db: Database, chat_id: str, path_ids: set[str]) -> dict[str, Any] | None:
    """The plot of a branch: the newest version anchored at a message of the branch."""
    return next((r for r in plot_rows(db, chat_id) if r["anchor_id"] in path_ids), None)


def put_plot(db: Database, chat_id: str, text: str, anchor_id: str, made_from: str, *, by: str,
             locked: bool = False) -> str:
    text_id = new_id()
    with db.transaction() as conn:
        conn.execute(f"INSERT INTO {TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, locked, "
                     "is_current, created_at) VALUES (?, ?, 'plot', '', ?, ?, ?, ?, ?, 1, ?)",
                     (text_id, chat_id, anchor_id, made_from, text, by, int(locked), now_iso()))
        conn.execute(f"DELETE FROM {TEXTS} WHERE chat_id = ? AND kind = 'plot' AND id NOT IN "
                     f"(SELECT id FROM {TEXTS} WHERE chat_id = ? AND kind = 'plot' "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS * 2})", (chat_id, chat_id))
    return text_id


def set_plot_lock(db: Database, text_id: str, locked: bool) -> None:
    db.execute(f"UPDATE {TEXTS} SET locked = ? WHERE id = ? AND kind = 'plot'", (int(locked), text_id))


# -- sticky hits -----------------------------------------------------------------------------------


def hits(db: Database, chat_id: str) -> dict[str, tuple[int, str]]:
    rows = db.fetchall(f"SELECT chapter_id, hit_gen, what FROM {HITS} WHERE chat_id = ?", (chat_id,))
    return {r["chapter_id"]: (int(r["hit_gen"]), r["what"]) for r in rows}


def put_hits(db: Database, chat_id: str, items: list[tuple[str, int, str]]) -> None:
    with db.transaction() as conn:
        for chapter_id, gen, what in items:
            conn.execute(f"INSERT INTO {HITS} (chat_id, chapter_id, hit_gen, what) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(chat_id, chapter_id) DO UPDATE SET hit_gen = excluded.hit_gen, "
                         "what = excluded.what", (chat_id, chapter_id, gen, what))


def next_generation(db: Database, chat_id: str) -> int:
    """The memory's own reply counter of the chat (kept in the chat row's settings): one more reply."""
    row = chat_row(db, chat_id)
    settings = dict(row["settings"]) if row else {}
    settings["gen"] = int(settings.get("gen", 0)) + 1
    db.execute(f"UPDATE {CHATS} SET settings = ? WHERE chat_id = ?", (json.dumps(settings), chat_id))
    return int(settings["gen"])


# -- members ---------------------------------------------------------------------------------------


def member_flags(db: Database, chat_id: str) -> dict[str, dict[str, bool]]:
    rows = db.fetchall(f"SELECT character_id, remembers_all, no_layer FROM {MEMBERS} WHERE chat_id = ?", (chat_id,))
    return {r["character_id"]: {"remembers_all": bool(r["remembers_all"]), "no_layer": bool(r["no_layer"])}
            for r in rows}


def set_member(db: Database, chat_id: str, character_id: str, *, remembers_all: bool | None = None,
               no_layer: bool | None = None) -> None:
    now = member_flags(db, chat_id).get(character_id, {"remembers_all": False, "no_layer": False})
    flags = {"remembers_all": now["remembers_all"] if remembers_all is None else remembers_all,
             "no_layer": now["no_layer"] if no_layer is None else no_layer}
    db.execute(f"INSERT INTO {MEMBERS} (chat_id, character_id, remembers_all, no_layer) VALUES (?, ?, ?, ?) "
               "ON CONFLICT(chat_id, character_id) DO UPDATE SET remembers_all = excluded.remembers_all, "
               "no_layer = excluded.no_layer",
               (chat_id, character_id, int(flags["remembers_all"]), int(flags["no_layer"])))


# -- «Развитие» layers and «Сейчас:» lines (migrations/0004_layers.sql) ------------------------------

LAYER_RUNS = "p_memory_layer_runs"
ANCHORED = ("development", "now_line")


def anchored_rows(db: Database, chat_id: str, kind: str, target: str | None = None) -> list[dict[str, Any]]:
    """Every version of a kind (all branches), newest first; one target only when given."""
    where, args = "chat_id = ? AND kind = ?", [chat_id, kind]
    if target is not None:
        where += " AND target = ?"
        args.append(target)
    rows = db.fetchall(f"SELECT {TEXT_FIELDS} FROM {TEXTS} WHERE {where} ORDER BY created_at DESC, rowid DESC",
                       tuple(args))
    return [_text(r) for r in rows]


def anchored_for_path(db: Database, chat_id: str, kind: str, path_ids: set[str]) -> dict[str, dict[str, Any]]:
    """target -> the branch's version: the newest one anchored at a message of the branch (ADR 3.1)."""
    out: dict[str, dict[str, Any]] = {}
    for row in anchored_rows(db, chat_id, kind):
        if row["anchor_id"] in path_ids and row["target"] not in out:
            out[row["target"]] = row
    return out


def anchored_versions(db: Database, chat_id: str, kind: str, target: str, path_ids: set[str]) -> list[dict[str, Any]]:
    """The versions of one target on a branch, newest first: the first one is the branch's."""
    return [r for r in anchored_rows(db, chat_id, kind, target) if r["anchor_id"] in path_ids]


def put_anchored(db: Database, chat_id: str, kind: str, target: str, text: str, anchor_id: str, *, by: str,
                 locked: bool = False, made_from: str = "") -> str:
    """A new version of a layer or a line, anchored at `anchor_id`. Older versions of the target past
    2 x KEEP_VERSIONS (all branches together) are dropped."""
    if kind not in ANCHORED:
        raise ValueError(kind)
    text_id = new_id()
    with db.transaction() as conn:
        conn.execute(f"INSERT INTO {TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, locked, "
                     "is_current, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                     (text_id, chat_id, kind, target, anchor_id, made_from, text, by, int(locked), now_iso()))
        conn.execute(f"DELETE FROM {TEXTS} WHERE chat_id = ? AND kind = ? AND target = ? AND id NOT IN "
                     f"(SELECT id FROM {TEXTS} WHERE chat_id = ? AND kind = ? AND target = ? "
                     f"ORDER BY created_at DESC, rowid DESC LIMIT {KEEP_VERSIONS * 2})",
                     (chat_id, kind, target, chat_id, kind, target))
    return text_id


def set_text_lock(db: Database, text_id: str, locked: bool) -> None:
    db.execute(f"UPDATE {TEXTS} SET locked = ? WHERE id = ?", (int(locked), text_id))


def layer_runs(db: Database, chat_id: str) -> dict[str, dict[str, Any]]:
    """chapter id -> {state, error, tries, failed_at} of the development step."""
    rows = db.fetchall(f"SELECT chapter_id, state, error, tries, updated_at FROM {LAYER_RUNS} WHERE chat_id = ?",
                       (chat_id,))
    return {r["chapter_id"]: {"state": r["state"], "error": r["error"], "tries": int(r["tries"]),
                              "failed_at": r["updated_at"] if r["state"] == "failed" else ""} for r in rows}


def put_layer_run(db: Database, chat_id: str, chapter_id: str, state: str, error: str = "") -> None:
    """The development step of a chapter is done, or failed once more (tries + 1)."""
    db.execute(
        f"INSERT INTO {LAYER_RUNS} (chapter_id, chat_id, state, error, tries, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(chapter_id) DO UPDATE SET state = excluded.state, error = excluded.error, "
        f"tries = CASE WHEN excluded.state = 'failed' THEN {LAYER_RUNS}.tries + 1 ELSE 0 END, "
        "updated_at = excluded.updated_at",
        (chapter_id, chat_id, state, (error or "")[:300], 1 if state == "failed" else 0, now_iso()))
