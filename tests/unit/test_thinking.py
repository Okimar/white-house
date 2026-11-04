"""Thinking written as text (stage 10е-1): split out of a reply, preset groups and the «Размышление» block,
past thinking sent back without repeats."""

from __future__ import annotations

import httpx
import pytest

from tavern.core import thinking
from tavern.core.assembly import Member, PromptInput, assemble
from tavern.core.budget import RunBudget
from tavern.core.llm import LLMClient
from tavern.core.presets import PresetError, default_preset, normalize_preset
from tavern.devtools.fake_llm import FakeLLM
from tavern.formats.st_preset import to_st
from tavern.providers.base import ChatMessage
from tavern.providers.openai_compat import OpenAICompatProvider

ANN = Member("ann", "Ann", {"name": "Ann", "description": "Ann is kind."}, primary=True)


def feed_all(pieces: list[str]) -> tuple[str, str, thinking.ThinkSplitter]:
    splitter = thinking.ThinkSplitter()
    out = [p for piece in pieces for p in splitter.feed(piece)] + splitter.finish()
    return ("".join(t for k, t in out if k == "text"), "".join(t for k, t in out if k == "reasoning"), splitter)


@pytest.mark.parametrize("raw", [
    "<thinking>plan: hug</thinking>\n\nShe smiles.",
    "<THINK>plan: hug</THINK>She smiles.",
])
def test_thinking_tag_is_cut_out_even_when_streamed_one_letter_at_a_time(raw):
    text, thought, _ = feed_all(list(raw))
    assert (text, thought) == ("She smiles.", "plan: hug")
    assert thinking.split(raw) == ("She smiles.", "plan: hug")


def test_text_without_tags_and_lone_angle_brackets_pass_through():
    text, thought, _ = feed_all(["a < b and <i>x</i>", " <thin", "ly veiled>"])
    assert (text, thought) == ("a < b and <i>x</i> <thinly veiled>", "")


def test_unclosed_thinking_is_kept_as_the_reply_rather_than_lost():
    text, thought, splitter = feed_all(["<thinking>only thoughts, no", " end"])
    assert text == "" and thought == "only thoughts, no end" and splitter.unclosed == "only thoughts, no end"
    assert thinking.split("<thinking>only thoughts") == ("only thoughts", "only thoughts")


def test_compact_drops_lines_already_said_but_keeps_changed_numbers():
    first = "Language: Russian\nTension: 40%\nPlan: she hides the key"
    second = "language:  RUSSIAN!\nTension: 43%\nPlan: she hides the key"
    third = "Language: Russian"
    assert thinking.compact([first, second, third]) == [first, "Tension: 43%", ""]


# --- the model stream ---------------------------------------------------------------------------


async def test_llm_stream_splits_thinking_only_when_asked_and_never_sends_the_option():
    fake = FakeLLM()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=fake.app), base_url="http://llm")
    llm = LLMClient(OpenAICompatProvider("http://llm/v1", "k", client=http), model="m", budget=RunBudget())
    fake.queue("<thinking>count the arrows</thinking> He steps in.", "<thinking>x</thinking>y")
    events = [e async for e in llm.stream([ChatMessage("user", "hi")], split_thinking=True)]
    assert "".join(e.text for e in events if e.kind == "text") == "He steps in."
    assert "".join(e.text for e in events if e.kind == "reasoning") == "count the arrows"
    assert "split_thinking" not in fake.requests[0]
    plain = [e async for e in llm.stream([ChatMessage("user", "hi")])]
    assert "".join(e.text for e in plain if e.kind == "text") == "<thinking>x</thinking>y"


# --- presets ------------------------------------------------------------------------------------


def preset_with(*extra: dict, thoughts: int = 0) -> dict:
    raw = default_preset()
    raw["blocks"][-2:-2] = list(extra)  # just before the history block
    next(b for b in raw["blocks"] if b["kind"] == "history")["history"]["thoughts"] = thoughts
    return normalize_preset(raw)


def test_group_allows_only_one_block_on():
    short = {"id": "short", "kind": "text", "text": "Short.", "group": "Длина"}
    long = {"id": "long", "kind": "text", "text": "Long.", "group": " Длина ", "enabled": False}
    data = preset_with(short, long)
    assert [b["group"] for b in data["blocks"] if b["id"] in ("short", "long")] == ["Длина", "Длина"]
    with pytest.raises(PresetError, match="only one block may be on"):
        preset_with(short, {**long, "enabled": True})


def test_only_one_thinking_block():
    with pytest.raises(PresetError, match="at most one"):
        preset_with({"id": "t1", "kind": "thinking", "text": "a"}, {"id": "t2", "kind": "thinking", "text": "b"})


def test_thinking_block_is_framed_and_skipped_when_writing_for_the_user():
    data = preset_with({"id": "thinking", "kind": "thinking", "text": "Scene: [where]\nPlan: [what {{char}} does]"})
    msgs = [{"id": "m0", "role": "user", "content": "hi", "depth": 0}]
    prompt = assemble(PromptInput(preset=data, preset_id="p", members=[ANN], history=msgs))
    system = prompt.messages[0].content
    assert "<thinking>\nScene: [where]\nPlan: [what Ann does]\n</thinking>" in system
    assert "think it through" in system
    writing_for_me = assemble(PromptInput(preset=data, preset_id="p", members=[ANN], history=msgs, impersonate=True))
    assert "think it through" not in writing_for_me.messages[0].content


def test_past_thinking_is_not_sent_by_default_and_sent_compacted_when_asked():
    def reply(i: int, thought: str) -> dict:
        return {"id": f"a{i}", "role": "assistant", "content": f"reply {i}", "depth": i,
                "swipe_meta": {"reasoning": thought}}
    msgs = [reply(0, "Lang: RU\nPlan: wave"), {"id": "u1", "role": "user", "content": "hi", "depth": 1},
            reply(2, "Lang: RU\nPlan: run"), {"id": "u3", "role": "user", "content": "go", "depth": 3},
            reply(4, "Lang: RU\nPlan: hide")]
    plain = assemble(PromptInput(preset=preset_with(), preset_id="p", members=[ANN], history=msgs))
    assert all("<thinking>" not in m.content for m in plain.messages)
    sent = assemble(PromptInput(preset=preset_with(thoughts=2), preset_id="p", members=[ANN], history=msgs))
    by_text = [m.content for m in sent.messages if m.role == "assistant"]
    assert by_text[0] == "reply 0"  # older than the last two: no thinking
    assert by_text[1] == "<thinking>\nLang: RU\nPlan: run\n</thinking>\n\nreply 2"
    assert by_text[2] == "<thinking>\nPlan: hide\n</thinking>\n\nreply 4"  # the repeated line is gone


def test_st_export_sends_the_thinking_block_as_framed_text():
    data = preset_with({"id": "thinking", "kind": "thinking", "text": "Plan: [..]", "group": "Мысли"})
    prompt = next(p for p in to_st(data)["prompts"] if p["identifier"] == "thinking")
    assert prompt["marker"] is False and "<thinking>\nPlan: [..]\n</thinking>" in prompt["content"]
