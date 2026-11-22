"""Wave 5 of the SillyTavern parity plan: «Дописать» (continue the last message) and preset blocks limited to kinds
of generation (SillyTavern's injection_trigger). Expected lists written by hand from the plain-words rules."""

from __future__ import annotations

import pytest

from tavern.core.assembly import Member, NoteInput, PromptInput, assemble
from tavern.core.presets import PresetError, default_preset, normalize_preset
from tavern.formats.st_preset import from_st, to_st
from tavern.services.chat import continued_text

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind."}, primary=True)
NUDGE = "[Continue your last message without repeating its original content.]"


def history(*texts: str) -> list[dict]:
    roles = ("user", "assistant")
    return [{"id": f"m{i}", "role": roles[i % 2], "content": t, "depth": i, "name": ""} for i, t in enumerate(texts)]


def preset(*, post: str = "", prefill: bool = False, **prompts) -> dict:
    data = default_preset()
    data["prompts"].update(prompts)
    data["continue_prefill"] = prefill
    if post:
        next(b for b in data["blocks"] if b["kind"] == "post_history")["text"] = post
    return normalize_preset(data)


def pairs(data, msgs, **kw) -> list[tuple[str, str]]:
    prompt = assemble(PromptInput(preset=data, preset_id="p", members=[ANN], history=msgs, **kw))
    return [(m.role, m.content) for m in prompt.messages]


# «Дописать»


def test_the_continued_message_goes_last_then_the_nudge_with_the_cut_point():
    sent = pairs(preset(post="JB"), history("hi", "He opened the"), continue_id="m1", generation_kind="continue")
    assert sent[-4:-1] == [("user", "hi"), ("system", "JB"), ("assistant", "He opened the")]
    # ours: a user turn (a trailing system text may be moved away by a proxy), the preset's nudge, then where the
    # text stopped and «same reply, next word» (a preset's «open every reply with…» rule lost to the bare nudge)
    role, text = sent[-1]
    assert role == "user" and text.startswith(NUDGE) and "«He opened the»" in text and "SAME reply" in text


def test_an_empty_preset_nudge_still_sends_the_cut_point():
    sent = pairs(preset(continue_nudge=""), history("hi", "He opened the"), continue_id="m1")
    assert sent[-2] == ("assistant", "He opened the") and "«He opened the»" in sent[-1][1]


def test_with_continue_prefill_the_message_is_the_start_of_the_reply():
    sent = pairs(preset(prefill=True), history("hi", "He opened the"), continue_id="m1", generation_kind="continue")
    assert sent[-1] == ("assistant", "He opened the") and "SAME reply" in sent[-2][1]


def test_depth_zero_goes_before_the_continued_message_and_no_preset_prefill_or_filler():
    data = preset(prefill="Sure:", send_if_empty="[go]")
    sent = pairs(data, history("hi", "He opened the"), continue_id="m1", generation_kind="continue",
                 notes=[NoteInput("n", "NOTE", depth=0)], provider_kind="anthropic")
    assert sent[-3:-1] == [("system", "NOTE"), ("assistant", "He opened the")] and sent[-1][1].startswith(NUDGE)
    assert ("user", "[go]") not in sent and ("assistant", "Sure:") not in sent


def test_the_nudge_can_quote_the_message():
    data = preset(continue_nudge="Go on from: {{lastChatMessage}}")
    assert pairs(data, history("hi", "Rain"), continue_id="m1")[-1][1].startswith("Go on from: Rain\n")


def test_the_continued_reply_goes_with_its_own_thinking_even_when_old_thinking_is_off():
    msgs = history("hi", "He opened the")
    msgs[1]["swipe_meta"] = {"reasoning": "plan: the door creaks"}
    sent = pairs(preset(), msgs, continue_id="m1")
    assert "plan: the door creaks" in sent[-2][1] and sent[-2][0] == "assistant"


def test_a_word_cut_in_half_is_joined_without_a_space():
    # the model begins with the last word again, whole (asked in the continue instruction): the overlap goes
    assert continued_text("была пр", " ", "принадлежала саду", mid_word=True) == "была принадлежала саду"
    assert continued_text("реакцию", " ", "реакцию на его лице", mid_word=True) == "реакцию на его лице"
    assert continued_text("серьгу", " ", " с камнем", mid_word=True) == "серьгу с камнем"  # no repeat: a space
    assert continued_text("He opened the", " ", "door.") == "He opened the door."  # not asked: SillyTavern's join


@pytest.mark.parametrize(("postfix", "result"), [(" ", "He opened the door."), ("", "He opened the  door."),
                                                  ("\n", "He opened the\ndoor."), ("\n\n", "He opened the\n\ndoor.")])
def test_the_new_text_joins_the_old_with_the_postfix(postfix, result):
    assert continued_text("He opened the ", postfix, " door.") == result


# Triggers


def test_a_block_with_triggers_goes_only_for_those_kinds():
    data = default_preset()
    data["blocks"].append({"id": "x", "kind": "text", "name": "Only swipes", "text": "SWIPE-ONLY",
                           "triggers": ["swipe"]})
    data = normalize_preset(data)
    assert not any("SWIPE-ONLY" in c for _, c in pairs(data, history("hi")))
    assert any("SWIPE-ONLY" in c for _, c in pairs(data, history("hi"), generation_kind="swipe"))


def test_triggers_are_checked_and_travel_with_sillytavern_presets():
    bad = default_preset()
    bad["blocks"][0]["triggers"] = ["sometimes"]
    with pytest.raises(PresetError, match="triggers"):
        normalize_preset(bad)
    _, data = from_st({"prompts": [{"identifier": "x", "name": "X", "content": "T", "injection_trigger":
                                    ["impersonate", "bogus"]}],
                       "prompt_order": [{"character_id": 100001, "order": [{"identifier": "x", "enabled": True}]}],
                       "continue_prefill": True, "continue_nudge_prompt": "[More]", "continue_postfix": "\n"})
    block = next(b for b in data["blocks"] if b["id"] == "x")
    assert block["triggers"] == ["impersonate"]
    assert data["continue_prefill"] is True and data["prompts"]["continue_nudge"] == "[More]"
    assert data["prompts"]["continue_postfix"] == "\n"
    out = to_st(data)
    assert out["continue_prefill"] is True and out["continue_nudge_prompt"] == "[More]"
    assert next(p for p in out["prompts"] if p["identifier"] == "x")["injection_trigger"] == ["impersonate"]
