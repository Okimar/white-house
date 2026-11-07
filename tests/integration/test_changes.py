"""«Изменения»: the common list of what the assistant changed, grouped by request, paged and filtered,
what exactly changed, and rollback from the journal (a deletion too) with warnings about later work."""

from __future__ import annotations

from tavern.storage import snapshot


def at(day: int, hour: int = 12, minute: int = 0, second: int = 0) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}.000Z"


def request(ctx, conv: str, text: str, when: str) -> None:
    message = ctx.assistant_repo.add_message(conv, "user", text)
    ctx.db.execute("UPDATE assistant_messages SET created_at = ? WHERE id = ?", (when, message["id"]))


def act(ctx, conv: str, what: str, undo: dict, when: str, tool: str = "app_call", status: str = "done") -> int:
    action = ctx.assistant_repo.add_action(conv, tool, {}, what, undo, status)
    ctx.db.execute("UPDATE assistant_actions SET created_at = ? WHERE id = ?", (when, action["id"]))
    return action["id"]


def conversation(ctx, title: str) -> str:
    return ctx.assistant_repo.create_chat(title)["id"]


async def new_book(client, ctx, conv: str, name: str, when: str) -> tuple[str, int]:
    """A lorebook made 'by the assistant': the book exists, the action says undo deletes it."""
    book = (await client.post("/api/lorebooks", json={"name": name})).json()["id"]
    snap = snapshot.created(snapshot.empty(), "lorebooks", {"id": book})
    return book, act(ctx, conv, f"Создан лорбук «{name}»", snap, when)


async def test_list_groups_by_request_across_conversations(client, ctx):
    a, b = conversation(ctx, "Город"), conversation(ctx, "Таверна")
    request(ctx, a, "Сделай город", at(1, 10))
    await new_book(client, ctx, a, "Улицы", at(1, 10, 1))
    await new_book(client, ctx, a, "Рынок", at(1, 10, 2))
    request(ctx, b, "Сделай таверну", at(1, 11))
    await new_book(client, ctx, b, "Зал", at(1, 11, 1))
    request(ctx, a, "Ещё один", at(2, 9))
    await new_book(client, ctx, a, "Порт", at(2, 9, 1))
    # nothing changed or nothing happened: not in the list
    act(ctx, a, "Не вышло: что-то", {}, at(2, 9, 5), status="failed")
    act(ctx, a, "Нельзя", {}, at(2, 9, 6), status="denied")

    data = (await client.get("/api/changes")).json()
    assert data["total"] == 4
    commits = data["commits"]
    assert [c["request"] for c in commits] == ["Ещё один", "Сделай таверну", "Сделай город"]  # newest first
    assert [c["conversation"] for c in commits] == ["Город", "Таверна", "Город"]
    assert [c["count"] for c in commits] == [1, 1, 2]
    assert [x["what"] for x in commits[2]["actions"]] == ["Создан лорбук «Улицы»", "Создан лорбук «Рынок»"]
    assert all(c["status"] == "done" and c["can_rollback"] and c["rollback_id"] for c in commits)
    assert {c["title"] for c in data["conversations"]} == {"Город", "Таверна"}


async def test_filter_by_day_and_conversation(client, ctx):
    a, b = conversation(ctx, "Первая"), conversation(ctx, "Вторая")
    for day, conv in ((1, a), (2, b), (3, a)):
        request(ctx, conv, f"Запрос {day}", at(day, 8))
        await new_book(client, ctx, conv, f"Книга {day}", at(day, 8, 1))
    page = (await client.get("/api/changes", params={"since": at(2, 0), "until": at(3, 0)})).json()
    assert [c["request"] for c in page["commits"]] == ["Запрос 2"]
    page = (await client.get("/api/changes", params={"conversation": a})).json()
    assert [c["request"] for c in page["commits"]] == ["Запрос 3", "Запрос 1"]
    page = (await client.get("/api/changes", params={"conversation": a, "since": at(3, 0)})).json()
    assert [c["request"] for c in page["commits"]] == ["Запрос 3"]


