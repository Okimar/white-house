"""core/assembly.py plugin pieces (ADR 0019 section 2.2-D): Extra with places, history first / memory for the rest
(covers, if_dropped, memory_reserve), the protected live tail, {{summary}}, the preset block kind `memory`."""

from __future__ import annotations

from tavern.core.assembly import Extra, Member, PromptInput, assemble
from tavern.core.presets import default_preset, normalize_preset

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind."}, primary=True)
BOB = Member("bob", "Bob", {"name": "Bob", "description": "Bob the smith."}, importance=8)


def history(count: int, size: int = 40) -> list[dict]:
    roles = ("user", "assistant")
    return [{"id": f"m{i}", "role": roles[i % 2], "content": f"сообщение {i} " + "слово " * size, "depth": i,
             "name": ""} for i in range(count)]


def build(msgs, extras=(), members=None, **kw):
    preset = kw.pop("preset", default_preset())
    return assemble(PromptInput(preset=preset, preset_id="p", members=members or [ANN], history=msgs,
                                extras=list(extras), **kw))


def fragment(i: int, **kw) -> Extra:
    return Extra(text=f"отрывок про {i}", source=f"plugin:memory:piece:{i}", label=f"Отрывок {i}",
                 covers=(f"m{i}", f"m{i}"), **kw)


def sent(prompt) -> str:
    return "\n".join(m.content for m in prompt.messages)


def test_when_everything_fits_the_prompt_is_exactly_what_it_was_without_the_plugin():
    msgs = history(6, size=5)
    plain = build(msgs, max_context_tokens=5000)
    with_extras = build(msgs, [fragment(0), fragment(1), Extra("Сюжет", "plugin:memory:plot", "Сюжет",
                                                               if_dropped=True)],
                        max_context_tokens=5000, memory_reserve=500, tail_from="m2")
    assert [m.content for m in with_extras.messages] == [m.content for m in plain.messages]
    assert [m.role for m in with_extras.messages] == [m.role for m in plain.messages]
    assert with_extras.dropped == plain.dropped  # the record is what it always was


def test_a_conditional_extra_is_sent_only_when_all_its_messages_were_left_out():
    msgs = history(30)
    prompt = build(msgs, [fragment(0), fragment(1), fragment(29)], max_context_tokens=500, memory_reserve=200)
    assert prompt.dropped["messages"] > 0
    text = sent(prompt)
    assert "отрывок про 0" in text and "отрывок про 1" in text  # old messages: gone from the window
    assert "отрывок про 29" not in text  # the newest message is in the window: nothing is sent twice
    assert prompt.dropped["memory"] == ["Отрывок 0", "Отрывок 1"]
    assert {e.source for e in prompt.entries} >= {"plugin:memory:piece:0", "plugin:memory:piece:1"}


def test_a_range_is_sent_only_if_every_message_in_it_was_left_out():
    msgs = history(30)
    wide = Extra("глава", "plugin:memory:chapter:a", "Глава", covers=("m0", "m29"))
    prompt = build(msgs, [wide], max_context_tokens=500, memory_reserve=200)
    assert "глава" not in sent(prompt)  # m29 is still in the window


def test_if_dropped_goes_only_when_anything_was_left_out():
    plot = Extra("Сюжет до сих пор", "plugin:memory:plot", "Сюжет", if_dropped=True)
    assert "Сюжет до сих пор" not in sent(build(history(3, 3), [plot], max_context_tokens=2000))
    assert "Сюжет до сих пор" in sent(build(history(30), [plot], max_context_tokens=500, memory_reserve=100))


def test_extras_go_to_the_memory_spot_before_the_first_sent_message_in_the_order_given():
    prompt = build(history(30), [fragment(3), fragment(1)], max_context_tokens=500, memory_reserve=200)
    text = sent(prompt)
    assert text.index("отрывок про 3") < text.index("отрывок про 1") < text.index("сообщение 29")
    assert text.index("Ann is kind.") < text.index("отрывок про 3")


def test_memory_block_in_the_default_preset_sits_just_before_the_history():
    kinds = [b["kind"] for b in default_preset()["blocks"]]
    assert kinds.index("memory") == kinds.index("history") - 1


def test_a_preset_without_a_memory_block_still_gets_the_spot_just_before_the_chat():
    preset = default_preset()
    preset["blocks"] = [b for b in preset["blocks"] if b["kind"] != "memory"]
    prompt = build(history(30), [fragment(0)], preset=normalize_preset(preset), max_context_tokens=500,
                   memory_reserve=200)
    assert "отрывок про 0" in sent(prompt)


def test_a_switched_off_memory_block_means_no_memory():
    preset = default_preset()
    next(b for b in preset["blocks"] if b["kind"] == "memory")["enabled"] = False
    prompt = build(history(30), [fragment(0), Extra("всегда", "plugin:memory:x", "Х")], preset=preset,
                   max_context_tokens=500, memory_reserve=200)
    assert "отрывок про 0" not in sent(prompt) and "всегда" not in sent(prompt)


