"""Built-in plugin «Орфография» (pure parts): request building and limits, reading saved answers of
LanguageTool and Yandex Speller, ignoring names / macros / code, the dictionary, the autocorrect
rules and a service that is down. No test touches the internet."""

from __future__ import annotations

import importlib.util

import httpx
import pytest

from tavern.paths import BUILTIN_PLUGINS_DIR

spec = importlib.util.spec_from_file_location("test_builtin_spelling", BUILTIN_PLUGINS_DIR / "spelling" / "server.py")
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

TEXT = "Привет, я пишу превет сообщение и ошибку допустил."
# Saved samples of answers (the shape of the services' JSON).
LT_SAMPLE = {"software": {"name": "LanguageTool"}, "language": {"code": "ru-RU"}, "matches": [
    {"message": "Возможна ошибка", "offset": 15, "length": 6,
     "replacements": [{"value": "привет"}], "rule": {"id": "MORFOLOGIK_RULE_RU_RU", "issueType": "misspelling",
                                                      "category": {"id": "TYPOS"}}},
    {"message": "стиль", "offset": 0, "length": 6, "replacements": [{"value": "Здравствуйте"}],
     "rule": {"id": "STYLE", "issueType": "style", "category": {"id": "STYLE"}}},
]}
YANDEX_SAMPLE = [{"code": 1, "pos": 15, "row": 0, "col": 15, "len": 6, "word": "превет", "s": ["привет", "превед"]}]


@pytest.fixture(autouse=True)
def fresh():
    sp._cache.clear()
    sp.transport = None
    yield
    sp.transport = None


def settings(**extra):
    return {"engine": "languagetool", "url": "", "language": "auto", "max_chars": 1500, "dictionary": "", **extra}


def issue(word, start, suggestions):
    return {"word": word, "start": start, "end": start + len(word), "suggestions": suggestions}


# -- what may be sent and how -------------------------------------------------------------------


def test_code_links_and_macros_are_blanked_before_sending():
    text = "Скажи {{char}} привет `rm -rf` и https://x.io/a_b тест\n```\nкод тут\n```\nконец"
    masked = sp.mask(text)
    assert len(masked) == len(text)
    for hidden in ("{{char}}", "rm -rf", "https", "код тут"):
        assert hidden not in masked
    assert "Скажи" in masked and "конец" in masked and masked.count("\n") == text.count("\n")


def test_only_the_last_part_is_sent_from_a_word_boundary():
    text = "слово " * 100
    offset, piece = sp.window(text, 50)
    assert piece == text[offset:] and len(piece) <= 50
    offset, piece = sp.window("абвгд " * 20, 52)  # the cut would fall inside a word: start at the next one
    assert piece.startswith(" ") or piece.startswith("абвгд ")
    assert sp.window("коротко", 50) == (0, "коротко")
    assert len(sp.window("а" * 9000, 99999)[1]) <= sp.HARD_LIMIT


def test_requests_for_each_service_and_own_address():
    url, fields = sp.build_request("languagetool", "", "текст", "auto")
    assert url == "https://api.languagetool.org/v2/check"
    assert fields["text"] == "текст" and fields["language"] == "auto" and fields["enabledOnly"] == "true"
    assert fields["enabledCategories"] == "TYPOS"
    url, fields = sp.build_request("languagetool", "http://127.0.0.1:8010/v2/check", "t", "ru")
    assert url == "http://127.0.0.1:8010/v2/check" and fields["language"] == "ru-RU"
    url, fields = sp.build_request("yandex", "", "text", "auto")
    assert url.startswith("https://speller.yandex.net/") and fields["lang"] == "ru,en"
    with pytest.raises(sp.SpellingFailed, match="http"):
        sp.build_request("yandex", "ftp://x", "t", "ru")
    with pytest.raises(sp.SpellingFailed):
        sp.build_request("other", "", "t", "ru")


# -- reading answers ----------------------------------------------------------------------------


def test_languagetool_answer_keeps_only_spelling():
    assert TEXT[15:21] == "превет"
    found = sp.parse("languagetool", LT_SAMPLE, TEXT)
    assert found == [{"start": 15, "end": 21, "word": "превет", "suggestions": ["привет"]}]


def test_yandex_answer():
    found = sp.parse("yandex", YANDEX_SAMPLE, TEXT)
    assert found == [{"start": 15, "end": 21, "word": "превет", "suggestions": ["привет", "превед"]}]


def test_positions_in_utf16_units_survive_emoji():
    text = "😀 превет"
    data = {"matches": [{"offset": 3, "length": 6, "replacements": [{"value": "привет"}],
                         "rule": {"category": {"id": "TYPOS"}}}]}
    [found] = sp.parse("languagetool", data, text)
    assert text[found["start"]:found["end"]] == "превет"


def test_nonsense_answers_are_an_error():
    with pytest.raises(sp.SpellingFailed, match="непонятный"):
        sp.parse("languagetool", {"oops": 1}, "текст")
    with pytest.raises(sp.SpellingFailed, match="непонятный"):
        sp.parse("yandex", {"oops": 1}, "текст")


# -- what is shown ------------------------------------------------------------------------------


