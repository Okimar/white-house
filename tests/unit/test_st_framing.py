"""Wave 1a of the SillyTavern parity plan: how the chat is framed and worded (docs/ST_PARITY.md, sections 2 and 3).

Expected message lists are written by hand from the plain-words rules: the new-chat line before the history,
send_if_empty, the group nudge before the post-history prompts, impersonation as the last system message, the
prefill only for Claude / text mode, and a preset from SillyTavern getting bare card parts, persona and examples.
"""

from __future__ import annotations

import pytest

from tavern.core.assembly import Member, NoteInput, PromptInput, assemble, example_dialogues
from tavern.core.presets import ORIGIN_ST, WHEN, WHEN_V1, default_preset, normalize_preset
from tavern.core.prompt import START_TURN
from tavern.core.templates import BUILTIN, render
from tavern.formats.st_preset import from_st, to_st
from tavern.providers.base import ChatMessage, spoken
from tavern.providers.openai_compat import _message

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind.", "personality": "calm",
                            "scenario": "A quiet library.",
                            "mes_example": "<START>\n{{user}}: hi\n{{char}}: yo\nand more\n<START>\n{{char}}: again"},
             primary=True)
BOB = Member("bob", "Bob", {"name": "Bob", "description": "Bob the smith."})
ME = Member("me", "Kira", {"name": "Kira", "description": "Tall man."}, is_me=True)
NEW_CHAT = "[Start a new Chat]"
NUDGE = "[Write the next reply only as Ann.]"


def history(*texts: str) -> list[dict]:
    roles = ("user", "assistant")
    return [{"id": f"m{i}", "role": roles[i % 2], "content": t, "depth": i, "name": ""} for i, t in enumerate(texts)]


def preset(*, st: bool = False, squash: bool | None = None, post: str = "", **prompts: str) -> dict:
    data = default_preset()
    data["prompts"].update(prompts)
    if st:
        data["origin"] = ORIGIN_ST
        data["squash_system"] = False
    if squash is not None:
        data["squash_system"] = squash
    if post:
        next(b for b in data["blocks"] if b["kind"] == "post_history")["text"] = post
    return normalize_preset(data)


def build(members, msgs, data=None, **kw):
    return assemble(PromptInput(preset=data or preset(), preset_id="p", members=members, history=msgs, **kw))


def pairs(prompt) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in prompt.messages]


# 1. The new-chat line


def test_new_chat_line_stands_right_before_the_first_history_message_and_is_never_squashed():
    prompt = build([ANN], history("hi", "hello"), preset(squash=True))
    assert [r for r, _ in pairs(prompt)] == ["system", "system", "user", "assistant"]
    assert pairs(prompt)[1:] == [("system", NEW_CHAT), ("user", "hi"), ("assistant", "hello")]
    assert "Ann is kind." in prompt.messages[0].content  # the cards squashed together, the line on its own


def test_group_chat_gets_the_group_line_with_the_member_names():
    prompt = build([ANN, BOB, ME], history("hi"), several=True)
    assert ("system", "[Start a new group chat. Group members: Ann, Bob]") in pairs(prompt)


def test_new_chat_line_goes_before_deep_injections_and_is_kept_by_the_budget():
    prompt = build([ANN], history(*[f"message {i} " * 30 for i in range(12)]), max_context_tokens=400,
                   notes=[NoteInput("n", "NOTE", depth=50)])
    contents = [c for _, c in pairs(prompt)]
    assert prompt.dropped["messages"] > 0
    assert contents.index(NEW_CHAT) + 1 == contents.index("NOTE")


def test_no_user_turn_our_preset_without_the_line_gets_start_and_st_presets_never_do():
    greeting = [{"id": "g", "role": "assistant", "content": "Greeting", "depth": 0}]
    assert pairs(build([ANN], greeting))[1:] == [("system", NEW_CHAT), ("assistant", "Greeting")]
    assert pairs(build([ANN], greeting, preset(new_chat="")))[1:] == [("user", START_TURN), ("assistant", "Greeting")]
    st = build([ANN], greeting, preset(st=True, new_chat=""))
    assert ("user", START_TURN) not in pairs(st) and pairs(st)[-1] == ("assistant", "Greeting")


def test_switched_off_history_has_no_new_chat_line():
    data = preset()
    next(b for b in data["blocks"] if b["kind"] == "history")["enabled"] = False
    assert ("system", NEW_CHAT) not in pairs(build([ANN], history("hi"), data))


# 2. send_if_empty


