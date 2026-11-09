"""Memory++ M1: which messages become pieces, how long messages are cut, keywords, and which message of each
speaker is left for later (the owner's rules)."""

from __future__ import annotations

import importlib

from tavern.core.prompt import estimate_tokens

pieces = importlib.import_module("tavern.builtin_plugins.memory.pieces")


def msg(id_, role="assistant", content="текст", **extra):
    return {"id": id_, "role": role, "content": content, "hidden": False, "exclude_from_prompt": False, "owner": "",
            "character_id": extra.pop("character_id", "ann" if role == "assistant" else None), "name": "x", **extra}


def test_only_real_conversation_text_counts():
    assert pieces.eligible(msg("a"))
    assert pieces.eligible(msg("u", role="user"))
    assert not pieces.eligible(msg("s", role="system"))
    assert not pieces.eligible(msg("h", hidden=True))
    assert not pieces.eligible(msg("x", exclude_from_prompt=True))
    assert not pieces.eligible(msg("o", owner="dice"))  # a plugin's service message
    assert not pieces.eligible(msg("e", content="  \n"))


def test_thinking_and_notes_are_never_read():
    message = msg("a", content="Она кивнула.", swipe_meta={"reasoning": "СЕКРЕТНЫЕ МЫСЛИ"},
                  notes=[{"text": "ЗАМЕТКА"}], extra={"note": "ЗАМЕТКА"})
    [spec] = pieces.specs_for(message)
    assert spec.text == "Она кивнула."
    assert "МЫСЛИ" not in " ".join(spec.keywords) and "заметк" not in " ".join(spec.keywords)


def test_the_latest_message_of_each_speaker_is_found():
    path = [msg("1", "assistant", character_id="ann"), msg("2", "user"), msg("3", "assistant", character_id="bob"),
            msg("4", "assistant", character_id="ann"), msg("5", "user"), msg("6", "assistant", character_id="bob")]
    assert pieces.latest_ids(path) == {"4", "5", "6"}


def test_the_latest_ignores_messages_that_do_not_count():
    path = [msg("1", "user"), msg("2", "user", exclude_from_prompt=True), msg("3", "assistant", character_id="ann"),
            msg("4", "assistant", character_id="ann", hidden=True)]
    assert pieces.latest_ids(path) == {"1", "3"}


def test_a_short_message_is_one_piece_and_the_hash_follows_the_text():
    [a] = pieces.specs_for(msg("a", content="Привет, мир."))
    [b] = pieces.specs_for(msg("a", content="Привет, мир."))
    [c] = pieces.specs_for(msg("a", content="Привет, мир!"))
    assert a.part == 0 and a.hash == b.hash and a.hash != c.hash


def test_a_long_message_is_cut_along_sentences_into_parts_of_about_the_target():
    text = " ".join(f"Это предложение номер {n}, оно довольно длинное и ни о чём." for n in range(120))
    parts = pieces.cut(text)
    assert len(parts) > 3
    assert all(estimate_tokens(p) <= pieces.MAX_TOKENS for p in parts)
    assert all(p.endswith(".") for p in parts)  # cut at sentence ends
    assert " ".join(parts).split() == text.split()  # nothing lost
    specs = pieces.specs_for(msg("a", content=text))
    assert [s.part for s in specs] == list(range(len(specs)))


def test_one_huge_sentence_is_cut_by_size():
    parts = pieces.cut("слово " * 2000)
    assert len(parts) > 3 and all(estimate_tokens(p) <= pieces.MAX_TOKENS for p in parts)


def test_paragraphs_are_kept_apart_when_cutting():
    paragraph = "Предложение. " * 60
    parts = pieces.cut(paragraph + "\n\n" + paragraph + "\n\n" + paragraph)
    assert len(parts) >= 2


def test_keywords_are_stems_names_and_numbers_are_starred():
    words = pieces.keywords("Вчера Мария купила яблоки за 15 монет у торговца Ивана.")
    assert any(w.startswith("*мар") for w in words) and any(w.startswith("*иван") for w in words)
    assert "*15" in words
    assert any(w.lstrip("*").startswith("яблок") for w in words)
    assert not any(w.lstrip("*") in ("за", "у") for w in words)  # stop and short words


def test_a_capital_at_the_start_of_a_sentence_is_not_a_name():
    words = pieces.keywords("Дождь шёл. Холодно было.")
    assert not any(w.startswith("*") for w in words)


def test_specs_follow_chat_order_and_skip_what_does_not_count():
    path = [msg("1", "user", "первое"), msg("2", "assistant", "второе", hidden=True), msg("3", "assistant", "третье")]
    assert [s.message_id for s in pieces.specs(path)] == ["1", "3"]
