"""Stage 7а: who speaks next in a chat with several characters (services/speakers.py), no I/O."""

from __future__ import annotations

import random

from tavern.services.speakers import choose, defaults, named, scene


def member(cid, name, *, talk=50, muted=False, me=False, blurb=""):
    return {"character_id": cid, "name": name, "talkativeness": talk, "muted": muted, "is_me": me,
            "blurb": blurb}


ANN, BORIS, VERA = member("a", "Аня"), member("b", "Борис Петров"), member("v", "Вера")
ME = member("me", "Кира", me=True)
CAST = [ANN, BORIS, ME, VERA]


def said(text, role="user", cid=None, name=""):
    return {"role": role, "content": text, "character_id": cid, "name": name}


def test_the_named_character_answers_whatever_the_ending():
    settings = defaults()
    assert choose(settings, CAST, [said("Бориса спросите, он знает")]) == "b"
    assert choose(settings, CAST, [said("Вера и Аня, идите сюда")]) == "v"  # the first named
    assert named("Привет, Аня! И ты, Вера", CAST) == ["a", "v"]


def test_a_character_naming_itself_does_not_answer_itself():
    history = [said("Я, Вера, скажу так", "assistant", "v", "Вера")]
    picks = {choose(defaults(), CAST, history, random.Random(seed)) for seed in range(30)}
    assert "v" not in picks and picks <= {"a", "b"}


def test_talkativeness_decides_when_nobody_is_named():
    cast = [member("a", "Аня", talk=100), member("b", "Борис", talk=0), member("v", "Вера", talk=0)]
    assert {choose(defaults(), cast, [said("ну?")], random.Random(s)) for s in range(30)} == {"a"}
    quiet = [member("a", "Аня", talk=0), member("b", "Борис", talk=0)]
    assert {choose(defaults(), quiet, [said("ну?")], random.Random(s)) for s in range(30)} == {"a", "b"}


def test_turns_go_round_skipping_the_muted():
    settings = {**defaults(), "order": "turns"}
    cast = [ANN, {**BORIS, "muted": True}, ME, VERA]
    assert choose(settings, cast, [said("hi")]) == "a"
    assert choose(settings, cast, [said("x", "assistant", "a", "Аня"), said("hi")]) == "v"
    assert choose(settings, cast, [said("x", "assistant", "v", "Вера")]) == "a"
    # older messages without character_id are matched by name
    assert choose(settings, cast, [said("x", "assistant", None, "Аня")]) == "v"


def test_nobody_speaks_when_everyone_is_muted_and_one_character_always_does():
    assert choose(defaults(), [{**ANN, "muted": True}, ME], []) is None
    assert choose(defaults(), [ANN, ME], [said("Вера?")]) == "a"


def test_scene_lists_the_others_as_asked():
    assert scene(CAST, "a", "none") == [] and scene(CAST, "a", "full") == []
    assert scene(CAST, "a", "names") == [("Борис Петров", ""), ("Вера", "")]
    cast = [ANN, {**BORIS, "blurb": "кузнец"}, {**VERA, "muted": True}, ME]
    assert scene(cast, "a", "blurbs") == [("Борис Петров", "кузнец")]


def test_common_words_that_share_a_name_stem_do_not_name_anyone():
    """Regression: «Ник» was found in «никто», «Ли» in «ли», «Вера» in «верно», so that character
    answered almost every message and talkativeness never got a say."""
    cast = [member(n, n) for n in ("Ли", "Ник", "Вера", "Мира")]
    assert named("Не знаю, ли он прав. Никто не пришёл, никогда. Верно, вернулся в мир", cast) == []
    assert named("Ник, иди сюда; Вере скажи, Мирой займусь, Ли тоже", cast) == ["Ник", "Вера", "Мира", "Ли"]
    assert named("Ann's hat and Алисой", [member("a", "Ann"), member("b", "Алиса")]) == ["a", "b"]
    picks = {choose(defaults(), cast, [said("Никто не знает, верно ли это")], random.Random(s)) for s in range(40)}
    assert len(picks) > 1  # nobody is named: talkativeness draws among all four
