"""The message the user just sent appears at once (web/core/pending.js, wired in core/chat.js, Message.js and
VirtualList.js). The pure helpers run in Node; the wiring is checked in the source, like the other page tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def run_module(code: str) -> object:
    result = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, timeout=30, cwd=WEB)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def pending(script: str) -> object:
    return run_module('import * as p from "./core/pending.js";' + script)


CHAT = [{"id": "m1", "depth": 0, "role": "assistant", "content": "hi"}]


@needs_node
def test_the_temporary_message_is_the_users_last_one_with_text_name_and_pictures() -> None:
    got = pending(f"console.log(JSON.stringify(p.makePending({json.dumps(CHAT)}, "
                  "{text: 'hello', images: ['a.png'], name: 'Ann'})))")
    assert got["role"] == "user" and got["content"] == "hello" and got["name"] == "Ann"
    assert got["pending"] is True and got["id"].startswith("pending:") and got["vkey"] == got["id"]
    assert got["depth"] == 1 and got["parent_id"] == "m1"
    assert got["swipe_meta"] == {"images": ["a.png"]}


@needs_node
def test_the_real_message_takes_the_place_and_the_list_identity_of_the_temporary_one() -> None:
    got = pending(
        f"const t = p.makePending({json.dumps(CHAT)}, {{text: 'hello', name: 'Ann'}});"
        f"const list = [...{json.dumps(CHAT)}, t];"
        "const real = {id: 'u1', role: 'user', depth: 1, content: 'hello', name: 'Ann'};"
        "const out = p.settle(list, real);"
        "console.log(JSON.stringify({out, vkey: t.vkey}))"
    )
    out = got["out"]
    assert [m["id"] for m in out] == ["m1", "u1"]
    assert out[1]["vkey"] == got["vkey"] and not out[1].get("pending")


@needs_node
def test_settling_twice_or_with_a_reload_in_between_leaves_one_copy() -> None:
    got = pending(
        f"const t = p.makePending({json.dumps(CHAT)}, {{text: 'x'}});"
        "const real = {id: 'u1', role: 'user', depth: 1, content: 'x'};"
        # a reload brought the real message first, then the event arrives with the temporary one still shown
        f"const list = [...{json.dumps(CHAT)}, real, t];"
        "console.log(JSON.stringify(p.settle(list, real).map((m) => m.id)))"
    )
    assert got == ["m1", "u1"]


@needs_node
def test_a_message_that_is_not_the_users_does_not_take_the_temporary_place() -> None:
    got = pending(
        f"const t = p.makePending({json.dumps(CHAT)}, {{text: 'x'}});"
        "const reply = {id: 'a2', role: 'assistant', depth: 2, content: ''};"
        f"console.log(JSON.stringify(p.settle([...{json.dumps(CHAT)}, t], reply).map((m) => m.id)))"
    )
    assert got[0] == "m1" and got[1].startswith("pending:") and got[2] == "a2"


@needs_node
def test_without_a_temporary_message_settling_adds_or_replaces_by_id_as_before() -> None:
    got = pending(
        f"const a = p.settle({json.dumps(CHAT)}, {{id: 'u1', role: 'user', depth: 1, content: 'x'}});"
        "const b = p.settle(a, {id: 'u1', role: 'user', depth: 1, content: 'y'});"
        "console.log(JSON.stringify(b.map((m) => m.id + ':' + m.content)))"
    )
    assert got == ["m1:hi", "u1:y"]


@needs_node
def test_a_failed_send_removes_only_the_temporary_message() -> None:
    got = pending(
        f"const t = p.makePending({json.dumps(CHAT)}, {{text: 'x'}});"
        f"const list = [...{json.dumps(CHAT)}, t];"
        "console.log(JSON.stringify({one: p.dropPending(list, t.id).map((m) => m.id),"
        " all: p.dropPending(list).map((m) => m.id), none: p.dropPending(list, 'pending:0') === list,"
        f" clean: (() => {{ const l = {json.dumps(CHAT)}; return p.dropPending(l) === l; }})()}}))"
    )
    assert got["one"] == ["m1"] and got["all"] == ["m1"] and got["none"] is True
    assert got["clean"] is True  # nothing to drop: the same list comes back (no needless render)


def source(path: str) -> str:
    return (WEB / path).read_text(encoding="utf-8")


def test_send_shows_the_message_first_and_removes_it_when_the_send_fails() -> None:
    chat = source("core/chat.js")
    body = chat[chat.index("send: async"):chat.index("// speaker: the character who writes it")]
    assert body.index("makePending") < body.index("api.post(")
    assert "dropPending(s.messages, temp.id)" in body and "result === false" in body
    assert "settle(s.messages, r.message)" in body  # a chat with no run (manual order) confirms by the answer
    # the run's events confirm the user's message in place
    assert chat.count("settle(") >= 3
    assert "dropPending(s.messages)" in chat  # a run that ends without confirming leaves nothing behind


def test_the_list_keeps_the_row_when_the_real_message_replaces_the_temporary_one() -> None:
    lst = source("components/VirtualList.js")
    assert "item.vkey || item.id" in lst and "key=${keyOf(item)}" in lst and "data-id=${keyOf(item)}" in lst
    assert "stickTo" in lst
    assert "stickTo=${" in source("main.js")


def test_the_temporary_message_has_no_actions_that_reach_the_server() -> None:
    msg = source("components/Message.js")
    for needle in ("canRegenerate = isLast && !busy && !pending", "disabled=${busy || pending}",
                   'inert=${pending ? true : null}', "streaming || pending ? [] : plugins.messageButtons"):
        assert needle in msg


def test_the_composer_clears_at_once_and_gets_the_text_back_on_failure() -> None:
    composer = source("components/Composer.js")
    submit = composer[composer.index("const submit = async"):composer.index("const attach")]
    assert submit.index('setText("")') < submit.index("await onSend(")
    assert "sent === false" in submit and "setText((current) => current || value)" in submit
