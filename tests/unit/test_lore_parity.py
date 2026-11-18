"""Wave 2 of the SillyTavern parity plan: lorebook engine precision (docs/ST_PARITY.md section 5).

Each test writes the expected entries by hand from the plain-words rule: decorators, triggers per kind of
generation, per-entry scan sources, delay until recursion, group scoring, timers counted in chat messages for
entries from SillyTavern files, minimum activations, the book tiers and the order of the found list.
"""

from __future__ import annotations

import random

from tavern.core.retrieval import select_blocks, split_decorators
from tavern.formats import st_lorebook
from tavern.services.lorebooks import (DEFAULT_SETTINGS, LorebookError, clean_settings, scan_lines,
                                       _with_key_macros)

import pytest


def st_block(uid: int, content: str, keys=(), *, owner: str = "book", **entry) -> dict:
    """An entry of a SillyTavern world file, the way the import stores it."""
    raw = {"uid": uid, "key": list(keys), "content": content, "constant": not keys, "disable": False,
           "order": entry.pop("order", 100), "position": 0, **entry}
    return {"id": f"b{uid}", "parent_id": None, "owner_id": owner, **st_lorebook.block_from_entry(raw, uid)}


def own(id_: str, content: str, keys=(), **more) -> dict:
    """One of our own bubbles (not from a SillyTavern file)."""
    return {"id": id_, "title": id_, "content": content, "mode": "search" if keys else "always",
            "parent_id": None, "keywords": list(keys), "sticky": 0, "position": 0, **more}


def found(blocks, scan="", **kw) -> list[str]:
    kw.setdefault("scan_messages", [scan] if scan else [])
    kw.setdefault("by_order", True)
    kw.setdefault("rng", random.Random(1))
    selection = select_blocks(blocks, owner_name="", scan_text=scan, **kw)
    return [f.block["id"] for f in selection.found]


# Decorators


def test_decorators_are_cut_from_the_text_and_decide_activation():
    assert split_decorators("@@activate\nText") == ({"activate"}, "Text")
    assert split_decorators("plain") == (set(), "plain")
    always = st_block(1, "@@activate\nALWAYS", ["never-said"])
    never = st_block(2, "@@dont_activate\nNEVER", ["river"])
    selection = select_blocks([always, never], owner_name="", scan_text="the river", scan_messages=["the river"],
                              by_order=True)
    assert [f.block["id"] for f in selection.found] == ["b1"]
    assert selection.found[0].block["content"] == "ALWAYS"  # the decorator line never reaches the model


# Triggers


def test_an_entry_with_triggers_goes_only_for_those_kinds_of_generation():
    entry = st_block(1, "SWIPE ONLY", ["river"], triggers=["swipe"])
    plain = st_block(2, "ANY", ["river"])
    assert found([entry, plain], "a river", kind="normal") == ["b2"]
    assert found([entry, plain], "a river", kind="swipe") == ["b2", "b1"]  # equal orders: reversed, as ST
    assert found([entry, plain], "a river", kind=None) == ["b2", "b1"]  # not checked when the caller has no kind


# Scan sources


def test_an_entry_may_look_in_the_card_and_persona_besides_the_messages():
    entry = st_block(1, "CASTLE", ["castle"], matchScenario=True)
    blind = st_block(2, "CASTLE TOO", ["castle"])
    sources = {"scenario": "They live in a castle.", "persona": ""}
    assert found([entry, blind], "hello", sources=sources) == ["b1"]
    assert found([entry, blind], "hello", sources={"description": "a castle"}) == []  # another source


# Delay until recursion


def test_delay_until_recursion_waits_for_a_chain_step():
    starter = st_block(1, "The dragon sleeps.", ["cave"])
    delayed = st_block(2, "DRAGON LORE", ["dragon"], delayUntilRecursion=True)
    assert found([starter, delayed], "the dragon cave", recursion=0) == ["b1"]  # the message alone never opens it
    assert found([starter, delayed], "the dragon cave", recursion=2) == ["b2", "b1"]


# Group scoring


def test_group_scoring_keeps_the_entry_with_the_most_keywords_found():
    one = st_block(1, "ONE KEY", ["sword"], group="weapons", order=100)
    two = st_block(2, "TWO KEYS", ["sword", "steel"], group="weapons", order=100, useGroupScoring=True)
    scan = "a steel sword"
    assert found([one, two], scan, group_scoring=True) == ["b2"]


# Timers of SillyTavern entries count chat messages


def test_sticky_of_a_sillytavern_entry_counts_messages_ours_counts_replies():
    st = st_block(1, "STICKY", ["river"], sticky=2)
    assert found([st], "nothing here", hits={"b1": 10}, clock=11, generation=99) == ["b1"]
    assert found([st], "nothing here", hits={"b1": 10}, clock=13, generation=99) == []
    ours = own("o", "OURS", ["river"], sticky=2)
    assert found([ours], "nothing here", hits={"o": 5}, clock=999, generation=6, by_order=False) == ["o"]


# Minimum activations


def test_minimum_activations_scan_deeper_until_enough_entries_are_found():
    old = st_block(1, "OLD", ["harbour"])
    messages = ["we met at the harbour", "x", "y"]
    assert found([old], "x\ny", scan_messages=messages, scan_depth=2) == []
    assert found([old], "x\ny", scan_messages=messages, scan_depth=2, min_found=1) == ["b1"]


# Book tiers and the order of the found list