def test_send_if_empty_closes_a_chat_that_ends_with_the_models_message():
    data = preset(send_if_empty="[continue]")
    assert pairs(build([ANN], history("hi", "hello"), data))[-1] == ("user", "[continue]")
    assert pairs(build([ANN], history("hi"), data))[-1] == ("user", "hi")
    assert ("user", "[continue]") not in pairs(build([ANN], history("hi", "hello"), data, impersonate=True))
    assert ("user", "[continue]") not in pairs(build([ANN], history("hi", "hello")))  # empty by default


def test_send_if_empty_counts_as_a_chat_message_for_depths_but_a_note_lands_on_the_real_message():
    note = NoteInput("n", "NOTE", depth=1, mode="visible")
    prompt = build([ANN], history("hi", "hello"), preset(send_if_empty="[continue]"), notes=[note])
    assert pairs(prompt)[-3:] == [("assistant", "hello"), ("system", "NOTE"), ("user", "[continue]")]
    assert prompt.placed[0]["message_id"] == "m1"


# 3. The group nudge and the impersonation instruction


def test_group_nudge_goes_after_the_last_message_and_before_the_post_history_prompt():
    prompt = build([ANN, BOB], history("hi"), preset(post="JB"), several=True)
    assert pairs(prompt)[-3:] == [("user", "hi"), ("system", NUDGE), ("system", "JB")]


def test_impersonation_is_a_system_message_at_the_very_end():
    prompt = build([ANN, ME], history("hi", "hello"), preset(post="JB", squash=False), impersonate=True)
    expected = WHEN["impersonate"][2].replace("{{user}}", "Kira").replace("{{char}}", "Ann")
    assert pairs(prompt)[-2:] == [("system", "JB"), ("system", expected)]
    assert expected.startswith("[Write your next reply from the point of view of Kira")


# 4. Prefill


@pytest.mark.parametrize("kind", ["openai", "gemini", ""])
def test_prefill_is_never_a_trailing_assistant_turn_for_openai_and_gemini(kind):
    prompt = build([ANN], history("hi"), preset(prefill="Sure:"), provider_kind=kind)
    assert pairs(prompt)[-1] == ("user", "hi")


@pytest.mark.parametrize("kind", ["anthropic", "text"])
def test_prefill_goes_to_claude_and_the_text_mode_with_trailing_spaces_cut(kind):
    prompt = build([ANN], history("hi"), preset(prefill="{{char}} smiles and  \n"), provider_kind=kind)
    assert pairs(prompt)[-1] == ("assistant", "Ann smiles and")


def test_claude_gets_the_impersonation_prefill_when_writing_for_the_user():
    data = preset(prefill="Sure:", impersonation_prefill="{{user}}: ")
    prompt = build([ANN, ME], history("hi", "hello"), data, impersonate=True, provider_kind="anthropic")
    assert prompt.messages[-2].role == "system" and pairs(prompt)[-1] == ("assistant", "Kira:")
    plain = build([ANN, ME], history("hi", "hello"), preset(prefill="Sure:"), impersonate=True,
                  provider_kind="anthropic")
    assert plain.messages[-1].role == "system"  # no impersonation prefill: none, and never the reply's prefill


# 5. Wording: a preset from SillyTavern


def test_st_preset_sends_card_parts_bare_each_its_own_message():
    prompt = build([ANN], history("hi"), preset(st=True, personality_format="", scenario_format=""))
    contents = [c for _, c in pairs(prompt)]
    assert ["Ann is kind.", "calm", "A quiet library."] == contents[1:4]
    assert not any("Description:" in c or "Personality:" in c for c in contents)


def test_st_preset_persona_is_the_bare_description():
    prompt = build([ANN, ME], history("hi"), preset(st=True))
    assert ("system", "Tall man.") in pairs(prompt)
    assert not any("played by the user" in c for _, c in pairs(prompt))


def test_st_preset_examples_are_separator_plus_named_lines_never_squashed():
    for squash in (False, True):
        prompt = build([ANN, ME], history("hi"), preset(st=True, squash=squash))
        lines = [(m.role, m.name, m.speaker, m.content) for m in prompt.messages if m.name or m.content == "[Example Chat]"]
        assert lines == [
            ("system", "", "", "[Example Chat]"),
            ("system", "example_user", "Kira", "hi"),
            ("system", "example_assistant", "Ann", "yo\nand more"),
            ("system", "", "", "[Example Chat]"),
            ("system", "example_assistant", "Ann", "again"),
        ]


