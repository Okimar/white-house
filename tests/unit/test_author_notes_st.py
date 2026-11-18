"""Wave 2, author's notes the SillyTavern way (docs/ST_PARITY.md section 6): a note before / after the main prompt,
lorebook entries «at the author's note» riding with it (from SillyTavern files only when a note fired), the
author's note of an imported SillyTavern chat."""

from __future__ import annotations

from tavern.core.assembly import Member, NoteInput, PromptInput, assemble
from tavern.core.presets import default_preset, normalize_preset
from tavern.core.retrieval import FoundBlock
from tavern.formats import st_lorebook
from tavern.formats.st_chat import author_note

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind."}, primary=True)
HISTORY = [{"id": f"m{i}", "role": ("user", "assistant")[i % 2], "content": f"msg {i}", "depth": i, "name": ""}
           for i in range(6)]


def build(notes=(), lore=(), preset=None):
    data = preset or normalize_preset(default_preset())
    return assemble(PromptInput(preset=data, preset_id="p", members=[ANN], history=HISTORY, notes=list(notes),
                                lore=list(lore)))


def contents(prompt) -> list[str]:
    return [m.content for m in prompt.messages]


def lore_at_note(uid: int, text: str, *, st: bool, place: int = 2) -> FoundBlock:
    """A constant entry «at the top of the author's note» (ST position 2), from an ST world file or ours."""
    if st:
        entry = {"uid": uid, "key": [], "content": text, "constant": True, "disable": False, "order": 100,
                 "position": place}
        block = {"id": f"b{uid}", **st_lorebook.block_from_entry(entry, uid)}
    else:
        block = {"id": f"b{uid}", "title": "Own", "content": text, "mode": "always", "depth": None,
                 "role": "system", "rules": {"place": "an_top" if place == 2 else "an_bottom"}}
    return FoundBlock(block, f"Book › {uid}", "always")


def test_a_note_before_and_after_the_main_prompt():
    prompt = build([NoteInput("a", "BEFORE", place="before_main"), NoteInput("b", "AFTER", place="after_main")])
    first = prompt.messages[0].content  # the default preset squashes the system texts at the top together
    assert first.index("BEFORE") < first.index("Write Ann's next reply") < first.index("AFTER")
    assert not any("BEFORE" in c or "AFTER" in c for c in contents(prompt)[1:])
    assert prompt.placed == []  # never kept on a message


def test_a_switched_off_main_prompt_leaves_the_notes_at_the_top():
    preset = default_preset()
    next(b for b in preset["blocks"] if b["kind"] == "main")["enabled"] = False
    prompt = build([NoteInput("a", "BEFORE", place="before_main")], preset=normalize_preset(preset))
    assert prompt.messages[0].content.startswith("BEFORE")


def test_lore_at_the_note_rides_with_a_note_outside_the_chat():
    prompt = build([NoteInput("a", "NOTE", place="after_main")], [lore_at_note(1, "TOP", st=True),
                                                                 lore_at_note(2, "BOTTOM", st=True, place=3)])
    first = prompt.messages[0].content
    assert first.index("Write Ann") < first.index("TOP") < first.index("NOTE") < first.index("BOTTOM")


def test_lore_at_the_note_goes_even_without_a_note_text():
    # SillyTavern's note fires every reply by default, empty or not, and carries these entries (owner's exam)
    without = contents(build([], [lore_at_note(1, "ST-TOP", st=True), lore_at_note(2, "OWN-TOP", st=False)]))
    assert any("ST-TOP" in c for c in without) and any("OWN-TOP" in c for c in without)
    with_note = contents(build([NoteInput("n", "NOTE", depth=2)], [lore_at_note(1, "ST-TOP", st=True)]))
    assert any("ST-TOP" in c for c in with_note)


def test_the_author_note_of_a_sillytavern_chat_becomes_a_chat_note():
    note = author_note({"note_prompt": "Stay grim.", "note_depth": 2, "note_position": 2, "note_interval": 3,
                        "note_role": 1})
    assert note == [{"title": "Заметка автора из SillyTavern", "text": "Stay grim.", "enabled": True, "depth": 2,
                     "place": "before_main", "role": "user", "interval": 2, "mode": "once"}]
    assert author_note({"note_prompt": "\n", "note_position": 1}) == []
    assert author_note(None) == [] and author_note({"note_prompt": 5}) == []
    off = author_note({"note_prompt": "x", "note_interval": 0})[0]
    assert off["enabled"] is False and off["place"] == "depth" and off["depth"] == 4
