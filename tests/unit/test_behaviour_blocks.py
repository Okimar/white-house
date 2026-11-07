"""Behaviour blocks: «не писать за меня», «кто отвечает», «написать за меня» and «рассказчик» are blocks of
the preset (kind text with `when`), sent only while their behaviour is on; older presets keep their wording."""

from __future__ import annotations

import pytest

from tavern.core.assembly import Member, PromptInput, assemble
from tavern.core.presets import PresetError, WHEN, default_preset, normalize_preset, when_texts
from tavern.services import presets as preset_service

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind."}, primary=True)
BOB = Member("bob", "Bob", {"name": "Bob", "description": "Bob."})
ME = Member("me", "Kira", {"name": "Kira", "description": "x"}, is_me=True, model_may_write=False)
HISTORY = [{"id": "m0", "role": "user", "content": "hi", "depth": 0, "name": ""}]


def build(preset, members, **kw):
    return assemble(PromptInput(preset=normalize_preset(preset), preset_id="p", members=members,
                                history=HISTORY, **kw))


def text_of(prompt):
    return "\n".join(m.content for m in prompt.messages)


def block(preset, key):
    return next(b for b in preset["blocks"] if b.get("when") == key)


def test_default_preset_has_one_block_per_behaviour_and_they_are_not_in_the_normal_flow():
    preset = default_preset()
    assert sorted(b["when"] for b in preset["blocks"] if b.get("when")) == sorted(WHEN)
    plain = build(preset, [ANN])
    assert "Never write" not in text_of(plain) and "narrator" not in text_of(plain)
    assert "Write the next reply only as" not in text_of(plain)


def test_no_write_block_is_sent_only_while_the_model_may_not_write_for_me():
    preset = default_preset()
    assert "Never write Kira's words" in text_of(build(preset, [ANN, ME]))
    allowed = Member("me", "Kira", {"description": "x"}, is_me=True, model_may_write=True)
    assert "Never write" not in text_of(build(preset, [ANN, allowed]))
    block(preset, "no_write_for_me")["enabled"] = False
    assert "Never write" not in text_of(build(preset, [ANN, ME]))


def test_speaker_reply_block_needs_two_characters_and_follows_its_switch():
    preset = default_preset()
    assert "only as Ann" not in text_of(build(preset, [ANN]))
    assert "only as Ann" in text_of(build(preset, [ANN, BOB], several=True))
    block(preset, "speaker_reply")["enabled"] = False
    assert "only as Ann" not in text_of(build(preset, [ANN, BOB], several=True))


def test_impersonate_block_goes_as_a_user_message_and_only_for_the_button():
    preset = default_preset()
    assert "Write the next message of" not in text_of(build(preset, [ANN, ME]))
    prompt = build(preset, [ANN, ME], impersonate=True)
    assert prompt.messages[-1].role == "user" and "Kira" in prompt.messages[-1].content


def test_narrator_text_comes_from_its_block():
    preset = normalize_preset(default_preset())
    assert "narrator of this roleplay" in when_texts(preset, "narrator")[0]["text"]
    block(preset, "narrator")["enabled"] = False
    assert when_texts(preset, "narrator") == []


def test_old_prompts_texts_become_blocks_and_nothing_is_lost():
    old = {"blocks": [{"id": "main", "kind": "main", "text": "Main."}],
           "prompts": {"no_write_for_me": "Hands off {{user}}!", "speaker_reply": "",
                       "impersonate": "As {{user}}:", "narrator": "Tell the story."}}
    preset = normalize_preset(old)
    assert block(preset, "no_write_for_me")["text"] == "Hands off {{user}}!"
    assert block(preset, "impersonate")["text"] == "As {{user}}:"
    assert block(preset, "impersonate")["role"] == "user"
    assert block(preset, "narrator")["text"] == "Tell the story."
    silent = block(preset, "speaker_reply")  # an emptied field meant «don't say it»: a switched-off block
    assert silent["enabled"] is False and silent["text"]
    assert "no_write_for_me" not in preset["prompts"]
    assert normalize_preset(preset) == preset  # normalizing again changes nothing


def test_a_block_with_unknown_behaviour_is_refused():
    raw = default_preset()
    block(raw, "narrator")["when"] = "weather"
    with pytest.raises(PresetError, match="unknown behaviour"):
        normalize_preset(raw)


def test_startup_migration_rewrites_stored_presets_once_and_names_the_changed_ones(ctx):
    custom = {"blocks": [], "prompts": {"no_write_for_me": "Hands off!"}}
    plain = {"blocks": [], "prompts": {}}
    a = ctx.presets.create("Mine", custom)
    b = ctx.presets.create("Plain", plain)
    assert preset_service.migrate_behaviour_blocks(ctx) == ["Mine"]
    stored = ctx.presets.get(a["id"])["data"]
    assert block(stored, "no_write_for_me")["text"] == "Hands off!"
    assert sorted(x["when"] for x in ctx.presets.get(b["id"])["data"]["blocks"] if x.get("when")) == sorted(WHEN)
    assert ctx.settings.get(preset_service.MOVED_SETTING) == ["Mine"]
    assert preset_service.migrate_behaviour_blocks(ctx) == []  # already done
    assert ctx.settings.get(preset_service.MOVED_SETTING) == ["Mine"]