def test_our_own_preset_keeps_its_headers():
    prompt = build([ANN, ME], history("hi"))
    system = prompt.messages[0].content
    assert "Description:\nAnn is kind." in system and "Kira (played by the user):" in system
    assert "Example dialogue of Ann:" in system and not any(m.name for m in prompt.messages)


def test_example_dialogues_split_by_start_and_names():
    assert example_dialogues("<START>\nKira: a\nAnn: b\nc\n<start>\nx", "Kira", "Ann") == [
        [("user", "a"), ("char", "b\nc")], [("char", "x")]]


def test_named_lines_reach_each_service():
    line = ChatMessage("system", "hi", name="example_user", speaker="Kira")
    assert _message(line) == {"role": "system", "content": "hi", "name": "example_user"}
    assert "name" not in _message(ChatMessage("user", "x"))
    assert spoken(line) == "Kira: hi" and spoken(ChatMessage("user", "x")) == "x"
    text, _ = render(BUILTIN["chatml"], [ChatMessage("system", "rules"), ChatMessage("user", "q"), line])
    assert "Kira: hi" in text


def test_group_macro_lists_the_characters():
    data = default_preset()
    next(b for b in data["blocks"] if b["kind"] == "main")["text"] = "Cast: {{group}}"
    prompt = build([ANN, BOB, ME], history("hi"), normalize_preset(data), several=True)
    assert prompt.messages[0].content.startswith("Cast: Ann, Bob")


# Presets: defaults, upgrade, import / export


def test_defaults_are_sillytaverns():
    prompts = default_preset()["prompts"]
    assert prompts["new_chat"] == NEW_CHAT and prompts["new_example_chat"] == "[Example Chat]"
    assert prompts["new_group_chat"] == "[Start a new group chat. Group members: {{group}}]"
    assert prompts["send_if_empty"] == "" and prompts["impersonation_prefill"] == ""
    assert WHEN["speaker_reply"][2] == "[Write the next reply only as {{char}}.]"
    assert WHEN["impersonate"][1] == "system"


def _when(data: dict, key: str) -> dict:
    return next(b for b in data["blocks"] if b.get("when") == key)


def test_old_default_wording_moves_to_sillytaverns_but_own_text_stays():
    data = default_preset()
    del data["texts_version"]
    _when(data, "impersonate").update(role=WHEN_V1["impersonate"][0], text=WHEN_V1["impersonate"][1])
    _when(data, "speaker_reply")["text"] = "My own nudge."
    out = normalize_preset(data)
    assert (_when(out, "impersonate")["role"], _when(out, "impersonate")["text"]) == WHEN["impersonate"][1:]
    assert _when(out, "speaker_reply")["text"] == "My own nudge." and out["texts_version"] == 2
    _when(out, "impersonate")["role"] = "user"  # a person's later choice is kept
    assert _when(normalize_preset(out), "impersonate")["role"] == "user"


def test_an_older_st_import_gets_system_impersonation_and_its_kept_texts():
    data = default_preset()
    del data["texts_version"]
    data["origin"] = None
    data["st"] = {"chat_completion_source": "custom", "new_chat_prompt": "[New]", "send_if_empty": "[go]"}
    del data["prompts"]["new_chat"], data["prompts"]["send_if_empty"]
    _when(data, "impersonate").update(role="user", text="Write as {{user}}.")
    out = normalize_preset(data)
    assert out["origin"] == ORIGIN_ST and _when(out, "impersonate")["role"] == "system"
    assert out["prompts"]["new_chat"] == "[New]" and out["prompts"]["send_if_empty"] == "[go]"


def test_st_import_and_export_carry_the_texts():
    _, data = from_st({"prompts": [], "new_chat_prompt": "", "assistant_impersonation": "As me:",
                       "new_example_chat_prompt": "[Ex]", "impersonation_prompt": "Be {{user}}."})
    assert data["origin"] == ORIGIN_ST and data["prompts"]["new_chat"] == ""
    assert data["prompts"]["impersonation_prefill"] == "As me:" and data["prompts"]["new_example_chat"] == "[Ex]"
    assert data["prompts"]["new_group_chat"].startswith("[Start a new group chat")  # missing in the file: default
    assert _when(data, "impersonate")["role"] == "system"
    out = to_st(data)
    assert out["new_chat_prompt"] == "" and out["assistant_impersonation"] == "As me:"
    assert out["new_example_chat_prompt"] == "[Ex]" and "new_chat_prompt" not in data["st"]
