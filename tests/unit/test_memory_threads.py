"""Memory++ M5, pure parts of «Слово и нити»: who may get a record (own, given to them, the persona's to them or to
everyone, secrets only to knowers, «Помнит всё»), a quote must really be in a message, the request texts (numbered
scene under the persona's name, quests only with their toggle, open records), and an outside IDF corpus for a few
records."""

from __future__ import annotations

import pytest

from tavern.builtin_plugins.memory import prompts, search, threads

PERSONA = {"user", "me1"}


def rec(kind: str, who: str, to: str = "") -> dict:
    return {"kind": kind, "who": who, "to_whom": to}


@pytest.mark.parametrize("row, knows, speakers, remembers_all, expected", [
    (rec("vow", "ann"), [], {"ann"}, False, True),                      # own
    (rec("vow", "ann"), [], {"bob"}, False, False),                     # another's vow
    (rec("promise", "user", "ann"), [], {"ann"}, False, True),          # «ты же обещал»
    (rec("promise", "user", "ann"), [], {"bob"}, False, False),         # given to someone else
    (rec("claim", "user", ""), [], {"bob"}, False, True),               # the persona to everyone
    (rec("claim", "ann", ""), [], {"bob"}, False, False),               # a character to everyone: only theirs
    (rec("debt", "bob", "ann"), [], {"ann"}, False, True),              # owed to her
    (rec("secret", "ann", "user"), ["ann", "user"], {"bob"}, False, False),
    (rec("secret", "ann", "user"), ["ann", "user", "bob"], {"bob"}, False, True),
    (rec("secret", "ann", "user"), ["ann", "user"], PERSONA, False, True),
    (rec("secret", "ann", "user"), ["ann", "user"], {"bob"}, True, True),  # «Помнит всё»
])
def test_who_may_get_a_record(row, knows, speakers, remembers_all, expected):
    assert threads.may_know(row, knows, speakers, PERSONA, remembers_all=remembers_all) is expected


def test_a_quote_must_really_be_in_a_message():
    messages = [{"id": "a", "content": "Я обещаю  найти твой «караван» до весны. Клянусь."},
                {"id": "b", "content": "Ёлки, я никогда не предам друга!"}]
    assert threads.find_message("я обещаю найти твой караван", None, messages)["id"] == "a"
    assert threads.find_message("Елки, я никогда", messages[0], messages)["id"] == "b"  # the named one was wrong
    assert threads.find_message("Я дочь короля", None, messages) is None


def test_the_request_numbers_the_scene_under_the_personas_name_and_quests_need_their_toggle():
    messages = [{"depth": 2, "role": "user", "name": None, "content": "Я обещаю."},
                {"depth": 3, "role": "assistant", "name": "Ann", "content": "Хорошо."}]
    lines = prompts.thread_lines(messages, "Игорь")
    assert lines == ["№3 Игорь: Я обещаю.", "№4 Ann: Хорошо."]
    people = [{"code": "C1", "name": "Ann"}, {"code": "C2", "name": "Игорь"}]
    opened = [{"code": "T1", "kind": "promise", "who": "Игорь", "to": "Ann", "quote": "Я обещаю.", "status": "active"}]
    system, user = (m.content for m in prompts.thread_messages(lines, people, opened, quests=False))
    assert "quest:" not in system and "EXACT words" in system
    assert "T1: promise of Игорь to Ann (active): «Я обещаю.»" in user and "C2: Игорь" in user
    system, _ = (m.content for m in prompts.thread_messages(lines, people, [], quests=True))
    assert "quest:" in system


def test_a_few_records_weigh_their_words_against_the_chats_pieces():
    record = search.Candidate("t:1", "m1", 0, "h", 5, ("караван", "весн"))
    alone = search.score([record], {}, [], {"караван"}, 0.5)
    assert alone == []  # one candidate is no corpus: its words weigh almost nothing
    corpus = [search.Candidate(f"p{i}", f"m{i}", 0, "h", 5, ("эль", "номер")) for i in range(20)]
    found = search.score([record], {}, [], {"караван"}, 0.5, idf=search.idf_table([record, *corpus]))
    assert [s.candidate.piece_id for s in found] == ["t:1"]
