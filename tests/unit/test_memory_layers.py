"""Memory++ M4, pure parts: which chapter the development step takes next (story order, waits for its summary,
backoff, gives up after MAX_TRIES), «Сейчас:» lines stacked after a regex rewrite and only on chosen bubbles, the
answer read by code or by name, the request texts, the self-compression's last resort, and the memory budget."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from tavern.builtin_plugins.memory import chapters, inject, layers, prompts
from tavern.core.prompt import estimate_tokens

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def placed(cid: str, lo: int, state: str = "summarized") -> chapters.Placed:
    return chapters.Placed({"id": cid, "state": state, "participants": []}, lo, lo + 4)


def run(state: str, tries: int = 0, minutes_ago: float = 0) -> dict:
    stamp = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {"state": state, "tries": tries, "error": "x", "failed_at": stamp if state == "failed" else ""}


SUMS = {"a": {"text": "A"}, "b": {"text": "B"}, "c": {"text": "C"}}


def test_next_chapter_in_story_order_skipping_done_and_deleted():
    rows = [placed("a", 0), placed("x", 5, "deleted"), placed("b", 10), placed("c", 15)]
    chosen, _ = layers.next_chapter(rows, SUMS, {"a": run("done")}, summaries_on=True, now=NOW)
    assert chosen.row["id"] == "b"


def test_next_chapter_waits_for_the_summary_unless_summaries_are_off_or_it_failed():
    rows = [placed("a", 0, "raw"), placed("b", 10)]
    assert layers.next_chapter(rows, {"b": {"text": "B"}}, {}, summaries_on=True, now=NOW) == (None, None)
    assert layers.next_chapter(rows, {}, {}, summaries_on=False, now=NOW)[0].row["id"] == "a"
    rows[0] = placed("a", 0, "failed")  # its summary failed: the chapter's text is used instead of waiting
    assert layers.next_chapter(rows, {}, {}, summaries_on=True, now=NOW)[0].row["id"] == "a"


def test_a_failed_run_waits_its_backoff_holds_later_chapters_and_is_skipped_after_max_tries():
    rows = [placed("a", 0), placed("b", 10)]
    chosen, wait = layers.next_chapter(rows, SUMS, {"a": run("failed", 1, minutes_ago=0.5)}, summaries_on=True,
                                       now=NOW)
    assert chosen is None and 0 < wait <= 120  # story order: b waits too
    chosen, _ = layers.next_chapter(rows, SUMS, {"a": run("failed", 1, minutes_ago=10)}, summaries_on=True, now=NOW)
    assert chosen.row["id"] == "a"
    chosen, _ = layers.next_chapter(rows, SUMS, {"a": run("failed", layers.MAX_TRIES)}, summaries_on=True, now=NOW)
    assert chosen.row["id"] == "b"


@dataclass
class Found:
    block: dict


@dataclass
class Prompt:
    lore: list
    lore_rewritten: dict = field(default_factory=dict)


def test_now_lines_go_only_on_chosen_bubbles_stacked_after_a_regex_rewrite():
    prompt = Prompt(lore=[Found({"id": "b1", "content": "Старая мельница."}), Found({"id": "b2", "content": "Замок."})],
                    lore_rewritten={"b2": "Замок (переписан правилом)."})
    lines = {"b1": {"text": "сгорела"}, "b2": {"text": "взят"}, "b3": {"text": "не выбран"}, }
    assert layers.add_now_lines(prompt, lines) == 2
    assert prompt.lore_rewritten == {"b1": "Старая мельница.\nСейчас: сгорела",
                                     "b2": "Замок (переписан правилом).\nСейчас: взят"}
    empty = Prompt(lore=[Found({"id": "b1", "content": "x"})])
    assert layers.add_now_lines(empty, {"b1": {"text": "  "}}) == 0 and empty.lore_rewritten == {}  # a removed line


def test_the_answer_is_read_by_code_or_by_name():
    people = [{"code": "C1", "name": "Ann"}, {"code": "C2", "name": "Боб"}]
    answer = [{"who": "c1", "text": "a"}, {"who": "Боб", "text": "b"}, {"who": "Кто-то", "text": "?"}, "junk"]
    assert layers._pick(people, answer, "who", "text") == {"C1": "a", "C2": "b"}


def test_layer_request_texts():
    people = [{"code": "C1", "name": "Ann", "card": "Добрая трактирщица.", "old": ""}]
    entries = [{"code": "E1", "title": "Мельница", "text": "Старая мельница.", "old": "цела"}]
    messages, source = prompts.layer_messages("Анна сожгла мельницу.", people, entries, 450)
    system, user = messages[0].content, messages[1].content
    assert "how this character is NOW" in system and "at most 450 tokens" in system and "world entry" in system
    assert "(here: Russian)" in system and "C1: Ann" in user and "Old layer: (none yet)" in user
    assert "E1: Мельница" in user and "Old line: цела" in user and source == "Анна сожгла мельницу."
    only_lines, _ = prompts.layer_messages("Анна сожгла мельницу.", [], entries, 450)
    assert "how this character is NOW" not in only_lines[0].content and "Characters:" not in only_lines[1].content
    again, _ = prompts.layer_messages("x", people, [], 450, note="the text must be in Russian")
    assert "refused: the text must be in Russian" in again[1].content


def test_wrong_language_and_the_last_resort_cut():
    assert prompts.wrong_language("Anna is tired now.", "Анна устала и ушла домой.")
    assert prompts.wrong_language("Анна устала.", "Анна устала и ушла домой.") == ""
    long = "Анна устала. " * 200
    cut = prompts.cut_to(long, 100)
    assert estimate_tokens(cut) <= 100 and cut.endswith(".")


def test_the_memory_budget_is_a_share_of_the_window_never_below_the_floor():
    settings = {"memory_tokens": 1500, "memory_percent": 15}
    assert inject.budget(settings, 32000) == 4800
    assert inject.budget(settings, 4000) == 1500  # a small window: the floor
    assert inject.budget({"memory_tokens": 600, "memory_percent": 0}, 32000) == 600  # percent 0: the fixed number


def test_a_fresh_hand_version_is_not_anchored_at_the_newest_message():
    """A regenerated or continued reply is left out of its own history: a version anchored at it would be off the
    branch exactly during that reply."""
    path = [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
    assert layers.fresh_anchor(path) == "m2" and layers.fresh_anchor(path[:1]) == "m1"
    assert layers._anchor({"anchor_id": "m1"}, path) == "m1"
