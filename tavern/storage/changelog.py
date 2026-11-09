"""Which rows a piece of work changed, so it can be undone (the assistant's actions, stage 8б-2).

    with Recorder(db) as rec:
        ...  # anything: an API call, a service
    undo = rec.snapshot()           # storage/snapshot.py format; snapshot.restore(db, undo) undoes it

While open, TEMP triggers on every watched table write each inserted row (NEW) and each row before
an update or delete (OLD) into a temp journal. For each row the first entry wins: inserted -> undo
deletes it; updated or deleted -> undo puts its first old version back. Everything on this
connection is recorded, so the caller makes sure nothing else writes meanwhile (the assistant
waits while a reply is being written).

Not watched: the assistant's own tables, spending (money was spent, undo does not unspend it),
the prompt archive, cached model info and meaning vectors (made again when needed; tables whose name ends
in `_vectors`, such as a plugin's `p_<id>_vectors`).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from tavern.storage import snapshot
from tavern.storage.db import Database
from tavern.storage.snapshot import _generated, _key_columns, _quote

SKIP_PREFIXES = ("sqlite_", "assistant_", "prompt_")
SKIP_TABLES = frozenset({"spending", "model_info", "block_embeddings"})
SKIP_SUFFIXES = (snapshot.VECTOR_SUFFIX,)  # a plugin's vector table (p_<id>_vectors): made again when needed, never journaled
OPS = (("i", "INSERT", "NEW"), ("u", "UPDATE", "OLD"), ("d", "DELETE", "OLD"))


def _tables(db: Database) -> list[str]:
    rows = db.fetchall("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
    return sorted(r["name"] for r in rows
                  if not r["name"].startswith(SKIP_PREFIXES) and r["name"] not in SKIP_TABLES
                  and not r["name"].endswith(SKIP_SUFFIXES)
                  and not (r["sql"] or "").upper().startswith("CREATE VIRTUAL"))


def _decode(value: Any) -> Any:
    """Blobs come out of the trigger as {"$h": hex} (or that JSON as text); snapshot keeps {"$b": base64}."""
    if isinstance(value, str) and value.startswith('{"$h":'):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, dict) and "$h" in value:
        return {"$b": base64.b64encode(bytes.fromhex(value["$h"])).decode("ascii")}
    return value


class Recorder:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.entries: list[tuple[str, str, dict[str, Any]]] = []
        self._triggers: list[str] = []
        self._keys: dict[str, list[str]] = {}

    def __enter__(self) -> Recorder:
        conn = self.db.connection
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS change_journal "
                        "(seq INTEGER PRIMARY KEY, tbl TEXT NOT NULL, op TEXT NOT NULL, row TEXT NOT NULL)")
        self.db.execute("DELETE FROM temp.change_journal")
        try:
            for table in _tables(self.db):
                keys = _key_columns(conn, table)
                self._keys[table] = keys
                skip = _generated(conn, table)
                columns = [r["name"] for r in conn.execute(f"PRAGMA table_info({_quote(table)})")
                           if r["name"] not in skip]
                for op, event, side in OPS:
                    pairs = [f"'{c}', CASE WHEN typeof({side}.{_quote(c)}) = 'blob' "
                             f"THEN json_object('$h', hex({side}.{_quote(c)})) ELSE {side}.{_quote(c)} END"
                             for c in columns]
                    if keys == ["rowid"]:
                        pairs.append(f"'rowid', {side}.rowid")
                    name = f"chg_{op}_{table}"
                    self.db.execute(
                        f"CREATE TEMP TRIGGER IF NOT EXISTS {_quote(name)} AFTER {event} ON main.{_quote(table)} "
                        f"BEGIN INSERT INTO change_journal (tbl, op, row) VALUES "
                        f"('{table}', '{op}', json_object({', '.join(pairs)})); END")
                    self._triggers.append(name)
        except BaseException:
            self._drop()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            rows = self.db.fetchall("SELECT tbl, op, row FROM temp.change_journal ORDER BY seq")
            self.entries = [(r["tbl"], r["op"], json.loads(r["row"])) for r in rows]
        finally:
            self._drop()

    def _drop(self) -> None:
        for name in self._triggers:
            self.db.execute(f"DROP TRIGGER IF EXISTS temp.{_quote(name)}")
        self._triggers = []
        self.db.execute("DELETE FROM temp.change_journal")

    @property
    def changed(self) -> bool:
        return bool(self.entries)

    def tables(self) -> set[str]:
        return {table for table, _, _ in self.entries}

    def snapshot(self) -> snapshot.Snapshot:
        snap = snapshot.empty()
        seen: set[tuple[str, tuple[Any, ...]]] = set()
        for table, op, row in self.entries:
            keys = self._keys[table]
            key = {k: row[k] for k in keys}
            marker = (table, tuple(key.items()))
            if marker in seen:
                continue
            seen.add(marker)
            if op == "i":
                snapshot.created(snap, table, key)
            else:
                stored = {k: _decode(v) for k, v in row.items() if k != "rowid" or keys == ["rowid"]}
                snap["rows"].append({"table": table, "key": key, "row": stored})
        return snap