def test_found_entries_go_lowest_order_first_and_the_budget_keeps_the_higher_tier():
    chat_book = st_block(1, "CHAT " * 40, ["river"], order=10, owner="chat")
    global_book = st_block(2, "GLOBAL " * 40, ["river"], order=50, owner="global")
    tiers = {"chat": 0, "global": 3}
    assert found([global_book, chat_book], "river", tiers=tiers) == ["b1", "b2"]  # text order: Order 10, then 50
    # a budget for one entry keeps the chat's book, though the global one has the higher «Order»
    assert found([global_book, chat_book], "river", tiers=tiers, budget_tokens=60) == ["b1"]


def test_equal_order_entries_go_in_reverse_of_the_book_like_sillytavern():
    """The owner's book «Сильваннета» (2026-11-17 exam): four constant entries at depth 1, all Order 100, uids 5, 28,
    29, 35 in the book. SillyTavern sends them 35, 29, 28, 5 (it walks the activated entries highest order first and
    puts each in front). This order matters to the owner's jailbreak: never change it."""
    book = [st_block(uid, f"E{uid}", depth=1, order=100) for uid in (5, 28, 29, 35)]
    assert found(book) == ["b35", "b29", "b28", "b5"]
    mixed = [st_block(1, "LOW", order=10), st_block(2, "A", order=50), st_block(3, "B", order=50)]
    assert found(mixed) == ["b1", "b3", "b2"]  # lowest order first; inside one order: reversed


# Settings and scan text


def test_settings_default_to_sillytaverns_and_are_checked():
    # the book strategy is ours: «evenly», a book has no weight, only the entries' numbers (owner, 2026-11-19);
    # an import of SillyTavern's settings still brings its own choice (test_st_settings_lore)
    assert (DEFAULT_SETTINGS["recursion"], DEFAULT_SETTINGS["scan_messages"], DEFAULT_SETTINGS["include_names"],
            DEFAULT_SETTINGS["strategy"]) == (0, 2, True, "evenly")
    assert clean_settings({"strategy": "evenly", "include_names": False}) == {"strategy": "evenly",
                                                                            "include_names": False}
    for bad in ({"strategy": "random"}, {"include_names": 1}, {"scan_messages": -1}):
        with pytest.raises(LorebookError):
            clean_settings(bad)


def test_the_scan_reads_names_like_sillytavern():
    history = [{"role": "user", "content": "hi", "name": ""}, {"role": "assistant", "content": "yo", "name": "Ann"},
               {"role": "user", "content": "gone", "name": "", "exclude_from_prompt": True}]
    assert scan_lines(history, names=True, user="Kira") == ["Kira: hi", "Ann: yo"]
    assert scan_lines(history, names=False, user="Kira") == ["hi", "yo"]


def test_macros_in_keywords_are_filled_but_the_bubble_stays_as_written():
    block = own("m", "MINE", ["{{char}}'s sword"], rules={"keys2": ["{{user}}"]})
    filled = _with_key_macros([block], char="Ann", user="Kira")[0]
    assert filled["keywords"] == ["Ann's sword"] and filled["rules"]["keys2"] == ["Kira"]
    assert block["keywords"] == ["{{char}}'s sword"]


# SillyTavern's global «whole words» (the owner's exam, 2026-11-17: «kisses» woke The Garden's «kiss» entries in ours)


def test_global_whole_words_makes_latin_keys_of_st_entries_exact_and_leaves_russian_by_root():
    from tavern.core.retrieval import with_latin_whole
    dragoon = st_block(1, "DRAGOON", ["dragoon"])
    hold = st_block(2, "HOLD", ["hold hands"])
    sword = st_block(3, "SWORD", ["меч"])
    mine = own("o1", "OURS", ["dragoon"])
    says_no = st_block(4, "OPT OUT", ["dragoon"], matchWholeWords=False)
    text = "Dragoons were holding hands, and she draws her меча."
    blocks = [dragoon, hold, sword, mine, says_no]
    assert set(found(blocks, text)) == {"b1", "b2", "b3", "o1", "b4"}  # off: English endings are allowed
    # on: «dragoon» is not in «Dragoons»; a phrase stays loose as in ST («hold hands» checked as text, endings kept)
    assert set(found(with_latin_whole(blocks, True), text)) == {"b2", "b3", "o1", "b4"}
    assert set(found(with_latin_whole(blocks, True), "a dragoon, hold hands")) == {"b1", "b2", "o1", "b4"}
    assert set(found(with_latin_whole(blocks, True), "proud Ajin tribes")) == set()
    tribe = st_block(5, "TRIBE", ["Ajin tribe"])
    assert found(with_latin_whole([tribe], True), "proud Ajin tribes") == ["b5"]
    assert "latin_whole" not in (dragoon.get("rules") or {})  # the stored bubble is never changed


def test_whole_words_is_a_lorebook_setting():
    assert DEFAULT_SETTINGS["whole_words"] is False
    assert clean_settings({"whole_words": True}) == {"whole_words": True}
    with pytest.raises(LorebookError):
        clean_settings({"whole_words": "yes"})


def test_a_card_bubble_that_repeats_a_chosen_lorebook_entry_word_for_word_is_left_out():
    from types import SimpleNamespace as NS

    from tavern.services.lorebooks import drop_repeated_bubbles

    def hit(id_, text):
        return NS(block={"id": id_, "content": text}, path=f"Card › {id_}")

    celia, own_only = hit("card-celia", "## Celia\nReceptionist.\n"), hit("card-own", "Only on the card.")
    member = NS(blocks=[celia, own_only])
    assert drop_repeated_bubbles([member], [hit("book-celia", "## Celia\nReceptionist.")]) == ["Card › card-celia"]
    assert member.blocks == [own_only]
    assert drop_repeated_bubbles([member], []) == [] and member.blocks == [own_only]
