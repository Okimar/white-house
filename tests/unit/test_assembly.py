"""Prompt assembly from presets: block order, members with depth and importance, "me", budget."""

from __future__ import annotations

from tavern.core.assembly import Member, NoteInput, PromptInput, assemble
from tavern.core.presets import default_preset, normalize_preset
from tavern.core.prompt import START_TURN

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind.", "personality": "calm",
                            "mes_example": "<START>\n{{char}}: Hi, {{user}}."}, primary=True)
BOB = Member("bob", "Bob", {"name": "Bob", "description": "Bob the smith."}, importance=8)
ME = Member("me", "Kira", {"name": "Kira", "description": "A traveller."}, is_me=True)


def history(*texts: str) -> list[dict]:
    roles = ("user", "assistant")
    return [{"id": f"m{i}", "role": roles[i % 2], "content": t, "depth": i, "name": ""}
            for i, t in enumerate(texts)]


def build(members, msgs, **kw):
    preset = kw.pop("preset", default_preset())
    return assemble(PromptInput(preset=preset, preset_id="p", members=members, history=msgs, **kw))


def test_default_preset_puts_cards_in_one_system_message_before_the_chat():
    prompt = build([ANN, ME], history("hello", "hi there"))
    assert [m.role for m in prompt.messages] == ["system", "user", "assistant"]
    system = prompt.messages[0].content
    assert system.index("Write Ann's next reply") < system.index("Kira (played by the user)")
    assert system.index("A traveller.") < system.index("Ann is kind.") < system.index("Example dialogue of Ann")
    assert "Hi, Kira." in system and "<START>" not in system
    assert {e.source for e in prompt.entries} >= {"character:ann:card", "character:me:card", "message:m0"}


def test_member_with_depth_is_inserted_into_the_chat():
    bob = Member("bob", "Bob", {"description": "Bob the smith."}, depth=1)
    prompt = build([ANN, bob], history("a", "b", "c"))
    contents = [m.content for m in prompt.messages]
    assert "Bob the smith." in contents[-2] and contents[-1] == "c"
    assert "Bob the smith." not in contents[0]


def test_least_important_member_is_dropped_before_the_main_character():
    big = "word " * 400
    bob = Member("bob", "Bob", {"description": big}, importance=9)
    prompt = build([ANN, bob], history(*["x"] * 8), max_context_tokens=200)
    assert prompt.dropped["members"] == ["Bob"]
    assert "Ann is kind." in prompt.messages[0].content


def test_oldest_messages_are_dropped_first_but_newest_is_kept():
    prompt = build([ANN], history(*[f"message {i} " * 20 for i in range(30)]), max_context_tokens=300)
    assert prompt.dropped["messages"] > 0
    assert prompt.messages[-1].content.startswith("message 29")


def test_no_write_for_me_is_sent_unless_the_model_writes_for_the_user():
    mute = Member("me", "Kira", {"description": "x"}, is_me=True, model_may_write=False)
    normal = build([ANN, mute], history("hi"))
    assert any("Never write Kira's words" in m.content for m in normal.messages)
    impersonating = build([ANN, mute], history("hi"), impersonate=True)
    assert not any("Never write" in m.content for m in impersonating.messages)
    assert impersonating.messages[-1].role == "user" and "Kira" in impersonating.messages[-1].content


def test_blocks_follow_the_preset_order_and_switches():
    preset = default_preset()
    blocks = {b["id"]: b for b in preset["blocks"]}
    blocks["main"]["enabled"] = False
    preset["blocks"].append({"id": "jb", "kind": "text", "name": "Style", "role": "system",
                             "text": "Be brief, {{char}}.", "position": "top", "depth": 4})
    prompt = build([ANN], history("hi"), preset=normalize_preset(preset))
    assert "Write Ann" not in prompt.messages[0].content
    assert prompt.messages[-1].content == "Be brief, Ann."  # after the history block


def test_card_system_prompt_replaces_main_with_original_macro():
    card = {"description": "d", "system_prompt": "Card rules. {{original}}"}
    prompt = build([Member("ann", "Ann", card, primary=True)], history("hi"))
    assert prompt.messages[0].content.startswith("Card rules. Write Ann's next reply")


def test_history_last_n_and_start_turn():
    preset = default_preset()
    next(b for b in preset["blocks"] if b["kind"] == "history")["history"] = {"mode": "last", "count": 2,
                                                                            "max_tokens": 0}
    prompt = build([ANN], history("1", "2", "3", "4"), preset=normalize_preset(preset))
    assert [m.content for m in prompt.messages[1:]] == ["3", "4"]
    only_reply = build([ANN], [{"id": "g", "role": "assistant", "content": "Greeting", "depth": 0}])
    assert [m.content for m in only_reply.messages[1:]] == [START_TURN, "Greeting"]


def test_prefill_ends_the_prompt_but_not_when_writing_for_the_user():
    preset = default_preset()
    preset["prompts"]["prefill"] = "{{char}} smiles and"
    prompt = build([ANN], history("hi"), preset=normalize_preset(preset))
    assert (prompt.messages[-1].role, prompt.messages[-1].content) == ("assistant", "Ann smiles and")
    impersonating = build([ANN], history("hi"), preset=normalize_preset(preset), impersonate=True)
    assert impersonating.messages[-1].role == "user"