async def test_paging_walks_every_commit_once(client, ctx):
    conv = conversation(ctx, "Много")
    for n in range(1, 26):
        request(ctx, conv, f"Запрос {n}", at(1, 8, n))
        act(ctx, conv, f"Действие {n}", {}, at(1, 8, n, 30))
    seen, cursor = [], 0
    while True:
        params = {"limit": 10} | ({"before": cursor} if cursor else {})
        data = (await client.get("/api/changes", params=params)).json()
        seen += [c["request"] for c in data["commits"]]
        cursor = data["next"]
        if not cursor:
            break
    assert seen == [f"Запрос {n}" for n in range(25, 0, -1)]
    assert (await client.get("/api/changes", params={"limit": "x"})).status_code == 400


async def test_details_say_created_changed_deleted(client, ctx):
    conv = conversation(ctx, "Детали")
    request(ctx, conv, "Поправь", at(1))
    kept = (await client.post("/api/lorebooks", json={"name": "Старое"})).json()["id"]
    gone = (await client.post("/api/lorebooks", json={"name": "Удалю"})).json()["id"]
    made = (await client.post("/api/lorebooks", json={"name": "Новое"})).json()["id"]
    snap = snapshot.capture(ctx.db, "lorebooks", {"id": kept})
    snapshot.capture(ctx.db, "lorebooks", {"id": gone}, cascade=True, into=snap)
    snapshot.created(snap, "lorebooks", {"id": made})
    ctx.db.execute("UPDATE lorebooks SET name = 'Новое имя' WHERE id = ?", (kept,))
    ctx.db.execute("DELETE FROM lorebooks WHERE id = ?", (gone,))
    action = act(ctx, conv, "Книги", snap, at(1, 12, 1))

    entries = {(e["state"], e["name"]): e for e in (await client.get(f"/api/changes/actions/{action}")).json()["entries"]}
    assert set(entries) == {("created", "Новое"), ("changed", "Старое"), ("deleted", "Удалю")}
    changed = entries[("changed", "Старое")]
    assert changed["before"]["имя"] == "Старое" and changed["after"]["имя"] == "Новое имя"
    assert entries[("deleted", "Удалю")]["after"] is None and entries[("deleted", "Удалю")]["kind"] == "лорбук"


