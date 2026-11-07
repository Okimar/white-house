"""✎ Орфография: spell checking of what is typed in the message box, no model, no key, no spending.

The text goes to an outside service (LanguageTool by default, Yandex Speller as the other choice,
or the person's own server through the address setting): only the text of the message box, never
the chat. Before it leaves, code, links, e-mails and macros like {{char}} are blanked out with
spaces (offsets stay the same), and only the last `max_chars` characters are sent. The page asks
after a pause in typing; this part only builds the request, reads the answer and decides which
mistakes to show (not names, not the person's dictionary, not odd words) and which are sure enough
for automatic correction.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

import httpx

from tavern.errors import AppError

DEFAULT_URLS = {
    "languagetool": "https://api.languagetool.org/v2/check",
    "yandex": "https://speller.yandex.net/services/spellservice.json/checkText",
}
LT_LANGUAGES = {"auto": "auto", "ru": "ru-RU", "en": "en-US", "uk": "uk-UA"}
YANDEX_LANGUAGES = {"auto": "ru,en", "ru": "ru", "en": "en", "uk": "uk"}
TIMEOUT = 15.0
HARD_LIMIT = 4000  # whatever the setting says, never more than this leaves
MAX_SUGGESTIONS = 5
MAX_WORDS = 2000
CACHE_SIZE = 24
transport: httpx.AsyncBaseTransport | None = None  # tests swap the network out

_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}

PROTECTED = re.compile(
    r"```.*?(?:```|\Z)"          # fenced code (an unclosed fence runs to the end)
    r"|`[^`\n]+`"                # inline code
    r"|\{\{.*?\}\}"              # macros {{char}}, {{user}}, {{random:a,b}}
    r"|<[^<>\n]{1,200}>"         # tags
    r"|(?:https?://|www\.)\S+"   # links
    r"|[\w.+-]+@[\w-]+\.[\w.-]+",  # e-mail
    re.DOTALL,
)
SENTENCE_END = ".!?…\n"


class SpellingFailed(AppError):
    code = "spelling_failed"
    status = 502


# -- what may be sent ---------------------------------------------------------------------------


def protected_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in PROTECTED.finditer(text)]


def mask(text: str) -> str:
    """The text with code, links, e-mails and macros replaced by spaces (newlines kept)."""
    chars = list(text)
    for start, end in protected_spans(text):
        for i in range(start, end):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def window(text: str, limit: int) -> tuple[int, str]:
    """(offset, piece): the last `limit` characters, starting at a word boundary."""
    limit = max(1, min(int(limit), HARD_LIMIT))
    if len(text) <= limit:
        return 0, text
    start = len(text) - limit
    if not text[start - 1].isspace() and not text[start].isspace():
        while start < len(text) and not text[start].isspace():
            start += 1
    return start, text[start:]


def build_request(engine: str, url: str, text: str, language: str) -> tuple[str, dict[str, str]]:
    """(address, form fields) of one check."""
    address = (url or "").strip() or DEFAULT_URLS.get(engine, "")
    if not re.match(r"^https?://\S+$", address):
        raise SpellingFailed("Адрес сервиса проверки должен начинаться с http:// или https://")
    if engine == "yandex":
        return address, {"text": text, "lang": YANDEX_LANGUAGES.get(language, "ru,en"), "options": "6",
                         "format": "plain"}
    if engine == "languagetool":
        fields = {"text": text, "language": LT_LANGUAGES.get(language, "auto"),
                  "enabledOnly": "true", "enabledCategories": "TYPOS"}
        if fields["language"] == "auto":
            fields["preferredVariants"] = "en-US"
        return address, fields
    raise SpellingFailed(f"Неизвестный сервис проверки: {engine}")


# -- reading the answer -------------------------------------------------------------------------


def _unit_map(text: str) -> list[int] | None:
    """UTF-16 unit position -> index in `text` (services count in UTF-16); None if they are the same."""
    if all(ord(c) < 0x10000 for c in text):
        return None
    out: list[int] = []
    for index, char in enumerate(text):
        out.extend([index] * (2 if ord(char) >= 0x10000 else 1))
    out.append(len(text))
    return out


def parse(engine: str, data: Any, text: str) -> list[dict[str, Any]]:
    """Mistakes as {start, end, word, suggestions} in `text` (the checked piece)."""
    units = _unit_map(text)

    def at(position: int) -> int:
        if units is None:
            return position
        return units[position] if 0 <= position < len(units) else -1

    found: list[dict[str, Any]] = []
    try:
        if engine == "languagetool":
            for m in data["matches"]:
                category = (m.get("rule") or {}).get("category", {}).get("id")
                kind = (m.get("rule") or {}).get("issueType")
                if (category and category != "TYPOS") and kind != "misspelling":
                    continue
                start = at(int(m["offset"]))
                end = at(int(m["offset"]) + int(m["length"]))
                suggestions = [r["value"] for r in m.get("replacements", []) if isinstance(r.get("value"), str)]
                found.append({"start": start, "end": end, "suggestions": suggestions})
        elif engine == "yandex":
            for m in data:
                start, length = int(m["pos"]), int(m["len"])
                word = str(m.get("word", ""))
                suggestions = [s for s in m.get("s", []) if isinstance(s, str)]
                if text[start:start + length] == word:
                    found.append({"start": start, "end": start + length, "suggestions": suggestions})
                else:  # counted in UTF-16 after all
                    s, e = at(start), at(start + length)
                    if s >= 0 and e >= 0 and text[s:e] == word:
                        found.append({"start": s, "end": e, "suggestions": suggestions})
        else:
            raise SpellingFailed(f"Неизвестный сервис проверки: {engine}")
    except (KeyError, TypeError, ValueError, AttributeError):
        raise SpellingFailed("Сервис проверки прислал непонятный ответ") from None
    out = []
    for item in found:
        if not (0 <= item["start"] < item["end"] <= len(text)):
            continue
        item["word"] = text[item["start"]:item["end"]]
        item["suggestions"] = [s for s in dict.fromkeys(item["suggestions"])
                               if s != item["word"]][:MAX_SUGGESTIONS]
        out.append(item)
    return out


# -- which mistakes to show ---------------------------------------------------------------------


def dictionary_words(raw: str) -> set[str]:
    return {w.strip().casefold() for w in raw.splitlines() if w.strip()}


def name_tokens(names: list[str]) -> list[str]:
    tokens: list[str] = []
    for name in names:
        tokens += [t.casefold() for t in re.split(r"[\s,]+", name or "") if len(t) >= 2]
    return tokens


def is_name(word: str, tokens: list[str]) -> bool:
    """The word is a name from the chat or its case form (Алисе, Алисой for Алиса)."""
    w = word.casefold()
    for token in tokens:
        if w == token:
            return True
        if len(token) >= 4:
            stem = token[:max(3, len(token) - 2)]
            if w.startswith(stem) and len(w) <= len(token) + 3:
                return True
    return False


def keep(issue: dict[str, Any], spans: list[tuple[int, int]], words: set[str], tokens: list[str]) -> bool:
    word = issue["word"]
    if any(issue["start"] < e and issue["end"] > s for s, e in spans):
        return False
    if word.casefold() in words or is_name(word, tokens):
        return False
    if not any(c.isalpha() for c in word) or any(c.isdigit() or c == "_" for c in word):
        return False
    if len(word) >= 2 and word.isupper():  # abbreviations
        return False
    return True


def is_sure(issue: dict[str, Any], text: str) -> bool:
    """Safe to correct without asking: one suggestion, close to the word, not a name-looking word."""
    suggestions = issue["suggestions"]
    word = issue["word"]
    if len(suggestions) != 1 or len(word) < 3 or not word.isalpha():
        return False
    suggestion = suggestions[0]
    if not suggestion.strip() or suggestion.casefold() == word.casefold():
        return False
    if SequenceMatcher(None, word.casefold(), suggestion.casefold()).ratio() < 0.6:
        return False
    if word[0].isupper():  # a capital letter inside a sentence is most likely a name
        before = text[:issue["start"]].rstrip(" \t")
        if before and before[-1] not in SENTENCE_END:
            return False
    return True


def select(issues: list[dict[str, Any]], text: str, *, dictionary: str, names: list[str],
           offset: int = 0) -> list[dict[str, Any]]:
    """Filter parsed mistakes of the piece starting at `offset` in `text`; positions become absolute."""
    spans = protected_spans(text)
    words = dictionary_words(dictionary)
    tokens = name_tokens(names)
    out = []
    for issue in issues:
        item = {**issue, "start": issue["start"] + offset, "end": issue["end"] + offset}
        if keep(item, spans, words, tokens):
            item["sure"] = is_sure(item, text)
            out.append(item)
    return out


# -- the check ----------------------------------------------------------------------------------


async def fetch(engine: str, url: str, language: str, piece: str) -> list[dict[str, Any]]:
    """Ask the service about one (already masked) piece. Remembers the last few answers."""
    key = (engine, url, language, piece)
    if key in _cache:
        return _cache[key]
    address, fields = build_request(engine, url, piece, language)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            response = await client.post(address, data=fields)
    except httpx.HTTPError as exc:
        raise SpellingFailed(f"Нет связи с сервисом проверки орфографии: {type(exc).__name__}") from None
    if response.status_code in (429, 403):
        raise SpellingFailed("Сервис проверки орфографии временно отказал (много запросов)")
    if response.status_code >= 400:
        raise SpellingFailed(f"Сервис проверки орфографии ответил ошибкой {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise SpellingFailed("Сервис проверки орфографии прислал непонятный ответ") from None
    issues = parse(engine, data, piece)
    if len(_cache) >= CACHE_SIZE:
        _cache.pop(next(iter(_cache)))
    _cache[key] = issues
    return issues


async def check(text: str, settings: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    """Mistakes of `text` with absolute positions; [] for commands and texts without letters."""
    if not text.strip() or text.lstrip().startswith("/"):
        return []
    # Masking runs over the whole text first, so a code fence opened before the window still hides code.
    offset, _ = window(text, settings.get("max_chars", 1500))
    sent = mask(text)[offset:]
    if not any(c.isalpha() for c in sent):
        return []
    issues = await fetch(settings.get("engine", "languagetool"), settings.get("url", ""),
                         settings.get("language", "auto"), sent)
    return select(issues, text, dictionary=settings.get("dictionary", ""), names=names, offset=offset)


def clean_word(word: Any) -> str:
    word = str(word or "").strip()
    if not word or len(word) > 60 or any(c.isspace() for c in word):
        raise SpellingFailed("Слово для словаря: одно слово без пробелов")
    return word


def add_to_dictionary(raw: str, word: str) -> str:
    lines = [w for w in raw.splitlines() if w.strip()]
    if word.casefold() not in dictionary_words(raw):
        if len(lines) >= MAX_WORDS:
            raise SpellingFailed("Словарь заполнен")
        lines.append(word)
    return "\n".join(lines)


def setup(api: Any) -> None:
    def names_of(chat_id: str) -> list[str]:
        if not chat_id:
            return []
        try:
            from tavern.services import cast

            members = [m["name"] for m in api.ctx.members.list(chat_id)]
            return members + [cast.user_name(api.ctx, chat_id)]
        except Exception:  # noqa: BLE001 - names only reduce false alarms; never fail the check for them
            api.log.debug("Names for the spell check not found", exc_info=True)
            return []

    async def act_check(payload: dict[str, Any]) -> dict[str, Any]:
        """{text, chat_id} -> {issues: [{start, end, word, suggestions, sure}]}; the page sends only the typed text."""
        text = payload.get("text")
        if not isinstance(text, str):
            raise SpellingFailed("Нет текста для проверки")
        issues = await check(text, api.settings, names_of(str(payload.get("chat_id") or "")))
        return {"issues": issues}

    def act_add_word(payload: dict[str, Any]) -> dict[str, Any]:
        """{word} -> put the word into the personal dictionary (the plugin's «Свой словарь» setting)."""
        word = clean_word(payload.get("word"))
        row = api.ctx.plugin_repo.get(api.id) or {}
        settings = dict(row.get("settings", {}))
        settings["dictionary"] = add_to_dictionary(api.settings.get("dictionary", ""), word)
        api.ctx.plugin_repo.save(api.id, settings=settings)
        return {"dictionary": settings["dictionary"]}

    api.action("check", act_check)
    api.action("add_word", act_add_word)
