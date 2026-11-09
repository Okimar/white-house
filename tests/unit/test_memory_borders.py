"""Memory++ M2: the no-model cutter on hand-made Russian dialogues, and the model judge's request and answer check."""

from __future__ import annotations

import importlib

borders = importlib.import_module("tavern.builtin_plugins.memory.borders")


def msg(i, role, text, name=None, when="2026-01-01T10:00:00+00:00"):
    return {"id": f"m{i}", "role": role, "content": text, "name": name or ("Ann" if role == "assistant" else "Игрок"),
            "depth": i, "created_at": when, "character_id": "ann" if role == "assistant" else None}


def dialogue(n, first="Таверна полна народа, Ann подаёт эль.", user="Я заказываю ещё эль и хлеб."):
    out = []
    for i in range(n):
        out.append(msg(i, "user" if i % 2 == 0 else "assistant", user if i % 2 == 0 else first))
    return out


def gap(gaps, index):
    return next(g for g in gaps if g.index == index)


def test_explicit_markers_are_strong_and_only_gaps_before_a_user_message_may_cut():
    chat = dialogue(20)
    chat[10] = msg(10, "user", "*** Глава вторая. Я выхожу на улицу.")
    gaps = borders.score_gaps(chat)
    assert gap(gaps, 10).score >= 1.0 and gap(gaps, 10).cut_ok
    assert not gap(gaps, 11).cut_ok  # before an assistant message
    cut = borders.choose(gaps, len(chat), 8)
    assert cut is not None and cut.index == 10


def test_marker_in_the_reply_after_the_user_message_counts_for_the_gap_before_the_user_message():
    chat = dialogue(20)
    chat[11] = msg(11, "assistant", "Тем временем в замке зажгли огни.")
    assert gap(borders.score_gaps(chat), 10).score >= 0.8


def test_time_words_at_the_start_are_strong_and_in_the_middle_are_nothing():
    chat = dialogue(20)
    chat[12] = msg(12, "user", "На следующий день я просыпаюсь поздно.")
    chat[14] = msg(14, "user", "Я говорю, что это было позже и утром тоже.")
    gaps = borders.score_gaps(chat)
    assert gap(gaps, 12).score >= 0.8
    assert gap(gaps, 14).score < 0.5


def test_ooc_is_never_a_border():
    chat = dialogue(20)
    chat[10] = msg(10, "user", "((Глава новая, ООС: пойду пить чай))")
    gaps = borders.score_gaps(chat)
    assert gap(gaps, 10).score == 0 and not gap(gaps, 10).cut_ok


def test_cast_change_is_medium_and_a_cameo_is_not():
    chat = []
    for i in range(8):
        chat.append(msg(i, "user" if i % 2 == 0 else "assistant",
                        "Гарольд и Мирна пьют в таверне Дракон." if i % 2 else "Я смотрю на Гарольда и Мирну."))
    for i in range(8, 16):
        chat.append(msg(i, "user" if i % 2 == 0 else "assistant",
                        "Король Эдмунд ждёт в замке Орлов." if i % 2 else "Я иду к Эдмунду в замок Орлов."))
    gaps = borders.score_gaps(chat)
    assert gap(gaps, 8).score >= borders.CAST - 1e-9
    assert any("лица" in r for r in gap(gaps, 8).reasons)
    cameo = [msg(i, "user" if i % 2 == 0 else "assistant", "Мы пьём вино в таверне Дракон.") for i in range(16)]
    cameo[9] = msg(9, "assistant", "Мимо прошёл Бенедикт и кивнул.")
    assert not any("лица" in r for r in gap(borders.score_gaps(cameo), 8).reasons)


def test_cohesion_valley_with_russian_endings():
    chat = []
    for i in range(10):
        chat.append(msg(i, "user" if i % 2 == 0 else "assistant",
                        "Рыцарь точит меч, меч блестит, рыцари чинят доспехи." if i % 2 else "Я чищу доспех и меч рыцаря."))
    for i in range(10, 20):
        chat.append(msg(i, "user" if i % 2 == 0 else "assistant",
                        "Повар варит суп, суп кипит, повара режут овощи." if i % 2 else "Я солю суп и режу овощ повара."))
    gaps = borders.score_gaps(chat)
    best = max((g for g in gaps if g.cut_ok), key=lambda g: g.score)
    assert best.index == 10 and any("тем" in r for r in best.reasons)


def test_long_real_time_pause_is_weak():
    chat = dialogue(20)
    chat[10] = msg(10, "user", "Я заказываю ещё эль и хлеб.", when="2026-01-02T10:00:00+00:00")
    g = gap(borders.score_gaps(chat), 10)
    assert "долгая пауза" in g.reasons and g.score <= 0.2
    assert borders.choose(borders.score_gaps(chat), 20, 8) is None  # weak alone never closes a chapter


def test_below_n_nothing_closes_and_the_minimum_length_is_kept():
    chat = dialogue(20)
    chat[2] = msg(2, "user", "*** Новая сцена")
    gaps = borders.score_gaps(chat)
    assert borders.choose(gaps, 20, 40) is None  # shorter than N
    assert borders.choose(gaps, 20, 12) is None or borders.choose(gaps, 20, 12).index >= borders.min_length(12)


def test_the_earliest_of_near_equal_markers_wins():
    chat = dialogue(30)
    chat[10] = msg(10, "user", "*** один")
    chat[20] = msg(20, "user", "*** два")
    assert borders.choose(borders.score_gaps(chat), 30, 12).index == 10


def test_forced_cut_at_three_n_even_without_any_sign():
    chat = dialogue(40)
    cut = borders.choose(borders.score_gaps(chat), 40, 8)
    assert cut is not None and cut.forced and cut.index <= 24 and cut.index % 2 == 0  # before a user message


def test_vector_drift_counts_only_where_vectors_exist():
    chat = dialogue(20, first="одно и то же слово", user="одно и то же слово")
    near, far = [1.0, 0.0], [0.0, 1.0]
    from array import array
    vectors = [array("f", near if i < 10 else far) for i in range(20)]
    with_vectors = borders.score_gaps(chat, vectors=vectors)
    assert any("векторы" in r for r in gap(with_vectors, 10).reasons)
    assert not any("векторы" in r for g in borders.score_gaps(chat) for r in g.reasons)


def test_judge_request_numbers_messages_and_answer_is_checked():
    chat = dialogue(30)
    gaps = borders.score_gaps(chat)
    sent = borders.judge_messages(chat, [gap(gaps, 12)])
    assert "[1] " in sent[1].content and "[30] " in sent[1].content and "numbered: 12" in sent[1].content
    assert "one place" in sent[0].content  # the scene definition is in the prompt
    ok = borders.judge_answer({"finished": True, "ends_at": 15, "reason": "сцена кончилась"}, chat, 12)
    assert ok is not None and ok.index == 15 and ok.reason == "сцена кончилась"
    assert borders.judge_answer({"finished": False}, chat, 12) is None
    assert borders.judge_answer({"finished": True, "ends_at": 999}, chat, 12) is None  # not a message of the part
    assert borders.judge_answer({"finished": True, "ends_at": 30}, chat, 12) is None  # nothing would be left
    assert borders.judge_answer({"finished": True, "ends_at": 1}, chat, 12) is None  # too early
