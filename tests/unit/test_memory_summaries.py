"""Memory++ M3, pure parts: which chapter is summarized next (lock, button, stale, backoff), what the plot needs,
the request texts and the checks of the answer (language, length), summary vs piece by the margin, the sticky
window, the group filter, and the step-1 review regression on the cost of cutting long messages."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from tavern.builtin_plugins.memory import chapters, pieces, prompts, search, summaries

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def placed(cid: str, lo: int, state: str = "raw", **row) -> chapters.Placed:
    return chapters.Placed({"id": cid, "state": state, "title": "", "failed_at": "", "tries": 0, **row}, lo, lo + 4)


def text(tid: str, locked: bool = False) -> dict:
    return {"id": tid, "text": "x", "locked": locked, "made_from": ""}


def test_next_chapter_story_order_raw_first_never_locked_or_deleted():
    rows = [placed("a", 0, "deleted"), placed("b", 5, "summarized"), placed("c", 10), placed("d", 15)]
    chosen, _ = summaries.next_chapter(rows, {"b": text("tb")}, auto=True, auto_stale=False, forced=set(), now=NOW)
    assert chosen.row["id"] == "c"
    rows[2] = placed("c", 10, "stale")
    chosen, _ = summaries.next_chapter(rows, {"b": text("tb"), "c": text("tc", locked=True)}, auto=True,
                                       auto_stale=True, forced={"c"}, now=NOW)
    assert chosen.row["id"] == "d"  # a locked summary is never replaced, even when asked for


def test_next_chapter_off_does_only_what_the_button_asked_and_stale_only_with_the_checkbox():
    rows = [placed("a", 0), placed("b", 5, "stale")]
    assert summaries.next_chapter(rows, {}, auto=False, auto_stale=False, forced=set(), now=NOW)[0] is None
    assert summaries.next_chapter(rows, {}, auto=False, auto_stale=False, forced={"b"}, now=NOW)[0].row["id"] == "b"
    only_stale = [placed("b", 5, "stale")]
    assert summaries.next_chapter(only_stale, {}, auto=True, auto_stale=False, forced=set(), now=NOW)[0] is None
    assert summaries.next_chapter(only_stale, {}, auto=True, auto_stale=True, forced=set(), now=NOW)[0] is not None


def test_a_failed_chapter_waits_for_its_backoff_which_doubles():
    failed = (NOW - timedelta(seconds=60)).isoformat()
    rows = [placed("a", 0, "failed", failed_at=failed, tries=1)]
    chosen, wait = summaries.next_chapter(rows, {}, auto=True, auto_stale=False, forced=set(), now=NOW)
    assert chosen is None and 59 <= wait <= 61  # 120 s after the first failure
    rows = [placed("a", 0, "failed", failed_at=(NOW - timedelta(seconds=130)).isoformat(), tries=1)]
    assert summaries.next_chapter(rows, {}, auto=True, auto_stale=False, forced=set(), now=NOW)[0] is not None
    assert summaries.retry_delay(3) == 480 and summaries.retry_delay(50) == summaries.BACKOFF_CAP


def test_plot_state_none_fresh_append_rebuild_and_lock():
    rows = [placed("a", 0), placed("b", 5)]
    sums = {"a": {"id": "sa", "text": "A"}, "b": {"id": "sb", "text": "B"}}
    assert summaries.plot_state(rows, {}, None) == ("none", [])
    assert summaries.plot_state(rows, sums, None) == ("rebuild", ["sa", "sb"])
    assert summaries.plot_state(rows, sums, {"made_from": '["sa"]', "locked": False}) == ("append", ["sb"])
    assert summaries.plot_state(rows, sums, {"made_from": '["sa", "sb"]', "locked": False})[0] == "fresh"
    assert summaries.plot_state(rows, sums, {"made_from": '["sx"]', "locked": False})[0] == "rebuild"
    assert summaries.plot_state(rows, sums, {"made_from": '[]', "locked": True})[0] == "fresh"


def test_summary_request_language_tense_no_meta_and_previous_context():
    messages = [{"role": "user", "name": "Я", "content": "Я вхожу в таверну."},
                {"role": "assistant", "name": "Ann", "content": "Ann наливает эль."}]
    request, source = prompts.summary_messages(messages, "Раньше Анна приехала в город.", 1000)
    system, user = request[0].content, request[1].content
    assert "(here: Russian)" in system and "Past tense" in system and "No meta talk" in system
    assert "about 1000 tokens" in system
    assert "Раньше Анна приехала в город." in user and "Ann: Ann наливает эль." in user
    english, _ = prompts.summary_messages([{"role": "user", "content": "I walk in."}], "", 500)
    assert "(here: Russian)" not in english[0].content


def test_the_answer_is_checked_for_language_and_length():
    russian = "Анна вошла в таверну и долго говорила с хозяином о караване."
    english = "Anna came into the tavern and talked with the keeper about the caravan."
    assert prompts.problem(russian, russian, 300) == ""
    assert "Russian" in prompts.problem(english, russian, 300)
    assert "not in Russian" in prompts.problem(russian, english, 300)
    assert "short" in prompts.problem("Коротко.", russian, 300)
    assert "longer" in prompts.problem(russian * 100, russian, 100)


def test_a_piece_beats_the_summary_only_by_the_margin():
    assert search.choose_form(0.5, 0.55) == "summary"
    assert search.choose_form(0.5, 0.65) == "pieces"
    assert search.choose_form(None, 0.3) == "pieces"
    assert search.choose_form(0.4, 0.0) == "summary"


def test_sticky_window_and_group_filter():
    assert not search.sticky_live(5, 5, 3)  # the reply it was found in found it anew
    assert search.sticky_live(5, 8, 3) and not search.sticky_live(5, 9, 3)
    assert search.member_allowed(["ann"], "bob", several=False)
    assert not search.member_allowed(["ann"], "bob", several=True)
    assert search.member_allowed(["ann"], "bob", several=True, remembers_all=True)
    assert search.member_allowed(["ann"], "", several=True)  # the user's own turn sees everything


def test_review_regression_keywords_of_a_long_message_are_linear():
    """M3 review: keywords() looked back over the whole text for every word (quadratic), and the job cut every
    message of the branch again on each pass, on the server's event loop."""
    long_text = "Анна посмотрела на Бориса и сказала, что они пойдут в Лес. " * 1500  # about 90 000 chars
    started = time.perf_counter()
    words = pieces.keywords(long_text)
    assert time.perf_counter() - started < 2.0
    assert "*борис" in words and "*лес" in words
    message = {"id": "m1", "role": "user", "content": "Вчера Анна видела Бориса."}
    assert pieces.specs_for(message) == pieces.specs_for(dict(message))  # the second one comes from the cache
    assert pieces._specs.cache_info().hits >= 1
