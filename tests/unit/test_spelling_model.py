"""Built-in plugin «Орфография», the model as the checker and why the plugin «did not work at all» (owner,
2026-11-11, «Привит я бола» typed on an Android tablet and nothing happened): every word of that line is a real
word, so the dictionary services find nothing, and only a model sees the mistakes. Here: sentences, reading the
model's answer (offsets checked against the text, made-up fragments dropped), only new sentences sent, names and
the dictionary filtered. Then the owner's report from the tablet: the model check never answered (a thinking model
spent the whole 700-token cap on thinking), so: room for the answer, failures said plainly, a newer check waiting
for the running one instead of cancelling it, both engines merged, positions in UTF-16 units for the page."""

from __future__ import annotations

import asyncio
import importlib.util
import json

import pytest

from tavern.core.llm import LLMClient
from tavern.paths import BUILTIN_PLUGINS_DIR
from tavern.providers.base import GenerationRequest, Provider, ProviderError, StreamEvent, Usage

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


# -- why the model check «did not work» (owner, 2026-11-11, tablet) -------------------------------------------


class ThinkingProvider(Provider):
    """A thinking model (Gemini 2.5 / 3 by default): its thinking counts in maxOutputTokens, so with a small cap the
    whole budget goes on thinking and the answer comes back empty, as the real service does."""

    kind = "fake"
    THINKING = 1500

    def __init__(self, answer: str):
        self.answer = answer
        self.caps: list[int] = []

    async def stream(self, request: GenerationRequest):
        self.caps.append(request.max_tokens)
        if request.max_tokens and request.max_tokens <= self.THINKING:
            yield StreamEvent("done", usage=Usage(10, request.max_tokens), finish_reason="max_tokens")
            return
        yield StreamEvent("text", self.answer)
        yield StreamEvent("done", usage=Usage(10, self.THINKING + 20), finish_reason="stop")

    async def list_models(self):
        return []


async def test_regression_a_thinking_model_has_room_left_for_the_answer():
    # 700 tokens all went on thinking: the reply was empty, twice, and the check failed every time.
    answer = json.dumps(owner_answer([OWNER_TEXT]), ensure_ascii=False)
    provider = ThinkingProvider(answer)
    found = await sp.model_check(OWNER_TEXT, settings(), [], LLMClient(provider, model="gemini-3-flash"))
    assert [i["word"] for i in found] == ["Привит", "бола"]
    assert provider.caps == [sp.MODEL_MAX_TOKENS] and sp.MODEL_MAX_TOKENS > 4 * ThinkingProvider.THINKING


async def test_an_empty_answer_says_why_in_plain_words():
    provider = ThinkingProvider("{}")
    provider.THINKING = 10**6  # thinks past any cap
    with pytest.raises(sp.SpellingFailed, match="пустой ответ"):
        await sp.model_check("Совсем новая строка", settings(), [], LLMClient(provider, model="m"))
    provider = ThinkingProvider("не JSON вовсе")
    with pytest.raises(sp.SpellingFailed, match="не в том виде"):
        await sp.model_check("Ещё одна строка", settings(), [], LLMClient(provider, model="m"))


async def test_a_failing_service_is_said_in_russian_with_its_reason():
    class Down(FakeModel):
        async def generate_json(self, messages, **opts):
            raise ProviderError("Gemini returned 400: API key not valid", reason="auth")

    with pytest.raises(sp.SpellingFailed, match="модель не ответила: Gemini returned 400"):
        await sp.model_check("Строка", settings(), [], Down(None))


# -- one model check at a time, never cancelled ---------------------------------------------------------------


async def test_a_newer_check_waits_for_the_running_one_instead_of_cancelling_it():
    # Cancelling meant a slow model never finished one while the person typed with pauses (and a «спрашивать каждый
    # раз» question closed under the finger). Now the running one ends and fills the memory; of the ones waiting
    # only the newest runs.
    latest = sp.Latest()
    started = asyncio.Event()
    release = asyncio.Event()
    ran = []

    async def slow():
        started.set()
        await release.wait()
        ran.append("old")
        return "old"

    async def middle():
        ran.append("middle")
        return "middle"

    async def newest():
        ran.append("new")
        return "new"

    first = asyncio.create_task(latest.run(slow()))
    await started.wait()
    second = asyncio.create_task(latest.run(middle()))
    third = asyncio.create_task(latest.run(newest()))
    await asyncio.sleep(0)
    release.set()
    assert await first == (True, "old")
    assert await second == (False, None) and await third == (True, "new")
    assert ran == ["old", "new"]


async def test_a_page_that_went_away_does_not_stop_the_model_answer_landing_in_memory():
    latest = sp.Latest()
    release = asyncio.Event()
    done = []

    async def work():
        await release.wait()
        done.append(True)
        return "x"

    caller = asyncio.create_task(latest.run(work()))
    await asyncio.sleep(0)
    caller.cancel()
    release.set()
    await asyncio.sleep(0.01)
    assert done == [True]


# -- both engines, positions for the page ---------------------------------------------------------------------


def test_marks_of_both_engines_merge_and_the_models_win_on_one_place():
    base = [{"start": 0, "end": 6, "word": "Привит", "suggestions": ["Привет"]},
            {"start": 15, "end": 21, "word": "превет", "suggestions": ["привет"]}]
    model = [{"start": 0, "end": 6, "word": "Привит", "suggestions": ["Привет,"], "source": "model"},
             {"start": 9, "end": 13, "word": "бола", "suggestions": ["была"], "source": "model"}]
    got = sp.merge(base, model)
    assert [(i["word"], i["suggestions"]) for i in got] == [
        ("Привит", ["Привет,"]), ("бола", ["была"]), ("превет", ["привет"])]
    assert sp.merge(base, []) == base and sp.merge([], model) == model


def test_positions_reach_the_page_in_utf16_units():
    text = "Ну 😊 Привит я бола"
    start = text.index("Привит")
    got = sp.to_units(text, [{"start": start, "end": start + 6, "word": "Привит"}])
    assert (got[0]["start"], got[0]["end"]) == (start + 1, start + 7)  # the emoji is two units in JavaScript
    plain = [{"start": 0, "end": 6, "word": "Привит"}]
    assert sp.to_units("Привит я бола", plain) is plain


def test_model_items_are_marked_as_the_models_and_capped():
    many = {"items": [{"n": 1, "start": 0, "end": 1, "wrong": "а", "variants": ["б"], "kind": "spelling"}] * 100}
    got = sp.read_model(many, ["а"])[0]
    assert len(got) == 1 and got[0]["source"] == "model"


def test_the_dictionary_service_runs_beside_the_model():
    assert sp.base_settings(settings())["engine"] == "languagetool"
    assert sp.base_settings(settings(base="yandex"))["engine"] == "yandex"
    assert sp.base_settings(settings(base="off")) is None
    plain = {"engine": "yandex"}
    assert sp.base_settings(plain) is plain


async def test_a_failure_of_the_newest_check_reaches_the_caller():
    latest = sp.Latest()

    async def broken():
        raise sp.SpellingFailed("нет связи")

    with pytest.raises(sp.SpellingFailed):
        await latest.run(broken())
