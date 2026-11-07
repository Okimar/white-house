"""Groups of characters, lorebooks and chats: CRUD per kind, membership, deletes, upgrade, copies."""

from __future__ import annotations

import sqlite3

import pytest

from tavern.services import assistant_app
from tavern.storage.backup import create_backup
from tavern.storage.db import Database
from tavern.storage.migrations import load_migrations


async def make_items(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "x"})).json()
    bob = (await client.post("/api/characters", json={"name": "Bob", "description": "x"})).json()
    book = (await client.post("/api/lorebooks", json={"name": "World"})).json()
    chat = (await client.post("/api/chats", json={"character_id": ann["id"]})).json()["chat"]
    return {"character": [ann["id"], bob["id"]], "lorebook": [book["id"]], "chat": [chat["id"]]}


async def test_crud_and_membership_per_kind(client):
    everything = await make_items(client)
    for kind in ("character", "lorebook", "chat"):
        await crud_of(client, kind, everything[kind])


async def crud_of(client, kind, items):
    made = await client.post("/api/groups", json={"kind": kind, "name": "  Мир РПГ  "})
    assert made.status_code == 201
    group = made.json()
    assert group["name"] == "Мир РПГ" and group["count"] == 0

    moved = await client.post("/api/groups/assign", json={"kind": kind, "item_ids": items, "group_id": group["id"]})
    assert moved.status_code == 200
    assert moved.json()["members"] == {i: group["id"] for i in items}
    assert moved.json()["groups"][0]["count"] == len(items)

    renamed = await client.put(f"/api/groups/{group['id']}", json={"name": "Мир B"})
    assert renamed.json()["name"] == "Мир B"

    # an item can leave
    freed = await client.post("/api/groups/assign", json={"kind": kind, "item_ids": items[:1], "group_id": None})
    assert items[0] not in freed.json()["members"]

    # other kinds do not see these groups
    other = next(k for k in ("character", "lorebook", "chat") if k != kind)
    assert (await client.get(f"/api/groups?kind={other}")).json()["groups"] == []

    # deleting the group frees the items, never deletes them
    assert (await client.delete(f"/api/groups/{group['id']}")).status_code == 200
    after = (await client.get(f"/api/groups?kind={kind}")).json()
    assert after["groups"] == [] and after["members"] == {}
    listing = {"character": "/api/characters", "lorebook": "/api/lorebooks", "chat": "/api/chats"}[kind]
    assert (await client.get(listing)).status_code == 200
    assert (await client.get(f"/api/groups/{group['id']}")).status_code in (404, 405)


async def test_validation(client):
    items = await make_items(client)
    assert (await client.post("/api/groups", json={"kind": "plugin", "name": "A"})).status_code == 400
    assert (await client.post("/api/groups", json={"kind": "chat", "name": "   "})).status_code == 400
    assert (await client.get("/api/groups")).status_code == 400
    chars = (await client.post("/api/groups", json={"kind": "character", "name": "A"})).json()
    # an item of another kind, a missing item, a group of another kind
    bad = await client.post("/api/groups/assign",
                            json={"kind": "character", "item_ids": items["lorebook"], "group_id": chars["id"]})
    assert bad.status_code == 404
    bad = await client.post("/api/groups/assign",
                            json={"kind": "lorebook", "item_ids": items["lorebook"], "group_id": chars["id"]})
    assert bad.status_code == 404
    assert (await client.post("/api/groups/assign", json={"kind": "chat", "item_ids": [], "group_id": None})).status_code == 400
    assert (await client.put("/api/groups/nope", json={"name": "A"})).status_code == 404
    assert (await client.delete("/api/groups/nope")).status_code == 404
    long = (await client.post("/api/groups", json={"kind": "chat", "name": "я" * 500})).json()
    assert len(long["name"]) == 120


async def test_moving_to_another_group_replaces_the_place(client):
    ids = (await make_items(client))["character"]
    a = (await client.post("/api/groups", json={"kind": "character", "name": "A"})).json()
    b = (await client.post("/api/groups", json={"kind": "character", "name": "B"})).json()
    await client.post("/api/groups/assign", json={"kind": "character", "item_ids": ids, "group_id": a["id"]})
    done = (await client.post("/api/groups/assign",
                              json={"kind": "character", "item_ids": ids[:1], "group_id": b["id"]})).json()
    assert done["members"][ids[0]] == b["id"] and done["members"][ids[1]] == a["id"]
    assert [g["count"] for g in done["groups"]] == [1, 1]
    assert [g["name"] for g in done["groups"]] == ["A", "B"]  # creation order


async def test_deleting_an_item_cleans_its_membership(client, ctx):
    items = await make_items(client)
    for kind, path in (("character", "characters"), ("lorebook", "lorebooks"), ("chat", "chats")):
        g = (await client.post("/api/groups", json={"kind": kind, "name": "G"})).json()
        await client.post("/api/groups/assign", json={"kind": kind, "item_ids": items[kind][-1:], "group_id": g["id"]})
        assert (await client.delete(f"/api/{path}/{items[kind][-1]}")).status_code in (200, 204)
        over = (await client.get(f"/api/groups?kind={kind}")).json()
        assert over["members"] == {} and over["groups"][0]["count"] == 0
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM group_members")["n"] == 0


async def test_assistant_reaches_groups_through_the_generic_call(client, ctx):
    routes = {(m, p) for m, p, _ in assistant_app._routes()}
    assert ("GET", "/groups") in routes and ("POST", "/groups") in routes
    assert ("DELETE", "/groups/{id}") in routes and ("POST", "/groups/assign") in routes
    assistant_app._check("POST", "/groups", {"kind": "chat", "name": "A"})  # not closed


def test_upgrade_from_the_previous_schema_keeps_data(tmp_path):
    db = Database(tmp_path / "old.sqlite3")
    db.migrate([m for m in load_migrations() if m.version <= 19])
    db.execute("INSERT INTO characters (id, name, data) VALUES ('c', 'Ann', '{}')")
    db.execute("INSERT INTO chats (id, title) VALUES ('h', 'H')")
    db.migrate()
    assert db.schema_version >= 20
    assert db.fetchone("SELECT name FROM characters WHERE id = 'c'")["name"] == "Ann"
    db.execute("INSERT INTO groups (id, kind, name, created_at) VALUES ('g', 'character', 'A', 't')")
    db.execute("INSERT INTO group_members (kind, item_id, group_id) VALUES ('character', 'c', 'g')")
    db.execute("DELETE FROM characters WHERE id = 'c'")
    assert db.fetchone("SELECT COUNT(*) AS n FROM group_members")["n"] == 0
    assert db.fetchone("SELECT COUNT(*) AS n FROM groups")["n"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO groups (id, kind, name, created_at) VALUES ('x', 'plugin', 'A', 't')")
    db.close()


async def test_whole_data_copy_carries_groups(client, ctx, tmp_path):
    items = await make_items(client)
    g = (await client.post("/api/groups", json={"kind": "chat", "name": "Копия"})).json()
    await client.post("/api/groups/assign", json={"kind": "chat", "item_ids": items["chat"], "group_id": g["id"]})
    copy = create_backup(ctx.paths.db, tmp_path / "copies", "test")
    conn = sqlite3.connect(str(copy))
    try:
        assert conn.execute("SELECT name FROM groups").fetchall() == [("Копия",)]
        assert conn.execute("SELECT item_id FROM group_members").fetchall() == [(items["chat"][0],)]
    finally:
        conn.close()