def test_the_live_tail_is_not_cut_before_older_messages():
    """The reserve for memory would cut into the newest messages; the tail stops that."""
    msgs = history(30, size=30)
    memory = [fragment(0)]
    without = build(msgs, memory, max_context_tokens=900, memory_reserve=400)
    protected = build(msgs, memory, max_context_tokens=900, memory_reserve=400, tail_from="m15")
    kept = lambda p: [e.source for e in p.entries if e.kind == "message"]  # noqa: E731
    assert len(kept(without)) < 15  # making room for memory ate into the newest messages
    assert all(f"message:m{i}" in kept(protected) for i in range(15, 30))
    assert kept(protected)[0] == "message:m15"


def test_the_reserve_is_kept_free_for_memory_while_messages_are_cut():
    msgs = history(30)
    small = build(msgs, [fragment(0)], max_context_tokens=500, memory_reserve=0)
    reserved = build(msgs, [fragment(0)], max_context_tokens=500, memory_reserve=200)
    assert reserved.dropped["messages"] > small.dropped["messages"]  # more history left out to make room
    assert "отрывок про 0" in sent(reserved)


def test_an_extra_that_does_not_fit_the_reserve_is_named_as_dropped():
    big = Extra("длинный " * 300, "plugin:memory:big", "Большой", covers=("m0", "m0"))
    prompt = build(history(30), [big, fragment(1)], max_context_tokens=500, memory_reserve=100)
    assert "Большой" in prompt.dropped["extras"] and "Отрывок 1" in prompt.dropped["memory"]


def test_labels_a_plugin_left_out_itself_are_shown_as_dropped():
    prompt = build(history(3, 3), extras_dropped=["Глава 3"])
    assert prompt.dropped["extras"] == ["Глава 3"]


def test_unconditional_extras_are_trimmed_by_rank_before_member_cards():
    keep = Extra("важное", "plugin:memory:a", "Важное", drop_rank=90)
    lose = Extra("мелочь", "plugin:memory:b", "Мелочь", drop_rank=10)
    bob = Member("bob", "Bob", {"description": "Bob the smith. " * 20}, importance=9)
    both = build(history(6, 10), [keep, lose], members=[ANN, bob], max_context_tokens=100000)
    window = both.tokens - 1  # a little too small: one small extra has to go
    prompt = build(history(6, 10), [keep, lose], members=[ANN, bob], max_context_tokens=window)
    assert "мелочь" not in sent(prompt) and "важное" in sent(prompt) and "Bob the smith." in sent(prompt)
    assert prompt.dropped["extras"] == ["Мелочь"] and prompt.dropped["members"] == []


def test_after_card_goes_right_after_that_members_card_and_leaves_with_the_member():
    dev = Extra("Сейчас: Боб устал", "plugin:memory:dev:bob", "Развитие Боба", place="after_card", owner="bob")
    prompt = build(history(4, 3), [dev], members=[ANN, BOB], max_context_tokens=100000)
    text = sent(prompt)
    assert text.index("Bob the smith.") < text.index("Сейчас: Боб устал")
    assert "Сейчас: Боб устал" not in sent(build(history(4, 3), [dev], members=[ANN], max_context_tokens=100000))


def test_after_card_is_cut_together_with_its_member_when_the_context_is_short():
    dev = Extra("Сейчас: Боб устал", "plugin:memory:dev:bob", "Развитие Боба", place="after_card", owner="bob")
    bob = Member("bob", "Bob", {"description": "Bob the smith. " * 60}, importance=9)
    prompt = build(history(8), [dev], members=[ANN, bob], max_context_tokens=300)
    assert prompt.dropped["members"] == ["Bob"] and "Сейчас: Боб устал" not in sent(prompt)


def test_extras_with_a_depth_go_into_the_chat_at_that_depth():
    deep = Extra("глубокий", "plugin:memory:d", "Глубокий", place="depth", depth=2)
    prompt = build(history(5, 3), [deep], max_context_tokens=100000)
    contents = [m.content for m in prompt.messages]
    index = next(i for i, c in enumerate(contents) if "глубокий" in c)
    assert index == len(contents) - 3


def test_summary_macro_gets_the_plugins_value():
    preset = default_preset()
    preset["blocks"][0]["text"] = "Сюжет: {{summary}}"
    prompt = build(history(2, 3), preset=preset, values={"summary": "Они встретились в таверне."})
    assert "Сюжет: Они встретились в таверне." in sent(prompt)
    assert "Сюжет: " in sent(build(history(2, 3), preset=preset))  # empty without the plugin


def test_the_memory_kind_has_no_text_and_survives_normalize():
    block = next(b for b in normalize_preset(default_preset())["blocks"] if b["kind"] == "memory")
    assert block["text"] == "" and block["enabled"] is True
