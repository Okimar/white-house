import pytest

from tavern.errors import Conflict, NotFound


@pytest.fixture
def chat(ctx):
    return ctx.chats.create_chat(character_id=None, title="t")


def add(ctx, chat, text, role="user", parent="leaf", **kw):
    parent_id = ctx.chats.get_chat(chat["id"])["leaf_id"] if parent == "leaf" else parent
    return ctx.chats.add_message(chat["id"], role=role, content=text, parent_id=parent_id, **kw)


def test_messages_form_a_path_ordered_by_depth(ctx, chat):
    a = add(ctx, chat, "a")
    b = add(ctx, chat, "b", role="assistant")
    c = add(ctx, chat, "c")
    path = ctx.chats.active_path(chat["id"])
    assert [m["content"] for m in path] == ["a", "b", "c"]
    assert [m["depth"] for m in path] == [0, 1, 2]
    assert ctx.chats.get_chat(chat["id"])["leaf_id"] == c["id"]
    assert ctx.chats.path_length(chat["id"]) == 3
    assert a["parent_id"] is None and b["parent_id"] == a["id"]


def test_paging_returns_newest_first_window_in_order(ctx, chat):
    for i in range(10):
        add(ctx, chat, f"m{i}")
    newest = ctx.chats.active_path(chat["id"], limit=3)
    assert [m["content"] for m in newest] == ["m7", "m8", "m9"]
    older = ctx.chats.active_path(chat["id"], before_depth=7, limit=3)
    assert [m["content"] for m in older] == ["m4", "m5", "m6"]


def test_branches_are_separate_paths(ctx, chat):
    root = add(ctx, chat, "root")
    first = add(ctx, chat, "branch one", parent=root["id"])
    second = add(ctx, chat, "branch two", parent=root["id"])  # leaf moves to the new branch
    assert [m["content"] for m in ctx.chats.active_path(chat["id"])] == ["root", "branch two"]
    ctx.chats.set_leaf(chat["id"], first["id"])
    assert [m["content"] for m in ctx.chats.active_path(chat["id"])] == ["root", "branch one"]
    assert second["id"] != first["id"]


def test_swipes_add_select_and_edit(ctx, chat):
    message = add(ctx, chat, "first", role="assistant")
    idx = ctx.chats.add_swipe(message["id"], "second")
    assert idx == 1
    current = ctx.chats.get_message(message["id"])
    assert (current["content"], current["active_swipe"], current["swipe_count"]) == ("second", 1, 2)

    assert ctx.chats.set_active_swipe(message["id"], 0)["content"] == "first"
    ctx.chats.set_content(message["id"], "edited")
    assert ctx.chats.get_message(message["id"])["content"] == "edited"
    with pytest.raises(Conflict):
        ctx.chats.set_active_swipe(message["id"], 5)


def test_remove_last_swipe_keeps_at_least_one(ctx, chat):
    message = add(ctx, chat, "only", role="assistant")
    with pytest.raises(Conflict):
        ctx.chats.remove_last_swipe(message["id"])
    ctx.chats.add_swipe(message["id"], "extra")
    ctx.chats.remove_last_swipe(message["id"])
    after = ctx.chats.get_message(message["id"])
    assert (after["swipe_count"], after["active_swipe"], after["content"]) == (1, 0, "only")


def test_delete_message_removes_subtree_and_moves_leaf(ctx, chat):
    a = add(ctx, chat, "a")
    b = add(ctx, chat, "b")
    add(ctx, chat, "c")
    ctx.chats.delete_message(b["id"])
    assert ctx.chats.get_chat(chat["id"])["leaf_id"] == a["id"]
    assert [m["content"] for m in ctx.chats.active_path(chat["id"])] == ["a"]
    assert ctx.db.fetchone("SELECT count(*) FROM messages")[0] == 1
    assert ctx.db.fetchone("SELECT count(*) FROM swipes")[0] == 1


def test_service_flags_are_stored_and_filterable(ctx, chat):
    add(ctx, chat, "visible")
    add(ctx, chat, "secret plan", role="system", hidden=True, exclude_from_prompt=True, owner="agents")
    everything = ctx.chats.active_path(chat["id"])
    assert [m["hidden"] for m in everything] == [False, True]
    assert everything[1]["owner"] == "agents" and everything[1]["exclude_from_prompt"]
    visible = ctx.chats.active_path(chat["id"], include_hidden=False)
    assert [m["content"] for m in visible] == ["visible"]


def test_parent_must_belong_to_the_same_chat(ctx, chat):
    other = ctx.chats.create_chat(character_id=None, title="other")
    foreign = ctx.chats.add_message(other["id"], role="user", content="x")
    with pytest.raises(NotFound):
        ctx.chats.add_message(chat["id"], role="user", content="y", parent_id=foreign["id"])
    with pytest.raises(ValueError):
        ctx.chats.add_message(chat["id"], role="robot", content="z")


def test_deleting_chat_cascades(ctx, chat):
    add(ctx, chat, "a")
    ctx.chats.delete_chat(chat["id"])
    assert ctx.db.fetchone("SELECT count(*) FROM messages")[0] == 0
    assert ctx.db.fetchone("SELECT count(*) FROM swipes")[0] == 0
    with pytest.raises(NotFound):
        ctx.chats.get_chat(chat["id"])


def test_deleting_character_keeps_its_chats(ctx):
    char = ctx.characters.create({"name": "Ann"})
    chat = ctx.chats.create_chat(character_id=char["id"], title="c")
    ctx.characters.delete(char["id"])
    assert ctx.chats.get_chat(chat["id"])["character_id"] is None
