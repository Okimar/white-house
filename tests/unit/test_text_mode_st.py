"""Wave 6 of the SillyTavern parity plan: the text mode the SillyTavern way and the card's «Character's Note»
(.work/reports/st-textmode-notes.md, parts A and B5). Expected texts written by hand from the plain-words rules."""

from __future__ import annotations

from tavern.core import templates as T
from tavern.core.assembly import Member, NoteInput, PromptInput, assemble, character_note
from tavern.core.presets import default_preset, normalize_preset
from tavern.providers.base import ChatMessage as M

TALK = [M("system", "Be Ann."), M("user", "Hi"), M("assistant", "Hello"), M("user", "How are you?")]
NAMES = {"user": "Kira", "char": "Ann"}


# Templates


def test_sillytavern_wrap_puts_the_line_break_only_after_the_marker():
    ours = T.from_st({"input_sequence": "<|im_start|>user", "input_suffix": "<|im_end|>\n",
                      "output_sequence": "<|im_start|>assistant", "output_suffix": "", "wrap": True})
    text, _ = T.render(ours, [M("user", "hi"), M("assistant", "yo"), M("user", "ok")])
    assert text == ("<|im_start|>user\nhi<|im_end|>\n<|im_start|>assistant\nyo\n"
                    "<|im_start|>user\nok<|im_end|>\n<|im_start|>assistant\n")


def test_first_and_last_markers():
    template = {**T.BUILTIN["alpaca"], "first_user_prefix": "### First:\n", "last_user_prefix": "### Last:\n",
                "last_assistant_prefix": "### Reply:\n"}
    text, _ = T.render(template, TALK)
    assert "### First:\nHi" in text and "### Last:\nHow are you?" in text and text.endswith("### Reply:\n")
    assert text.count("### Instruction:") == 0


def test_names_fill_the_markers_and_stop_the_reply():
    template = {**T.BUILTIN["plain"], "user_prefix": "{{name}}: ", "assistant_prefix": "{{char}}: "}
    text, stops = T.render(template, TALK, names=NAMES)
    assert "Kira: Hi" in text and "Ann: Hello" in text and text.endswith("Ann: ")
    assert "\nKira:" in stops


def test_impersonation_opens_the_users_turn_and_stops_on_the_character():
    text, stops = T.render(T.BUILTIN["chatml"], TALK[:3], names=NAMES, turn="user")
    assert text.endswith("<|im_start|>assistant\nHello<|im_end|>\n<|im_start|>user\n")
    assert "<|im_start|>assistant" in stops and "\nAnn:" in stops and "\nKira:" not in stops


def test_no_builtin_carries_a_bos_text():
    assert all(not template["bos"] for template in T.BUILTIN.values())


def test_first_and_last_markers_travel_both_ways():
    ours = T.from_st({"input_sequence": "U:", "output_sequence": "A:", "first_output_sequence": "A1:",
                      "last_output_sequence": "AL:", "sequences_as_stop_strings": False})
    assert (ours["first_assistant_prefix"], ours["last_assistant_prefix"], ours["sequences_as_stops"]) == (
        "A1:", "AL:", False)
    back = T.to_st(ours)["instruct"]
    assert back["first_output_sequence"] == "A1:" and back["sequences_as_stop_strings"] is False


def test_text_mode_impersonation_sends_no_instruction():
    me = Member("me", "Kira", {"name": "Kira", "description": "x"}, is_me=True)
    ann = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind."}, primary=True)
    history = [{"id": "m0", "role": "user", "content": "hi", "depth": 0, "name": ""},
               {"id": "m1", "role": "assistant", "content": "hello", "depth": 1, "name": ""}]
    data = default_preset()
    data["prompts"]["impersonation_prefill"] = "Kira:"
    preset = normalize_preset(data)

    def last(kind):
        prompt = assemble(PromptInput(preset=preset, preset_id="p", members=[ann, me], history=history,
                                      impersonate=True, provider_kind=kind))
        return prompt.messages[-1]
    assert (last("text").role, last("text").content) == ("assistant", "hello")
    assert last("anthropic").content == "Kira:" and "point of view of Kira" in "".join(
        m.content for m in assemble(PromptInput(preset=preset, preset_id="p", members=[ann, me], history=history,
                                                impersonate=True, provider_kind="anthropic")).messages)


# Character's Note


def card(**depth_prompt) -> dict:
    return {"name": "Ann", "description": "Ann is kind.", "extensions": {"depth_prompt": depth_prompt}}


def test_character_note_defaults():
    assert character_note(card(prompt="Stay in role.")) == ("Stay in role.", 4, "system")
    assert character_note(card(prompt="X", depth=1, role="user")) == ("X", 1, "user")
    assert character_note(card(prompt="  ")) is None and character_note({"name": "Ann"}) is None
    assert character_note(card(prompt="X", depth=-3, role="narrator")) == ("X", 4, "system")


def test_character_note_goes_every_reply_at_its_depth_after_the_authors_note():
    ann = Member("ann", "Ann", card(prompt="{{char}} hides a key.", depth=1), primary=True)
    history = [{"id": f"m{i}", "role": ("user", "assistant")[i % 2], "content": f"msg {i}", "depth": i, "name": ""}
               for i in range(4)]
    prompt = assemble(PromptInput(preset=normalize_preset(default_preset()), preset_id="p", members=[ann],
                                  history=history, notes=[NoteInput("n", "NOTE", depth=1)]))
    sent = [(m.role, m.content) for m in prompt.messages]
    assert sent[-2:] == [("system", "NOTE\nAnn hides a key."), ("assistant", "msg 3")]
