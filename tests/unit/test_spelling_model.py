"""Built-in plugin «Орфография», the model as the checker and why the plugin «did not work at all» (owner,
2026-11-11, «Привит я бола» typed on an Android tablet and nothing happened): every word of that line is a real
word, so the dictionary services find nothing, and only a model sees the mistakes. Here: sentences, reading the
model's answer (offsets checked against the text, made-up fragments dropped), only new sentences sent, names and
the dictionary filtered, a newer check cancelling an older one."""

from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest

from tavern.paths import BUILTIN_PLUGINS_DIR

spec = importlib.util.spec_from_file_location("test_builtin_spelling_model",
                                              BUILTIN_PLUGINS_DIR / "spelling" / "server.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

OWNER_TEXT = "Привит я бола"


class FakeModel:
    """Answers generate_json from a function of the numbered lines it got; remembers every request."""

    def __init__(self, answer):
        self.answer = answer
        self.requests: list[list[str]] = []

    async def generate_json(self, messages, *, schema=None, **opts):
        assert schema is sp.MODEL_SCHEMA and opts["temperature"] == 0
        lines = [line.split(": ", 1)[1] for line in messages[-1].content.split("\n")]
        self.requests.append(lines)
        return self.answer(lines)


def owner_answer(lines):
    items = []
    for n, line in enumerate(lines, 1):
        if "Привит" in line:
            items.append({"n": n, "start": line.index("Привит"), "end": line.index("Привит") + 6, "wrong": "Привит",
                          "variants": ["Привет,", "Привет"], "kind": "spelling"})
        if "бола" in line:
            items.append({"n": n, "start": line.index("бола"), "end": line.index("бола") + 4, "wrong": "бола",
                          "variants": ["была"], "kind": "grammar"})
    return {"items": items}


def settings(**extra):
    return {"engine": "model", "connection": "", "model": "", "language": "auto", "max_chars": 1500,
            "dictionary": "", **extra}


@pytest.fixture(autouse=True)
def fresh():
    sp._model_cache.clear()
    sp._cache.clear()
    yield


# -- the regression: real words with mistakes ---------------------------------------------------------------


def test_the_owners_line_has_only_real_words_so_a_dictionary_service_reports_nothing():
    # LanguageTool's answer for it holds no misspelling (a grammar hint at most); only TYPOS are shown.
    answer = {"matches": [{"offset": 9, "length": 4, "replacements": [{"value": "была"}],
                           "rule": {"id": "RU_AGREEMENT", "issueType": "grammar", "category": {"id": "GRAMMAR"}}}]}
    assert sp.parse("languagetool", answer, OWNER_TEXT) == []


async def test_the_model_finds_both_mistakes_of_the_owners_line():
    model = FakeModel(owner_answer)
    found = await sp.model_check(OWNER_TEXT, settings(), [], model)
    assert [(i["word"], i["start"], i["end"], i["suggestions"], i["kind"]) for i in found] == [
        ("Привит", 0, 6, ["Привет,", "Привет"], "spelling"), ("бола", 9, 13, ["была"], "grammar")]
    assert model.requests == [[OWNER_TEXT]]
    assert "Привит я бола" in sp.MODEL_SYSTEM  # the prompt names exactly this kind of mistake


# -- sentences and what is sent -------------------------------------------------------------------------------


def test_sentences_keep_their_place_in_the_text():
    text = "  Привит я бола. Как дела?\nНовая строка"
    got = sp.sentences(text)
    assert [line for _, line in got] == ["Привит я бола.", "Как дела?", "Новая строка"]
    assert all(text[start:start + len(line)] == line for start, line in got)


async def test_only_new_sentences_go_to_the_model_and_the_old_ones_come_from_memory():
    model = FakeModel(owner_answer)
    await sp.model_check("Привит я бола. Всё хорошо.", settings(), [], model)
    found = await sp.model_check("Привит я бола. Всё хорошо. И ещё бола", settings(), [], model)
    assert model.requests == [["Привит я бола.", "Всё хорошо."], ["И ещё бола"]]
    assert [(i["word"], i["start"]) for i in found] == [("Привит", 0), ("бола", 9), ("бола", 33)]
    again = await sp.model_check("Привит я бола. Всё хорошо. И ещё бола", settings(), [], model)
    assert len(model.requests) == 2 and again == found


async def test_code_macros_and_commands_never_reach_the_model():
    model = FakeModel(lambda lines: {"items": []})
    await sp.model_check("Скажи {{char}} привет `секрет` https://x.io", settings(), [], model)
    sent = " ".join(model.requests[0])
    assert "{{char}}" not in sent and "секрет" not in sent and "https" not in sent
    assert await sp.model_check("/roll 2d6", settings(), [], model) == []
    assert len(model.requests) == 1


async def test_names_and_dictionary_words_are_not_flagged_and_names_are_told_to_the_model():
    def answer(lines):
        line = lines[0]
        return {"items": [{"n": 1, "start": line.index(w), "end": line.index(w) + len(w), "wrong": w,
                           "variants": [w + "а"], "kind": "spelling"} for w in ("Алисой", "кролик", "превет")]}

    model = FakeModel(answer)
    found = await sp.model_check("Я с Алисой и кролик превет", settings(dictionary="кролик"), ["Алиса"], model)
    assert [i["word"] for i in found] == ["превет"]
    messages = sp.model_messages(["x"], "ru", ["Алиса", "Гарольд"])
    assert "Алиса, Гарольд" in messages[0].content and "Russian" in messages[0].content


# -- reading the answer ---------------------------------------------------------------------------------------


def test_offsets_are_checked_against_the_text_and_made_up_fragments_are_dropped():
    lines = ["мы пошли в лес и в лес", "второй"]
    value = {"items": [
        {"n": 1, "start": 19, "end": 22, "wrong": "лес", "variants": ["лесу"], "kind": "grammar"},     # exact
        {"n": 1, "start": 0, "end": 2, "wrong": "пошли", "variants": ["пошёл"], "kind": "grammar"},    # wrong offsets
        {"n": 1, "start": 0, "end": 3, "wrong": "дом", "variants": ["дома"], "kind": "spelling"},      # not in the text
        {"n": 2, "start": 0, "end": 6, "wrong": "второй", "variants": [], "kind": "spelling"},         # nothing offered
        {"n": 3, "start": 0, "end": 1, "wrong": "x", "variants": ["y"], "kind": "spelling"},           # no such line
        {"n": 1, "start": 3, "end": 8, "wrong": "пошли", "variants": ["пошли"], "kind": "spelling"},   # same as wrong
        {"n": 1, "start": 20, "end": 22, "wrong": "ес", "variants": ["ез"], "kind": "odd"},            # overlaps
        "junk",
    ]}
    got = sp.read_model(value, lines)
    assert got[1] == []
    assert [(i["word"], i["start"], i["end"], i["suggestions"], i["kind"]) for i in got[0]] == [
        ("лес", 19, 22, ["лесу"], "grammar"), ("пошли", 3, 8, ["пошёл"], "grammar")]
    assert sp.read_model({"nothing": 1}, lines) == [[], []]
    assert sp.read_model(None, lines) == [[], []]


def test_variants_are_cleaned_and_limited():
    value = {"items": [{"n": 1, "start": 0, "end": 4, "wrong": " бола ",
                        "variants": ["была", "была", " бала ", "", 5, "x" * 200, "a", "b", "c", "d"], "kind": "spelling"}]}
    got = sp.read_model(value, ["бола тут"])[0]
    assert got[0]["word"] == "бола" and got[0]["suggestions"] == ["была", "бала", "a", "b", "c"]


def test_the_schema_is_tight_and_strict():
    item = sp.MODEL_SCHEMA["properties"]["items"]["items"]
    assert set(item["required"]) == {"n", "start", "end", "wrong", "variants", "kind"}
    assert item["additionalProperties"] is False and item["properties"]["kind"]["enum"] == list(sp.KINDS)
    json.dumps(sp.MODEL_SCHEMA)


# -- a newer check cancels an older one ---------------------------------------------------------------------


async def test_a_newer_check_cancels_the_one_still_waiting_for_the_model():
    latest = sp.Latest()
    started = asyncio.Event()
    cancelled = []

    async def slow():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "old"

    async def quick():
        return "new"

    first = asyncio.create_task(latest.run(slow()))
    await started.wait()
    second = await latest.run(quick())
    assert second == (True, "new")
    assert await first == (False, None) and cancelled == [True]
    assert latest.task is None


async def test_a_failure_of_the_newest_check_reaches_the_caller():
    latest = sp.Latest()

    async def broken():
        raise sp.SpellingFailed("нет связи")

    with pytest.raises(sp.SpellingFailed):
        await latest.run(broken())