def test_names_macros_code_and_the_dictionary_are_not_mistakes():
    text = "Алисе сказал {{char}} про `скрипт` и превет, Кролик."
    items = [issue(w, text.index(w), s) for w, s in
             (("Алисе", ["Алисе"]), ("char", []), ("скрипт", []), ("превет", ["привет"]), ("Кролик", ["Кролики"]))]
    shown = sp.select(items, text, dictionary="кролик", names=["Алиса", "Боб"])
    assert [i["word"] for i in shown] == ["превет"]
    again = sp.select(items, text, dictionary="", names=[])
    assert "Кролик" in [i["word"] for i in again]


def test_names_in_case_forms_and_odd_words():
    tokens = sp.name_tokens(["Алиса Лидделл"])
    assert sp.is_name("Алисой", tokens) and sp.is_name("алиса", tokens) and sp.is_name("Лидделла", tokens)
    assert not sp.is_name("Алкоголь", tokens) and not sp.is_name("ли", tokens)
    text = "NASA fff3 a_b"
    items = [issue("NASA", 0, ["X"]), issue("fff3", 5, ["fff"]), issue("a_b", 10, ["ab"])]
    assert sp.select(items, text, dictionary="", names=[]) == []


def test_dictionary_adds_once_and_is_case_blind():
    raw = sp.add_to_dictionary("", "Кролик")
    assert sp.add_to_dictionary(raw, "кролик") == raw
    assert sp.add_to_dictionary(raw, "Шляпник").splitlines() == ["Кролик", "Шляпник"]
    with pytest.raises(sp.SpellingFailed):
        sp.clean_word("два слова")
    with pytest.raises(sp.SpellingFailed):
        sp.clean_word("")


# -- autocorrect rules --------------------------------------------------------------------------


def sure(text, word, suggestions):
    return sp.is_sure(issue(word, text.index(word), suggestions), text)


def test_only_sure_mistakes_are_corrected_automatically():
    assert sure("я пишу превет", "превет", ["привет"])
    assert not sure("я пишу превет", "превет", ["привет", "превед"])      # two ways
    assert not sure("я пишу превет", "превет", [])                         # no way
    assert not sure("я пишу абвгд", "абвгд", ["совсем другое слово"])      # too far from the word
    assert not sure("я пишу ку", "ку", ["кум"])                            # too short
    assert not sure("мы идём к Гарольд", "Гарольд", ["Гарольда"])          # a capital inside a sentence
    assert sure("Привет. Превет", "Превет", ["Привет"])                    # a capital after a full stop
    assert sure("Превет", "Превет", ["Привет"])


def test_select_marks_sure_ones():
    text = "я пишу превет и вот дрязг"
    items = [issue("превет", 7, ["привет"]), issue("дрязг", 20, ["дрязг1", "дрязги"])]
    shown = sp.select(items, text, dictionary="", names=[])
    assert [(i["word"], i["sure"]) for i in shown] == [("превет", True), ("дрязг", False)]


# -- the whole check against a fake service ------------------------------------------------------


async def test_check_sends_only_masked_text_and_reports_absolute_positions():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content.decode())
        piece = httpx.QueryParams(request.content.decode())["text"]
        at = piece.find("превет")
        return httpx.Response(200, json={"matches": [{"offset": at, "length": 6, "replacements": [{"value": "привет"}],
                                                      "rule": {"category": {"id": "TYPOS"}}}]})

    sp.transport = httpx.MockTransport(handler)
    found = await sp.check("Это превет {{char}} `секрет`", settings(), ["Боб"])
    assert "char" not in seen[0] and "%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82" not in seen[0]
    assert found[0]["word"] == "превет" and found[0]["sure"] is True and (found[0]["start"], found[0]["end"]) == (4, 10)
    before = len(seen)
    await sp.check("Это превет {{char}} `секрет`", settings(), [])
    assert len(seen) == before  # the same text is answered from memory


async def test_window_offsets_are_added_back_and_size_is_limited():
    long_text = "слово " * 50 + "превет"
    body = []

    def yandex(request: httpx.Request) -> httpx.Response:
        body.append(request.content.decode())
        piece = httpx.QueryParams(request.content.decode())["text"]
        return httpx.Response(200, json=[{"pos": piece.index("превет"), "len": 6, "word": "превет", "s": ["привет"]}])

    sp.transport = httpx.MockTransport(yandex)
    found = await sp.check(long_text, settings(engine="yandex", max_chars=100), [])
    assert len(httpx.QueryParams(body[0])["text"]) <= 100
    assert long_text[found[0]["start"]:found[0]["end"]] == "превет"


async def test_nothing_is_sent_for_commands_or_text_without_letters():
    def refuse(request):
        raise AssertionError("must not call the service")

    sp.transport = httpx.MockTransport(refuse)
    for text in ("", "   ", "/roll 2d6", "12345 !!!", "{{char}} `код`"):
        assert await sp.check(text, settings(), []) == []


async def test_service_down_is_a_quiet_error():
    sp.transport = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(sp.SpellingFailed, match="503"):
        await sp.check("Это превет", settings(), [])
    sp.transport = httpx.MockTransport(lambda r: httpx.Response(429))
    with pytest.raises(sp.SpellingFailed, match="временно"):
        await sp.check("Это превет2 текст", settings(), [])
    sp.transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>"))
    with pytest.raises(sp.SpellingFailed, match="непонятный"):
        await sp.check("Это превет3 текст", settings(), [])

    def down(request):
        raise httpx.ConnectError("no route")

    sp.transport = httpx.MockTransport(down)
    with pytest.raises(sp.SpellingFailed, match="Нет связи"):
        await sp.check("Это превет4 текст", settings(), [])
