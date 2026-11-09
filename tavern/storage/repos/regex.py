"""Regex rules (table `regex_scripts`).

Rule dict shape (what the services and the page use): id, scope ('global' | 'character' | 'preset'),
owner_id, name, enabled, find, replace, trim (list), places (list), when, min_depth, max_depth,
run_on_edit, macros, render_html, approved, position, extra (SillyTavern fields we do not use).
"""

from __future__ import annotations

import json
from typing import Any

from tavern.errors import NotFound
from tavern.storage.db import Database
from tavern.util.ids import new_id, now_iso

SCOPES = ("global", "character", "preset")
_SELECT = ("SELECT id, scope, owner_id, name, enabled, find, replace_with, trim_out, places, applies_to, min_depth, "
           "max_depth, run_on_edit, macros, render_html, approved, position, extra, created_at, updated_at "
           "FROM regex_scripts")


def _rule(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"], "scope": row["scope"], "owner_id": row["owner_id"], "name": row["name"],
        "enabled": bool(row["enabled"]), "find": row["find"], "replace": row["replace_with"],
        "trim": json.loads(row["trim_out"]), "places": json.loads(row["places"]), "when": row["applies_to"],
        "min_depth": row["min_depth"], "max_depth": row["max_depth"], "run_on_edit": bool(row["run_on_edit"]),
        "macros": row["macros"], "render_html": bool(row["render_html"]), "approved": bool(row["approved"]),
        "position": row["position"], "extra": json.loads(row["extra"]),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


class RegexRepo:
    changes = 0  # bumped by every change made through here; part of stamp()

    def __init__(self, db: Database) -> None:
        self.db = db

    def stamp(self) -> tuple:
        """Changes whenever the rules do (also edits made behind this class's back: triggers, a restored copy),
        so a service may keep what it built from them. One cheap query; a swap of two rules changes it too."""
        row = self.db.fetchone(
            "SELECT COUNT(*), SUM(position * rowid), SUM(approved * rowid), SUM(enabled * rowid), "
            "MAX(updated_at), SUM(length(find) + length(replace_with) + length(name) + length(extra)) "
            "FROM regex_scripts")
        return (RegexRepo.changes, *tuple(row))

    def count(self, scope: str, owner_id: str = "") -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM regex_scripts WHERE scope = ? AND owner_id = ?",
                               (scope, owner_id))
        return row["n"]

    def list(self, scope: str, owner_id: str = "") -> list[dict[str, Any]]:
        rows = self.db.fetchall(f"{_SELECT} WHERE scope = ? AND owner_id = ? ORDER BY position, created_at, id",
                                (scope, owner_id))
        return [_rule(r) for r in rows]

    def owners(self, scope: str) -> dict[str, int]:
        """owner id -> how many rules it has."""
        rows = self.db.fetchall("SELECT owner_id, COUNT(*) AS n FROM regex_scripts WHERE scope = ? GROUP BY owner_id",
                                (scope,))
        return {r["owner_id"]: r["n"] for r in rows}

    def all(self) -> list[dict[str, Any]]:
        return [_rule(r) for r in self.db.fetchall(f"{_SELECT} ORDER BY scope, owner_id, position, created_at")]

    def get(self, rule_id: str) -> dict[str, Any]:
        row = self.db.fetchone(f"{_SELECT} WHERE id = ?", (rule_id,))
        if row is None:
            raise NotFound("Правило не найдено")
        return _rule(row)

    def create(self, scope: str, owner_id: str, rule: dict[str, Any], *, approved: bool = True) -> dict[str, Any]:
        RegexRepo.changes += 1
        rule_id, now = new_id(), now_iso()
        last = self.db.fetchone("SELECT COALESCE(MAX(position), 0) AS p FROM regex_scripts "
                                "WHERE scope = ? AND owner_id = ?", (scope, owner_id))
        self.db.execute(
            "INSERT INTO regex_scripts (id, scope, owner_id, name, enabled, find, replace_with, trim_out, places, "
            "applies_to, min_depth, max_depth, run_on_edit, macros, render_html, approved, position, extra, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rule_id, scope, owner_id, rule["name"], int(rule["enabled"]), rule["find"], rule["replace"],
             json.dumps(rule["trim"], ensure_ascii=False), json.dumps(rule["places"]), rule["when"],
             rule["min_depth"], rule["max_depth"], int(rule["run_on_edit"]), rule["macros"],
             int(rule["render_html"]), int(approved), last["p"] + 1,
             json.dumps(rule.get("extra") or {}, ensure_ascii=False), now, now))
        return self.get(rule_id)

    def create_many(self, scope: str, owner_id: str, rules: list[dict[str, Any]], *, approved: bool = True
                    ) -> list[dict[str, Any]]:
        """Many rules in one transaction (an import of a thousand is one write, not a thousand)."""
        RegexRepo.changes += 1
        now = now_iso()
        ids: list[str] = []
        with self.db.transaction() as conn:
            position = conn.execute("SELECT COALESCE(MAX(position), 0) FROM regex_scripts "
                                    "WHERE scope = ? AND owner_id = ?", (scope, owner_id)).fetchone()[0]
            for rule in rules:
                position += 1
                ids.append(new_id())
                conn.execute(
                    "INSERT INTO regex_scripts (id, scope, owner_id, name, enabled, find, replace_with, trim_out, "
                    "places, applies_to, min_depth, max_depth, run_on_edit, macros, render_html, approved, position, "
                    "extra, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ids[-1], scope, owner_id, rule["name"], int(rule["enabled"]), rule["find"], rule["replace"],
                     json.dumps(rule["trim"], ensure_ascii=False), json.dumps(rule["places"]), rule["when"],
                     rule["min_depth"], rule["max_depth"], int(rule["run_on_edit"]), rule["macros"],
                     int(rule["render_html"]), int(approved), position,
                     json.dumps(rule.get("extra") or {}, ensure_ascii=False), now, now))
        made = set(ids)
        return [r for r in self.list(scope, owner_id) if r["id"] in made]

    def update(self, rule_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        RegexRepo.changes += 1
        self.get(rule_id)
        self.db.execute(
            "UPDATE regex_scripts SET name = ?, enabled = ?, find = ?, replace_with = ?, trim_out = ?, places = ?, "
            "applies_to = ?, min_depth = ?, max_depth = ?, run_on_edit = ?, macros = ?, render_html = ?, "
            "extra = ?, updated_at = ? WHERE id = ?",
            (rule["name"], int(rule["enabled"]), rule["find"], rule["replace"],
             json.dumps(rule["trim"], ensure_ascii=False), json.dumps(rule["places"]), rule["when"],
             rule["min_depth"], rule["max_depth"], int(rule["run_on_edit"]), rule["macros"],
             int(rule["render_html"]), json.dumps(rule.get("extra") or {}, ensure_ascii=False), now_iso(), rule_id))
        return self.get(rule_id)

    def delete(self, rule_id: str) -> None:
        RegexRepo.changes += 1
        self.get(rule_id)
        self.db.execute("DELETE FROM regex_scripts WHERE id = ?", (rule_id,))

    def set_order(self, scope: str, owner_id: str, ids: list[str]) -> None:
        RegexRepo.changes += 1
        with self.db.transaction() as conn:
            for position, rule_id in enumerate(ids, start=1):
                conn.execute("UPDATE regex_scripts SET position = ? WHERE id = ? AND scope = ? AND owner_id = ?",
                             (position, rule_id, scope, owner_id))

    def approve(self, scope: str, owner_id: str) -> int:
        RegexRepo.changes += 1
        return self.db.execute("UPDATE regex_scripts SET approved = 1 WHERE scope = ? AND owner_id = ? "
                               "AND approved = 0", (scope, owner_id)).rowcount

    def pending(self, scope: str, owner_id: str) -> int:
        row = self.db.fetchone("SELECT COUNT(*) AS n FROM regex_scripts WHERE scope = ? AND owner_id = ? "
                               "AND approved = 0", (scope, owner_id))
        return row["n"]

    def delete_scope(self, scope: str) -> int:
        """Every rule of one scope (all owners). Returns how many were deleted."""
        RegexRepo.changes += 1
        return self.db.execute("DELETE FROM regex_scripts WHERE scope = ?", (scope,)).rowcount

    def delete_owner(self, scope: str, owner_id: str) -> None:
        RegexRepo.changes += 1
        self.db.execute("DELETE FROM regex_scripts WHERE scope = ? AND owner_id = ?", (scope, owner_id))
