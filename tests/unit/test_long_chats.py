"""Wave 3 of the SillyTavern parity plan: long chats (docs/ST_PARITY.md section 10).

Token counts by script without a tokenizer library, the ratio learned from the service's own counts, the window
minus the reply's length, and example dialogues that go only while room is left after the history (ST's default).
"""

from __future__ import annotations

import pytest

from tavern.core import tokens
from tavern.core.assembly import Member, PromptInput, assemble
from tavern.core.presets import PresetError, default_preset, normalize_preset
from tavern.services.chat import response_reserve

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind.",
                            "mes_example": "<START>\n{{user}}: hi\n{{char}}: " + "yo " * 60}, primary=True)


def history(count: int, size: int = 20) -> list[dict]:
    roles = ("user", "assistant")
    return [{"id": f"m{i}", "role": roles[i % 2], "content": f"message {i} " + "word " * size, "depth": i,
             "name": ""} for i in range(count)]


def build(msgs, mode=None, **kw):
    preset = default_preset()
    if mode is not None:
        next(b for b in preset["blocks"] if b["kind"] == "examples")["examples_mode"] = mode
    return assemble(PromptInput(preset=normalize_preset(preset), preset_id="p", members=[ANN], history=msgs, **kw))


def text_of(prompt) -> str:
    return "\n".join(m.content for m in prompt.messages)


# Counting


def test_counts_follow_the_script():
    assert tokens.count("") == 0 and tokens.count("a") == 1
    english = tokens.count("The cold evening came to the northern village.")
    russian = tokens.count("Холодный вечер пришёл в северную деревню.")
    assert russian > english  # Russian costs more than English in every model
    assert tokens.count("我们今天去公园") == 7  # one per CJK character
    assert tokens.count("1234567") == 3  # digits in groups of three
    assert tokens.count("a\n\n\nb") == 3  # a run of line breaks is one


def test_counts_are_remembered_per_text():
    tokens.count.cache_clear()
    text = "Одно и то же сообщение. " * 50
    tokens.count(text)
    tokens.count(text)
    assert tokens.count.cache_info().hits == 1


# Learning the model's own count


def test_the_ratio_starts_at_the_first_real_count_then_moves_slowly_and_stays_in_bounds():
    assert tokens.learn(None, 30, 300) is None  # too short to say anything
    assert tokens.learn(None, 1000, 0) is None
    first = tokens.learn(None, 1000, 1500)
    assert first == 1.5
    assert tokens.learn(first, 1000, 1000) == pytest.approx(1.5 + 0.3 * (1.0 - 1.5))
    assert tokens.learn(None, 1000, 99_000) == tokens.RATIO_MAX


def test_a_too_high_ratio_comes_down_even_when_the_window_keeps_prompts_small():
    ratio = tokens.RATIO_MAX
    for _ in range(12):  # a 550-token window at ratio 3 leaves prompts of about 180 estimated tokens
        ratio = tokens.learn(ratio, 180, 200) or ratio
    assert ratio < 1.2


def test_the_stored_ratio_turns_the_window_into_estimated_tokens():
    store = {tokens.ratio_key("c1", "m"): 1.25, tokens.ratio_key("c1", "bad"): "x", tokens.ratio_key("c1", "big"): 9}
    assert tokens.ratio(store, "c1", "m") == 1.25
    assert tokens.ratio(store, "c1", "bad") == 1.0 and tokens.ratio(store, "c1", "big") == 1.0
    assert tokens.ratio(None, "c2", "m") == 1.0
    assert tokens.window(8000, 1.25) == 6400 and tokens.window(0, 2.0) == 0


# The reply's room


@pytest.mark.parametrize(("window", "max_tokens", "reserve"), [
    (8000, 1000, 1000), (8000, None, 0), (0, 1000, 0), (8000, 0, 0), (1000, 5000, 750), (8000, True, 0),
])
def test_the_reply_gets_room_in_the_window(window, max_tokens, reserve):
    assert response_reserve(window, max_tokens) == reserve


# Example dialogues


def test_examples_go_only_while_room_is_left_after_the_history():
    roomy = build(history(4))
    assert "yo yo" in text_of(roomy) and "examples" not in roomy.dropped
    tight = build(history(30), max_context_tokens=500)
    assert "yo yo" not in text_of(tight) and tight.dropped["examples"] == ["Ann"]
    always = build(history(30), "always", max_context_tokens=500)
    assert "yo yo" in text_of(always) and "examples" not in always.dropped
    assert len(always.messages) < len(tight.messages)  # old messages went instead
    assert "yo yo" not in text_of(build(history(4), "never"))


def test_examples_mode_is_checked_and_defaults_to_room():
    blocks = {b["kind"]: b for b in normalize_preset(default_preset())["blocks"]}
    assert blocks["examples"]["examples_mode"] == "room"
    old = default_preset()
    del next(b for b in old["blocks"] if b["kind"] == "examples")["examples_mode"]
    assert {b["kind"]: b for b in normalize_preset(old)["blocks"]}["examples"]["examples_mode"] == "room"
    bad = default_preset()
    next(b for b in bad["blocks"] if b["kind"] == "examples")["examples_mode"] = "sometimes"
    with pytest.raises(PresetError, match="examples_mode"):
        normalize_preset(bad)
