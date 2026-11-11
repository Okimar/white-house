"""Memory in a chat file (ADR 0019 point E, stage M7): what the memory made or the user edited goes out with the chat and
comes back with it. Vectors NEVER go: they are rebuilt after an import (the new chat is woken, the job vectorizes).

`export(api, chat_id)` gives one JSON-able dict (None when memory was never on in the chat), `import_data(api, chat_id,
data, id_map)` writes it into the freshly imported chat. Every id that points at a message goes through `id_map` (old
message id -> new); chapters, texts, records and faces get new ids of their own, so the same file can be imported twice.
A row whose message is not in the file is skipped, never half-written. The format is versioned (`version`); a newer file
than this code reads is ignored, an older one is read as it is.

Shape (all lists may be empty):
  enabled, settings                the chat's switch
  chapters   [{id, first_id, last_id, title, state, content_hash, participants, whole, border_by, reason, ...}]
  texts      [{id, kind: summary|plot|development|now_line, target, anchor_id, made_from, text, by, locked,
               current, created_at}]   summaries (with the lock), every version of the plot, layers, «Сейчас:» lines
  members    [{character_id, remembers_all, no_layer}]
  threads    [{...record row...}], thread_states [{...}]       «Слово и нити» with statuses
  faces      [{id, name, aliases, card, favourite, made_by, locked, deleted, versions: [...], link: {...}}]
             only the mini-cards linked to this chat
  runs       {layer|thread|face: [chapter ids already done]}   so an imported chat does not redo them
"""

from __future__ import annotations

import json
from typing import Any

from tavern.util.ids import new_id, now_iso

from . import chapters, store

VERSION = 1
FACE_PREFIX = store.FACE_PREFIX
CHAPTER_COPY = ("id", "first_id", "last_id", "title", "state", "content_hash", "participants", "whole", "border_by",
                "reason", "tries", "created_at", "updated_at", "error", "failed_at")
THREAD_COPY = tuple(k for k in store.THREAD_FIELDS if k != "chat_id")


def export(api: Any, chat_id: str) -> dict[str, Any] | None:
    """The memory of one chat as data, or None when memory was never switched on there."""
    db = api.ctx.db
    row = store.chat_row(db, chat_id)
    if row is None:
        return None
    texts = [{**t, "current": bool(t["current"])} for t in _all_texts(db, chat_id)]
    links = {link["face_id"]: link for link in store.face_links(db, chat_id=chat_id)}
    faces = []
    for face in store.face_rows(db):
        link = links.get(face["id"])
        if link is None:
            continue
        faces.append({"id": face["id"], "name": face["name"], "aliases": face["aliases"], "card": face["card"],
                      "favourite": face["favourite"], "made_by": face["made_by"], "locked": face["locked"],
                      "deleted": face["deleted"], "created_at": face["created_at"],
                      "versions": [{"name": v["name"], "aliases": v["aliases"], "card": v["card"],
                                    "made_by": v["by"], "created_at": v["created_at"]}
                                   for v in reversed(store.face_versions(db, face["id"]))],
                      "link": {"first_message_id": link["first_message_id"], "in_use": link["in_use"]}})
    flags = store.member_flags(db, chat_id)
    return {
        "version": VERSION, "enabled": row["enabled"], "settings": row["settings"],
        "chapters": [{k: c[k] for k in CHAPTER_COPY} for c in store.chapter_rows(db, chat_id)],
        "texts": texts,
        "members": [{"character_id": k, **v} for k, v in flags.items()],
        "threads": [{k: t[k] for k in THREAD_COPY} for t in store.thread_rows(db, chat_id)],
        "thread_states": [{k: s[k] for k in ("id", "thread_id", "anchor_id", "status", "knows", "note", "message_id",
                                            "by", "created_at")} for s in reversed(store.thread_states(db, chat_id))],
        "faces": faces,
        "runs": {name: sorted(i for i, r in store.runs(db, table, chat_id).items() if r["state"] == "done")
                 for name, table in (("layer", store.LAYER_RUNS), ("thread", store.THREAD_RUNS),
                                     ("face", store.FACE_RUNS))},
    }


def _all_texts(db: Any, chat_id: str) -> list[dict[str, Any]]:
    """Every text row of the chat, oldest first (so a later import keeps the order of versions)."""
    rows = db.fetchall(f"SELECT {store.TEXT_FIELDS} FROM {store.TEXTS} WHERE chat_id = ? ORDER BY created_at, rowid",
                       (chat_id,))
    return [store._text(r) for r in rows]


