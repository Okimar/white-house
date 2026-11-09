"""Row snapshots for "Отменить" of the assistant's actions (stage 8б).

`capture` keeps a row as it is now, and with `cascade` also every row that would go with it on
delete (children through ON DELETE CASCADE, recursively) and every row that would lose its link
(ON DELETE SET NULL). `restore` puts them back: rows that exist are updated, missing ones inserted,
parents first. `created` rows (made by the action) are deleted first, which takes along anything
made under them later.

Rows removed by triggers rather than foreign keys (a character's edit history) are not kept.
Snapshots are plain JSON: bytes are stored as {"$b": base64}.
"""

from __future__ import annotations

import base64
import sqlite3
from typing import Any

from tavern.storage.db import Database

VECTOR_SUFFIX = "_vectors"
Snapshot = dict[str, list[dict[str, Any]]]  # {"rows": [{table, key, row}], "created": [{table, key}]}


def empty() -> Snapshot:
    return {"rows": [], "created": []}


def _encode(value: Any) -> Any:
    return {"$b": base64.b64encode(value).decode("ascii")} if isinstance(value, bytes) else value


def _decode(value: Any) -> Any:
    return base64.b64decode(value["$b"]) if isinstance(value, dict) and "$b" in value else value


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _key_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    info = conn.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    keys = sorted((r["pk"], r["name"]) for r in info if r["pk"])
    return [name for _, name in keys] or ["rowid"]


def _generated(conn: sqlite3.Connection, table: str) -> set[str]:
    """Computed columns (owner_id of bubbles): read by SELECT *, but never written."""
    return {r["name"] for r in conn.execute(f"PRAGMA table_xinfo({_quote(table)})") if r["hidden"] in (2, 3)}


def _children(conn: sqlite3.Connection) -> dict[str, list[tuple[str, list[str], list[str | None], str]]]:
    """parent table -> [(child table, child columns, parent columns, on_delete)]."""
    found: dict[str, list[tuple[str, list[str], list[str | None], str]]] = {}
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    for table in tables:
        if table.endswith(VECTOR_SUFFIX):
            continue  # a plugin's vectors are never kept for undo: its job makes them again
        groups: dict[int, list[Any]] = {}
        for fk in conn.execute(f"PRAGMA foreign_key_list({_quote(table)})"):
            groups.setdefault(fk["id"], []).append(fk)
        for fks in groups.values():
            fks.sort(key=lambda f: f["seq"])
            action = fks[0]["on_delete"].upper()
            if action in ("CASCADE", "SET NULL"):
                found.setdefault(fks[0]["table"], []).append(
                    (table, [f["from"] for f in fks], [f["to"] for f in fks], action))
    return found


def _select(conn: sqlite3.Connection, table: str, where: dict[str, Any]) -> list[dict[str, Any]]:
    cond = " AND ".join(f"{_quote(k)} IS ?" for k in where)
    rows = conn.execute(f"SELECT rowid AS rowid, * FROM {_quote(table)} WHERE {cond}", tuple(where.values()))
    return [dict(r) for r in rows]


def capture(db: Database, table: str, where: dict[str, Any], *, cascade: bool = False,
            into: Snapshot | None = None) -> Snapshot:
    """Add the rows of `table` matching `where` (and with `cascade` what depends on them) to a snapshot."""
    snap = into if into is not None else empty()
    conn = db.connection
    children = _children(conn) if cascade else {}
    seen = {(item["table"], tuple(item["key"].items())) for item in snap["rows"]}
    # (table, where, follow children, only these columns: a SET NULL link keeps just the link)
    queue: list[tuple[str, dict[str, Any], bool, list[str] | None]] = [(table, where, cascade, None)]
    while queue:
        current, cond, follow, only = queue.pop(0)
        keys = _key_columns(conn, current)
        skip = _generated(conn, current)
        for row in _select(conn, current, cond):
            key = {k: row[k] for k in keys}
            marker = (current, tuple(key.items()))
            if marker in seen:
                continue
            seen.add(marker)
            stored = {k: _encode(v) for k, v in row.items()
                      if k not in skip and (k != "rowid" or keys == ["rowid"]) and (only is None or k in only or k in keys)}
            snap["rows"].append({"table": current, "key": key, "row": stored, **({"link": True} if only else {})})
            if not follow:
                continue
            for child, columns, parent_columns, action in children.get(current, []):
                targets = parent_columns if all(parent_columns) else keys  # no columns named: the key
                values = [row[c] for c in targets]
                if any(v is None for v in values):
                    continue
                if action == "CASCADE":
                    queue.append((child, dict(zip(columns, values)), True, None))
                else:
                    queue.append((child, dict(zip(columns, values)), False, columns))
    return snap


def created(snap: Snapshot, table: str, key: dict[str, Any]) -> Snapshot:
    """Note a row the action made: undo deletes it."""
    snap["created"].append({"table": table, "key": key})
    return snap


def restore(db: Database, snap: Snapshot) -> None:
    """Put the snapshot back in one transaction."""
    with db.transaction() as conn:
        # A sub-bubble may be listed before its parent: links are checked once, at the end.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for item in reversed(snap.get("created", [])):
            cond = " AND ".join(f"{_quote(k)} = ?" for k in item["key"])
            conn.execute(f"DELETE FROM {_quote(item['table'])} WHERE {cond}", tuple(item["key"].values()))
        for item in snap.get("rows", []):
            table, key = item["table"], item["key"]
            row = {k: _decode(v) for k, v in item["row"].items()}
            cond = " AND ".join(f"{_quote(k)} = ?" for k in key)
            exists = conn.execute(f"SELECT 1 FROM {_quote(table)} WHERE {cond}", tuple(key.values())).fetchone()
            if item.get("link") and not exists:
                continue  # the row that pointed here is gone since: nothing to relink
            if exists:
                sets = ", ".join(f"{_quote(k)} = ?" for k in row)
                conn.execute(f"UPDATE {_quote(table)} SET {sets} WHERE {cond}", (*row.values(), *key.values()))
            else:
                columns = ", ".join(_quote(k) for k in row)
                conn.execute(f"INSERT INTO {_quote(table)} ({columns}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values()))