async def test_rollback_puts_back_a_deleted_book_and_marks_the_commit(client, ctx):
    conv = conversation(ctx, "Откат")
    request(ctx, conv, "Удали книгу", at(1))
    book = (await client.post("/api/lorebooks", json={"name": "Важная"})).json()["id"]
    snap = snapshot.capture(ctx.db, "lorebooks", {"id": book}, cascade=True)
    ctx.db.execute("DELETE FROM lorebooks WHERE id = ?", (book,))
    action = act(ctx, conv, "Удалён лорбук «Важная»", snap, at(1, 12, 1))
    assert ctx.db.fetchone("SELECT 1 FROM lorebooks WHERE id = ?", (book,)) is None

    plan = (await client.get(f"/api/changes/actions/{action}/plan")).json()
    assert plan["can"] and plan["this"] == ["Удалён лорбук «Важная»"] and not plan["needs_confirm"]
    done = await client.post(f"/api/changes/actions/{action}/rollback", json={})
    assert done.status_code == 200 and done.json() == {"undone": [action]}
    assert ctx.db.fetchone("SELECT name FROM lorebooks WHERE id = ?", (book,))["name"] == "Важная"

    commit = (await client.get("/api/changes")).json()["commits"][0]
    assert commit["status"] == "undone" and not commit["can_rollback"]
    again = await client.post(f"/api/changes/actions/{action}/rollback", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "cannot_rollback"
    assert (await client.get(f"/api/changes/actions/{action}/plan")).json()["can"] is False


async def test_rollback_of_an_older_request_warns_about_later_ones(client, ctx):
    conv = conversation(ctx, "Порядок")
    request(ctx, conv, "Первое", at(1, 8))
    first_book, first = await new_book(client, ctx, conv, "Первая", at(1, 8, 1))
    request(ctx, conv, "Второе", at(1, 9))
    second_book, _ = await new_book(client, ctx, conv, "Вторая", at(1, 9, 1))

    plan = (await client.get(f"/api/changes/actions/{first}/plan")).json()
    assert plan["needs_confirm"] and plan["this"] == ["Создан лорбук «Первая»"]
    assert plan["later"][0]["request"] == "Второе" and plan["later"][0]["whats"] == ["Создан лорбук «Вторая»"]

    refused = await client.post(f"/api/changes/actions/{first}/rollback", json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "needs_confirm"
    assert refused.json()["error"]["details"]["later"][0]["request"] == "Второе"
    assert ctx.db.fetchone("SELECT 1 FROM lorebooks WHERE id = ?", (first_book,)) is not None  # nothing happened

    ok = await client.post(f"/api/changes/actions/{first}/rollback", json={"accepted": True})
    assert ok.status_code == 200 and len(ok.json()["undone"]) == 2
    assert ctx.db.fetchone("SELECT 1 FROM lorebooks WHERE id IN (?, ?)", (first_book, second_book)) is None
    assert {c["status"] for c in (await client.get("/api/changes")).json()["commits"]} == {"undone"}


async def test_rollback_warns_when_another_conversation_touched_the_same_thing(client, ctx):
    a, b = conversation(ctx, "Одна"), conversation(ctx, "Другая")
    request(ctx, a, "Создай", at(1, 8))
    book, first = await new_book(client, ctx, a, "Общая", at(1, 8, 1))
    request(ctx, b, "Переименуй", at(1, 9))
    snap = snapshot.capture(ctx.db, "lorebooks", {"id": book})
    ctx.db.execute("UPDATE lorebooks SET name = 'Переименованная' WHERE id = ?", (book,))
    act(ctx, b, "Лорбук переименован", snap, at(1, 9, 1))

    plan = (await client.get(f"/api/changes/actions/{first}/plan")).json()
    assert plan["needs_confirm"] and not plan["later"]
    assert plan["clashes"][0]["conversation"] == "Другая" and plan["clashes"][0]["what"] == "Лорбук переименован"
    assert (await client.post(f"/api/changes/actions/{first}/rollback", json={})).status_code == 409
    assert (await client.post(f"/api/changes/actions/{first}/rollback", json={"accepted": True})).status_code == 200


async def test_rollback_waits_while_the_assistant_works(client, ctx):
    conv = conversation(ctx, "Занята")
    request(ctx, conv, "Сделай", at(1))
    _, action = await new_book(client, ctx, conv, "Книга", at(1, 12, 1))

    class Busy:
        id = "r"

    ctx.runs.active = lambda key: Busy() if key.endswith(conv) else None  # type: ignore[method-assign]
    plan = (await client.get(f"/api/changes/actions/{action}/plan")).json()
    assert plan["can"] is False and "работает" in plan["reason"]
    assert (await client.post(f"/api/changes/actions/{action}/rollback", json={"accepted": True})).status_code == 409


async def test_old_actions_without_a_request_or_undo_still_show(client, ctx):
    conv = conversation(ctx, "")  # no title, no user message at all
    action = act(ctx, conv, "Старое действие", {}, at(1))
    commit = (await client.get("/api/changes")).json()["commits"][0]
    assert commit["request"] == "" and commit["conversation"].startswith("Беседа от ")
    assert commit["actions"][0]["undoable"] is False and commit["can_rollback"] is False
    assert (await client.get(f"/api/changes/actions/{action}")).json()["entries"] == []
    assert (await client.get("/api/changes/actions/99999")).status_code == 404


async def test_journal_outlives_a_deleted_conversation_and_rolls_back(client, ctx):
    conv = conversation(ctx, "Исчезнет")
    request(ctx, conv, "Удали книгу", at(1))
    book = (await client.post("/api/lorebooks", json={"name": "Важная"})).json()["id"]
    snap = snapshot.capture(ctx.db, "lorebooks", {"id": book}, cascade=True)
    ctx.db.execute("DELETE FROM lorebooks WHERE id = ?", (book,))
    action = act(ctx, conv, "Удалён лорбук «Важная»", snap, at(1, 12, 1))

    assert (await client.delete(f"/api/assistant/chats/{conv}")).status_code == 200
    assert ctx.db.fetchone("SELECT 1 FROM assistant_chats WHERE id = ?", (conv,)) is None

    data = (await client.get("/api/changes")).json()
    assert data["total"] == 1
    commit = data["commits"][0]
    assert commit["conversation"] == "Исчезнет" and commit["conversation_deleted"] is True
    assert commit["can_rollback"] and commit["chat_id"] == conv
    assert data["conversations"] == [{"id": conv, "title": "Исчезнет", "count": 1, "deleted": True}]
    assert (await client.get("/api/changes", params={"conversation": conv})).json()["total"] == 1

    plan = (await client.get(f"/api/changes/actions/{action}/plan")).json()
    assert plan["can"] and plan["conversation"] == "Исчезнет"
    done = await client.post(f"/api/changes/actions/{action}/rollback", json={})
    assert done.status_code == 200 and done.json() == {"undone": [action]}
    assert ctx.db.fetchone("SELECT name FROM lorebooks WHERE id = ?", (book,))["name"] == "Важная"
    assert (await client.get("/api/changes")).json()["commits"][0]["status"] == "undone"


async def test_deleted_untitled_conversation_keeps_a_dated_name(client, ctx):
    conv = conversation(ctx, "")
    act(ctx, conv, "Что-то", {}, at(1))
    ctx.assistant_repo.delete_chat(conv)
    assert (await client.get("/api/changes")).json()["commits"][0]["conversation"].startswith("Беседа от ")


async def test_clash_scan_names_a_deleted_conversation(client, ctx):
    a, b = conversation(ctx, "Одна"), conversation(ctx, "Другая")
    request(ctx, a, "Создай", at(1, 8))
    book, first = await new_book(client, ctx, a, "Общая", at(1, 8, 1))
    snap = snapshot.capture(ctx.db, "lorebooks", {"id": book})
    ctx.db.execute("UPDATE lorebooks SET name = 'Иначе' WHERE id = ?", (book,))
    act(ctx, b, "Переименован", snap, at(1, 9, 1))
    ctx.assistant_repo.delete_chat(b)
    plan = (await client.get(f"/api/changes/actions/{first}/plan")).json()
    assert plan["clashes"][0]["conversation"] == "Другая"


def test_migration_0019_keeps_every_row(tmp_path):
    """From the real previous schema (v18): rows survive, names are filled, deleting a conversation keeps actions."""
    from tavern.storage.db import Database, load_migrations

    db = Database(tmp_path / "t.db")
    old = [m for m in load_migrations() if m.version <= 18]
    db.migrate(old)
    db.execute("INSERT INTO assistant_chats (id, title, created_at, updated_at) VALUES ('c1', ' Город ', '2026-10-01T00:00:00Z', 'x')")
    db.execute("INSERT INTO assistant_chats (id, title, created_at, updated_at) VALUES ('c2', '', '2026-10-02T00:00:00Z', 'x')")
    for i, chat in enumerate(("c1", "c1", "c2"), 1):
        db.execute("INSERT INTO assistant_actions (id, chat_id, tool, args, what, undo, status, created_at) "
                   "VALUES (?, ?, 't', '{\"a\":1}', ?, '{\"rows\":[]}', 'done', '2026-10-0' || ? || 'T00:00:00Z')", (i, chat, f"w{i}", i))
    assert db.migrate([m for m in load_migrations() if m.version <= 19]) == [19]
    rows = db.fetchall("SELECT * FROM assistant_actions ORDER BY id")
    assert [(r["id"], r["chat_id"], r["chat_ref"], r["what"], r["args"], r["undo"]) for r in rows] == [
        (1, "c1", "c1", "w1", '{"a":1}', '{"rows":[]}'), (2, "c1", "c1", "w2", '{"a":1}', '{"rows":[]}'),
        (3, "c2", "c2", "w3", '{"a":1}', '{"rows":[]}')]
    assert [r["chat_title"] for r in rows] == ["Город", "Город", "Беседа от 2026-10-02"]
    db.execute("DELETE FROM assistant_chats WHERE id = 'c1'")
    kept = db.fetchall("SELECT id, chat_id, chat_ref FROM assistant_actions ORDER BY id")
    assert [(r["id"], r["chat_id"], r["chat_ref"]) for r in kept] == [(1, None, "c1"), (2, None, "c1"), (3, "c2", "c2")]


async def test_requests_stay_apart_with_their_text_after_the_conversation_is_deleted(client, ctx):
    conv = conversation(ctx, "Уйдёт")
    request(ctx, conv, "Первое дело", at(1, 8))
    first_book, first = await new_book(client, ctx, conv, "Старая", at(1, 8, 1))
    request(ctx, conv, "Второе дело", at(1, 9))
    second_book, second = await new_book(client, ctx, conv, "Новая", at(1, 9, 1))
    assert (await client.delete(f"/api/assistant/chats/{conv}")).status_code == 200
    assert ctx.db.fetchone("SELECT COUNT(*) AS n FROM assistant_messages WHERE chat_id = ?", (conv,))["n"] == 0

    commits = (await client.get("/api/changes")).json()["commits"]
    assert [(c["request"], c["count"]) for c in commits] == [("Второе дело", 1), ("Первое дело", 1)]
    assert all(c["conversation_deleted"] for c in commits)

    # Only the newer request goes back; the older one stays in force.
    plan = (await client.get(f"/api/changes/actions/{second}/plan")).json()
    assert plan["can"] and not plan["needs_confirm"] and plan["later"] == []
    done = await client.post(f"/api/changes/actions/{second}/rollback", json={})
    assert done.status_code == 200 and done.json() == {"undone": [second]}
    assert ctx.db.fetchone("SELECT 1 FROM lorebooks WHERE id = ?", (second_book,)) is None
    assert ctx.db.fetchone("SELECT 1 FROM lorebooks WHERE id = ?", (first_book,)) is not None

    # The older one still warns about taking later work along, with the later request's text.
    again = (await client.get(f"/api/changes/actions/{first}/plan")).json()
    assert again["this"] == ["Создан лорбук «Старая»"] and again["later"] == []


def test_migration_0019_fills_the_request_of_existing_actions(tmp_path):
    from tavern.storage.db import Database, load_migrations

    db = Database(tmp_path / "t.db")
    db.migrate([m for m in load_migrations() if m.version <= 18])
    db.execute("INSERT INTO assistant_chats (id, title, created_at, updated_at) VALUES ('c1', 'A', '2026-10-01T00:00:00Z', 'x')")
    for text, when in (("Раз", "2026-10-01T01:00:00Z"), ("Давай", "2026-10-01T01:30:00Z"), ("Два", "2026-10-01T02:00:00Z")):
        db.execute("INSERT INTO assistant_messages (chat_id, role, text, data, created_at) VALUES ('c1', 'user', ?, '{}', ?)", (text, when))
    for i, when in enumerate(("2026-10-01T01:10:00Z", "2026-10-01T01:40:00Z", "2026-10-01T02:10:00Z"), 1):
        db.execute("INSERT INTO assistant_actions (id, chat_id, tool, args, what, undo, status, created_at) "
                   "VALUES (?, 'c1', 't', '{}', 'w', '{}', 'done', ?)", (i, when))
    db.migrate([m for m in load_migrations() if m.version <= 19])
    rows = db.fetchall("SELECT req_text FROM assistant_actions ORDER BY id")
    assert [r["req_text"] for r in rows] == ["Раз", "Раз", "Два"]
    assert len({r["req_id"] for r in db.fetchall("SELECT req_id FROM assistant_actions")}) == 2
