"""Macros the SillyTavern way (core/macros.py through core/prompt.py:substitute), one behaviour per test."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tavern.core.macros import format_datetime, humanize
from tavern.core.prompt import substitute

NOW = datetime(2026, 11, 14, 15, 5, 7, tzinfo=timezone(timedelta(hours=3)))  # a Saturday


def sub(text: str, **kw) -> str:
    kw.setdefault("char", "Ann")
    kw.setdefault("user", "Bob")
    kw.setdefault("now", NOW)
    return substitute(text, **kw)


def test_inventory_example_trim_comment_newline():
    assert sub("A{{trim}}\n\nB {{// hidden}}{{newline}}C") == "AB \nC"


@pytest.mark.parametrize("text, expected", [
    ("x{{// a note for the preset author}}y", "xy"),
    ("x{{//\nline one\n{{char}} line two\n}}y", "xy"),
    ("{{//{{setvar::v::1}}}}[{{getvar::v}}]", "[]"),  # nothing inside a comment runs
    ("a{{noop}}b", "ab"),
    ("a{{space}}b", "a b"),
    ("a{{newline}}b", "a\nb"),
    ("a\n{{newline}}\n{{trim}}\nb", "ab"),  # trim comes last, eats line breaks newline made
    ("one\r\n{{trim}}\r\ntwo", "onetwo"),
])
def test_formatting_macros(text, expected):
    assert sub(text, variables={}) == expected


@pytest.mark.parametrize("text, expected", [
    ("<USER> and <BOT> and <CHAR>", "Bob and Ann and Ann"),
    ("<GROUP>", "Ann"),
    ("<user> tag stays", "<user> tag stays"),
    ("{{Char}} / {{USER}}", "Ann / Bob"),
])
def test_names_and_angle_forms(text, expected):
    assert sub(text) == expected


def test_group_macros_from_values_and_their_fallbacks():
    group = {"group": "Ann, Cid", "groupNotMuted": "Ann", "notChar": "Cid, Bob"}
    assert sub("{{group}}|{{charIfNotGroup}}|{{groupNotMuted}}|{{notChar}}|<GROUP>", values=group) == \
        "Ann, Cid|Ann, Cid|Ann|Cid, Bob|Ann, Cid"
    # one character: group = charIfNotGroup = the character, notChar = the user
    assert sub("{{group}}|{{charIfNotGroup}}|{{groupNotMuted}}|{{notChar}}") == "Ann|Ann|Ann|Bob"


def test_card_macros_long_forms_and_values_holding_macros():
    values = {"description": "{{char}} is tall.", "personality": "calm", "scenario": "a bar",
              "charFirstMessage": "Hi", "charInstruction": "Stay", "mesExamples": "ex"}
    text = "{{charDescription}}|{{charPersonality}}|{{charScenario}}|{{greeting}}|{{charJailbreak}}|{{mesExamplesRaw}}"
    assert sub(text, values=values) == "Ann is tall.|calm|a bar|Hi|Stay|ex"
    # known card fields without a value are empty; unknown names stay
    assert sub("[{{charPrompt}}][{{charVersion}}][{{systemPrompt}}][{{nope}}]", values={}) == "[][][][{{nope}}]"


def test_context_macros_stay_without_values_for_a_later_pass():
    assert sub("{{description}} {{lastMessage}} {{original}}") == "{{description}} {{lastMessage}} {{original}}"


def test_message_and_model_macros():
    values = {"lastMessage": "m3", "lastUserMessage": "u", "lastCharMessage": "c", "lastMessageId": "2",
              "firstIncludedMessageId": "0", "currentSwipeId": "1", "lastSwipeId": "3", "input": "typed",
              "model": "gpt-x", "maxContext": "8192", "maxPrompt": "7000", "maxResponse": "1192"}
    text = ("{{lastMessage}} {{lastUserMessage}} {{lastCharMessage}} {{lastMessageId}} {{firstIncludedMessageId}} "
            "{{currentSwipeId}} {{lastSwipeId}} {{input}} {{model}} {{maxContext}} {{maxPrompt}} {{maxResponse}}")
    assert sub(text, values=values) == "m3 u c 2 0 1 3 typed gpt-x 8192 7000 1192"
    assert sub("{{lastGenerationType}} {{isMobile}} [{{lastMessage}}]", values={}) == "normal false []"


def test_random_both_syntaxes_and_roll():
    assert sub("{{random::red::blue}}", rng=lambda n: 1) == "blue"
    assert sub("{{random:a, b ,c}}", rng=lambda n: 1) == "b"
    assert sub("{{roll:1d6}}", rng=lambda n: 3) == "4"
    assert sub("{{roll::2d6+3}}", rng=lambda n: 0) == "5"
    assert sub("{{roll:20}}", rng=lambda n: n - 1) == "20"
    assert sub("{{roll:0d6}}") == "{{roll:0d6}}"


def test_pick_is_stable_per_chat_and_place():
    text = "{{pick::a::b::c::d::e::f}} {{pick::a::b::c::d::e::f}}"
    once = sub(text, values={"chatId": "chat-1"})
    assert once == sub(text, values={"chatId": "chat-1"})
    results = {sub(text, values={"chatId": f"chat-{i}"}) for i in range(30)}
    assert len(results) > 1  # another chat picks differently


def test_reverse():
    assert sub("{{reverse::abc}}") == "cba"


def test_time_and_date_macros():
    assert sub("{{time}}|{{date}}|{{weekday}}|{{isotime}}|{{isodate}}") == \
        "3:05 PM|November 14, 2026|Saturday|15:05|2026-11-14"
    assert sub("{{datetimeformat DD.MM.YYYY HH:mm}}") == "14.11.2026 15:05"
    assert sub("{{time_UTC+0}}|{{time_UTC-5}}") == "12:05 PM|7:05 AM"
    assert sub("{{timeDiff::2026-11-14 12:00::2026-11-14 10:00}}") == "in 2 hours"
    assert sub("{{timeDiff::2026-11-10::2026-11-14}}") == "4 days ago"


def test_idle_duration():
    since = (NOW - timedelta(hours=3)).isoformat()
    assert sub("{{idle_duration}}", values={"lastUserMessageTime": since}) == "3 hours"
    assert sub("{{idle_duration}}", values={}) == "just now"


@pytest.mark.parametrize("pattern, expected", [
    ("YYYY-MM-DD", "2026-11-14"), ("dddd, MMMM Do", "Saturday, November 14th"), ("h:mm A", "3:05 PM"),
    ("[Day] D", "Day 14"), ("LT", "3:05 PM"), ("LL", "November 14, 2026"), ("Z", "+03:00"),
])
def test_format_datetime(pattern, expected):
    assert format_datetime(NOW, pattern) == expected


@pytest.mark.parametrize("delta, expected", [
    (timedelta(seconds=10), "a few seconds"), (timedelta(minutes=1), "a minute"), (timedelta(minutes=5), "5 minutes"),
    (timedelta(hours=1), "an hour"), (timedelta(days=1), "a day"), (timedelta(days=40), "a month"),
    (timedelta(days=400), "a year"),
])
def test_humanize(delta, expected):
    assert humanize(delta) == expected


def test_variables_chat_store_kept_in_given_dict():
    store: dict[str, str] = {"seen": "2"}
    text = ("{{setvar::mood::{{char}} is happy}}{{getvar::mood}}|{{incvar::seen}}|{{decvar::n}}|"
            "{{addvar::seen::5}}{{getvar::seen}}|{{addvar::s::ab}}{{addvar::s::cd}}{{getvar::s}}|"
            "{{hasvar::mood}}{{deletevar::mood}}{{hasvar::mood}}")
    assert sub(text, variables=store) == "Ann is happy|3|-1|8|abcd|truefalse"
    assert store["seen"] == "8"
    # a second text of the same prompt sees what the first one set
    assert sub("{{getvar::s}}", variables=store) == "abcd"


def test_variables_without_a_store_stay_for_the_assembly():
    assert sub("{{setvar::a::1}}{{getvar::a}}") == "{{setvar::a::1}}{{getvar::a}}"


def test_global_variables():
    shared: dict[str, str] = {}
    assert sub("{{setglobalvar::g::7}}{{incglobalvar::g}}", global_variables=shared) == "8"
    assert sub("{{getglobalvar::g}}|{{hasglobalvar::g}}", global_variables=shared) == "8|true"
    assert sub("{{setglobalvar::x::1}}{{getglobalvar::x}}") == "1"  # no store given: this text only


def test_if_else_blocks():
    values = {"description": "tall", "scenario": ""}
    assert sub("{{if description}}D{{else}}none{{/if}}", values=values) == "D"
    assert sub("{{if scenario}}S{{else}}no scene{{/if}}", values=values) == "no scene"
    assert sub("{{if !scenario}}empty{{/if}}", values=values) == "empty"
    store = {"flag": "0"}
    assert sub("{{if {{getvar::flag}}}}on{{else}}off{{/if}}", variables=store) == "off"
    # nested blocks, and only the chosen branch runs
    text = "{{if description}}a{{if scenario}}b{{else}}c{{setvar::ran::1}}{{/if}}{{else}}{{setvar::no::1}}{{/if}}"
    store = {}
    assert sub(text, values=values, variables=store) == "ac"
    assert store == {"ran": "1"}


def test_outlet_from_values():
    values = {"outlet::Lore": "the book's text"}
    assert sub("[{{outlet::Lore}}][{{outlet::missing}}]", values=values) == "[the book's text][]"


def test_unknown_macros_and_stray_braces_stay():
    for text in ("{{unknownThing}}", "{{foo::bar}}", "{{ {\"json\": 1} }}", "a {{ b", "{{/if}} {{else}}",
                 "{{if x}} never closed", "}} {{"):
        assert sub(text, values={}, variables={}) == text
    assert sub("{{foo::{{char}}}}") == "{{foo::Ann}}"


def test_banned_and_has_extension():
    assert sub('a{{banned "word"}}b|{{hasExtension::tts}}') == "ab|false"


def test_text_without_macros_is_returned_as_is():
    text = "plain text, no macros"
    assert sub(text) is text