def _span(api: Any, first: str, last: str) -> list[dict[str, Any]] | None:
    """The messages from `first` to `last` along the parent chain (any branch), or None when `first` is not above."""
    chats = api.ctx.chats
    found: list[dict[str, Any]] = []
    current: str | None = last
    while current:
        message = chats.get_message(current)
        found.append(message)
        if message["id"] == first:
            return list(reversed(found))
        current = message["parent_id"]
    return None


def import_data(api: Any, chat_id: str, data: dict[str, Any], id_map: dict[str, str]) -> bool:
    """Write the exported memory into `chat_id` (new messages are in `id_map`). False when the file is not for this
    code (a newer version) or holds nothing."""
    if not isinstance(data, dict) or int(data.get("version") or 0) > VERSION:
        return False
    db = api.ctx.db

    def mid(old: Any) -> str | None:
        return id_map.get(old) if isinstance(old, str) else None

    face_map = _import_faces(api, chat_id, data.get("faces") or [], mid)

    def person(value: Any) -> str | None:
        """A character / persona id stays; a mini-card's `face:<id>` goes to its new id (None: that card is not here)."""
        value = str(value or "")
        if value.startswith(FACE_PREFIX):
            new = face_map.get(value[len(FACE_PREFIX):])
            return None if new is None else FACE_PREFIX + new
        return value

    chapter_map: dict[str, str] = {}
    hash_map: dict[str, str] = {}
    rows = []
    for old in data.get("chapters") or []:
        first, last = mid(old.get("first_id")), mid(old.get("last_id"))
        if not first or not last:
            continue
        span = _span(api, first, last)
        fresh = chapters.content_hash(span) if span else str(old.get("content_hash") or "")
        new_id_ = new_id()
        chapter_map[old["id"]] = new_id_
        hash_map[str(old.get("content_hash") or "")] = fresh
        rows.append({**{k: old.get(k, "") for k in CHAPTER_COPY}, "id": new_id_, "chat_id": chat_id, "first_id": first,
                     "last_id": last, "content_hash": fresh, "participants": list(old.get("participants") or []),
                     "whole": bool(old.get("whole")), "tries": int(old.get("tries") or 0),
                     "created_at": old.get("created_at") or now_iso(), "updated_at": old.get("updated_at") or now_iso()})
    if rows:
        store.put_chapters(db, rows)
    store.set_enabled(db, chat_id, bool(data.get("enabled")))
    if isinstance(data.get("settings"), dict) and data["settings"]:
        db.execute(f"UPDATE {store.CHATS} SET settings = ? WHERE chat_id = ?",
                   (json.dumps(data["settings"], ensure_ascii=False), chat_id))
    _import_texts(api, chat_id, data.get("texts") or [], mid, chapter_map, hash_map, person)
    for flag in data.get("members") or []:
        who = person(flag.get("character_id"))
        if who:
            store.set_member(db, chat_id, who, remembers_all=bool(flag.get("remembers_all")),
                             no_layer=bool(flag.get("no_layer")))
    _import_threads(api, chat_id, data, mid, chapter_map, person)
    runs = data.get("runs") or {}
    for name, table in (("layer", store.LAYER_RUNS), ("thread", store.THREAD_RUNS), ("face", store.FACE_RUNS)):
        for old in runs.get(name) or []:
            if old in chapter_map:
                store.put_run(db, table, chat_id, chapter_map[old], "done")
    return True