def test_st_style_card_formats():
    preset = default_preset()
    preset["prompts"]["personality_format"] = "[{{char}}'s personality: {{personality}}]"
    prompt = build([ANN], history("hi"), preset=normalize_preset(preset))
    assert "Ann is kind.\n\n[Ann's personality: calm]" in prompt.messages[0].content
    assert "Description:" not in prompt.messages[0].content


def test_card_macros_and_variables_in_preset_blocks():
    """Presets that switch the card markers off and pull the card in with macros (owner's ST presets)."""
    preset = default_preset()
    for block in preset["blocks"]:
        if block["kind"] in ("cast", "persona"):
            block["enabled"] = False
    preset["blocks"].insert(0, {"id": "vars", "kind": "text", "name": "Vars",
                                "text": "{{setvar::lang::Russian}}{{setvar::style::\nshort\n}}"})
    preset["blocks"].insert(1, {"id": "card", "kind": "text", "name": "Card",
                                "text": "<char>{{description}} / {{personality}}</char> <me>{{persona}}</me> "
                                        "lang={{getvar::lang}} {{{getvar::style}}} missing=[{{getvar::nope}}]"})
    prompt = build([ANN, ME], history("hi"), preset=normalize_preset(preset))
    system = prompt.messages[0].content
    assert "<char>Ann is kind. / calm</char> <me>A traveller.</me> lang=Russian {\nshort\n} missing=[]" in system
    assert "setvar" not in system


def test_old_random_and_roll_forms():
    from tavern.core.prompt import substitute
    assert substitute("{{random:a,b,c}}", char="", user="", rng=lambda n: 2) == "c"
    assert substitute("{{roll::1d20}}", char="", user="", rng=lambda n: 4) == "5"


def test_tree_blocks_go_next_to_the_card_or_at_their_depth():
    from dataclasses import replace

    from tavern.core.retrieval import FoundBlock

    def found(id, title, depth=None, role="system"):
        return FoundBlock({"id": id, "title": title, "content": f"{title} text", "depth": depth, "role": role},
                          f"Ann › {title}", "keyword")

    ann = replace(ANN, blocks=[found("h", "Hair"), found("m", "Mood", depth=0, role="user")])
    prompt = build([ann], history("a", "b"))
    system = prompt.messages[0].content
    assert system.index("Ann is kind.") < system.index("[Ann › Hair]\nHair text")
    assert (prompt.messages[-1].role, prompt.messages[-1].content) == ("user", "[Ann › Mood]\nMood text")
    assert {e.source for e in prompt.entries} >= {"block:h", "block:m"}

    preset = default_preset()
    for b in preset["blocks"]:
        if b["kind"] == "cast":
            b["enabled"] = False
    no_cast = build([ann], history("a"), preset=normalize_preset(preset))
    assert "[Ann › Hair]" in no_cast.messages[0].content  # still sent, before the chat


def test_entries_point_to_the_message_they_were_sent_in():
    prompt = build([ANN, BOB, ME], history("hi"))
    for entry in prompt.entries:
        assert entry.text in prompt.messages[entry.msg].content


# --- author's notes (stage 4в) ------------------------------------------------------------------


def test_fired_note_goes_at_its_depth_and_says_after_which_message():
    note = NoteInput("n1", "[Rain is falling, {{char}} is cold.]", role="system", depth=1, mode="visible")
    prompt = build([ANN], history("a", "b", "c"), notes=[note])
    contents = [m.content for m in prompt.messages]
    assert contents[-2] == "[Rain is falling, Ann is cold.]" and contents[-1] == "c"
    assert prompt.placed == [{"note_id": "n1", "mode": "visible", "message_id": "m1",
                              "text": "[Rain is falling, Ann is cold.]", "role": "system", "title": ""}]
    assert any(e.kind == "note" and e.source == "note:n1" for e in prompt.entries)


def test_saved_note_stays_after_its_message_and_is_not_sent_twice():
    msgs = history("a", "b", "c")
    msgs[1]["notes"] = [{"id": "a1", "note_id": "n1", "role": "user", "text": "It is night.", "title": "Time"}]
    prompt = build([ANN], msgs, notes=[NoteInput("n1", "It is night.", depth=1, mode="visible")])
    texts = [e.text for e in prompt.entries if e.kind in ("message", "note")]
    assert texts == ["a", "b", "It is night.", "c"]  # the live copy was skipped: already attached there
    assert prompt.placed == []
    label = next(e.label for e in prompt.entries if e.source == "message_note:a1")
    assert label == "Заметка у №2: Time"


def test_attached_notes_go_away_with_their_trimmed_message():
    msgs = history(*["word " * 50] * 10)
    msgs[0]["notes"] = [{"id": "a1", "note_id": None, "role": "system", "text": "OLD NOTE", "title": ""}]
    prompt = build([ANN], msgs, max_context_tokens=300)
    assert prompt.dropped["messages"] > 0
    assert all("OLD NOTE" not in m.content for m in prompt.messages)


def test_note_deeper_than_the_chat_goes_first_and_has_no_message():
    prompt = build([ANN], history("a"), notes=[NoteInput("n1", "early", depth=9)])
    assert prompt.placed[0]["message_id"] is None
    assert [e.text for e in prompt.entries if e.kind in ("message", "note")] == ["early", "a"]
