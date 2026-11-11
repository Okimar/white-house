"""Memory++ M6, pure parts of «Лица»: names found by stem (any ending, every word of the name), the same person, the
recurrence rule of a new side character (two messages of the chapter, or one and an earlier one), the request text."""

from __future__ import annotations

from tavern.builtin_plugins.memory import faces, prompts


def stems(text: str) -> frozenset[str]:
    return faces.word_stems(text)


def test_a_name_is_found_with_any_ending_and_all_its_words():
    assert faces.mentioned(["Борин"], stems("Налей, Борину скажи"))
    assert faces.mentioned(["Старый Том"], stems("старого Тома нет"))
    assert not faces.mentioned(["Старый Том"], stems("Том ушёл"))
    assert faces.mentioned(["Борин", "трактирщик"], stems("трактирщика позвали"))
    assert not faces.mentioned(["Борин"], stems("бор и лес"))


def test_the_same_person_by_name_or_alias():
    face = {"name": "Борин", "aliases": ["трактирщик"]}
    assert faces.same_person(face, "Борина") and faces.same_person(face, "Трактирщик")
    assert not faces.same_person(face, "Грег") and not faces.same_person(face, "")


def test_a_new_face_must_recur():
    two = [{"content": "Грег пришёл"}, {"content": "и Грег ушёл"}]
    one = [{"content": "Грег пришёл"}, {"content": "тишина"}]
    assert faces.recurs(["Грег"], two, lambda: frozenset())
    assert not faces.recurs(["Грег"], one, lambda: frozenset())  # a cameo
    assert faces.recurs(["Грег"], one, lambda: stems("вчера Грега видели"))  # back from an earlier chapter


def test_the_request_lists_main_and_known_side_characters():
    messages, scene = prompts.face_messages(["№1 Ann: Борин налил"], ["Ann", "User"],
                                            [{"code": "F1", "name": "Борин", "aliases": ["трактирщик"],
                                              "card": "хозяин", "locked": True}], 200)
    system, user = messages[0].content, messages[1].content
    assert "side characters" in system and "at most 200 tokens" in system and "(here: Russian)" in system
    assert "Main characters: Ann, User" in user and "F1: Борин (also: трактирщик) [locked]\nCard: хозяин" in user
    assert scene == "№1 Ann: Борин налил"