def _import_faces(api: Any, chat_id: str, items: list[dict[str, Any]], mid: Any) -> dict[str, str]:
    """Mini-cards linked to the chat. The same app (the card is there under the same id and name) just gets linked;
    otherwise a new card is made, with its versions. Returns old face id -> id here."""
    db = api.ctx.db
    known = {f["id"]: f for f in store.face_rows(db)}
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("name") or not isinstance(item.get("id"), str):
            continue
        link = item.get("link") or {}
        same = known.get(item["id"])
        if same is not None and same["name"] == item["name"]:
            out[item["id"]] = same["id"]
            store.link_face(db, same["id"], chat_id, mid(link.get("first_message_id")),
                            in_use=bool(link.get("in_use", True)))
            continue
        row = store.new_face(str(item["name"])[:60], str(item.get("card") or ""), aliases=list(item.get("aliases") or []),
                             by=str(item.get("made_by") or "auto"), origin=chat_id, locked=bool(item.get("locked")))
        row["favourite"] = bool(item.get("favourite"))
        row["deleted"] = bool(item.get("deleted"))
        store.put_face(db, row)
        for version in item.get("versions") or []:
            with db.transaction() as conn:
                conn.execute(f"INSERT INTO {store.FACE_VERSIONS} (id, face_id, name, aliases, card, made_by, "
                             "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (new_id(), row["id"], version.get("name", row["name"]),
                              json.dumps(version.get("aliases") or [], ensure_ascii=False), version.get("card", ""),
                              version.get("made_by", "auto"), version.get("created_at") or now_iso()))
        store.link_face(db, row["id"], chat_id, mid(link.get("first_message_id")),
                        in_use=bool(link.get("in_use", True)))
        out[item["id"]] = row["id"]
    return out


def _import_texts(api: Any, chat_id: str, items: list[dict[str, Any]], mid: Any, chapter_map: dict[str, str],
                  hash_map: dict[str, str], person: Any) -> None:
    db = api.ctx.db
    text_map = {t["id"]: new_id() for t in items if isinstance(t, dict) and isinstance(t.get("id"), str)}
    with db.transaction() as conn:
        for t in items:
            if not isinstance(t, dict) or t.get("id") not in text_map or t.get("kind") not in (
                    "summary", "plot", "development", "now_line"):
                continue
            kind, target, anchor, made_from = t["kind"], str(t.get("target") or ""), mid(t.get("anchor_id")), \
                str(t.get("made_from") or "")
            if kind == "summary":
                if target not in chapter_map:
                    continue
                target, anchor = chapter_map[target], None
                made_from = hash_map.get(made_from, made_from)
            else:
                if not anchor:
                    continue
                if kind == "plot":
                    try:
                        ids = json.loads(made_from) if made_from else []
                    except ValueError:
                        ids = []
                    made_from = json.dumps([text_map[i] for i in ids if i in text_map])
                elif kind == "development":
                    target = person(target) or ""
                    if not target:
                        continue
            conn.execute(f"INSERT INTO {store.TEXTS} (id, chat_id, kind, target, anchor_id, made_from, text, made_by, "
                         "locked, is_current, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (text_map[t["id"]], chat_id, kind, target, anchor, made_from, str(t.get("text") or ""),
                          str(t.get("by") or "auto"), int(bool(t.get("locked"))), int(bool(t.get("current", True))),
                          t.get("created_at") or now_iso()))


def _import_threads(api: Any, chat_id: str, data: dict[str, Any], mid: Any, chapter_map: dict[str, str],
                    person: Any) -> None:
    db = api.ctx.db
    thread_map: dict[str, str] = {}
    for old in data.get("threads") or []:
        anchor = mid(old.get("anchor_id"))
        who = person(old.get("who"))
        if not anchor or not who or not old.get("quote"):
            continue
        to = person(old.get("to_whom")) or ""
        knows = [k for k in (person(x) for x in old.get("knows") or []) if k]
        row = {**{k: old.get(k) for k in THREAD_COPY}, "id": new_id(), "chat_id": chat_id, "who": who, "to_whom": to,
               "knows": knows, "anchor_id": anchor, "message_id": mid(old.get("message_id")),
               "chapter_id": chapter_map.get(old.get("chapter_id") or "", ""),
               "importance": int(old.get("importance") or 2), "keywords": list(old.get("keywords") or []),
               "made_by": old.get("made_by") or "auto", "locked": bool(old.get("locked")),
               "deleted": bool(old.get("deleted")), "created_at": old.get("created_at") or now_iso(),
               "updated_at": old.get("updated_at") or now_iso()}
        store.put_thread(db, row)
        thread_map[old["id"]] = row["id"]
    for state in data.get("thread_states") or []:
        thread, anchor = thread_map.get(state.get("thread_id") or ""), mid(state.get("anchor_id"))
        if not thread or not anchor:
            continue
        knows = [k for k in (person(x) for x in state.get("knows") or []) if k]
        store.put_state(db, chat_id, thread, anchor, str(state.get("status") or "active"), knows,
                        by=str(state.get("by") or "auto"), note=str(state.get("note") or ""),
                        message_id=mid(state.get("message_id")))
