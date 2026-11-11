"""✎ Орфография: spell checking of what is typed in the message box.

The text goes to an outside service (LanguageTool by default, Yandex Speller as the other choice,
or the person's own server through the address setting), and with the engine «model» also to a model of the
person's own connection (through the plugin's model access: allowed in Settings → «Дополнения», its limits and
spending): only the text of the message box, never the chat. Dictionary services only see words that do
not exist; a model also sees real words in the wrong place («Привит я бола» → «Привет, я была»). With the model
on, the dictionary service still runs (setting `base`): the page asks both apart (part «dict» / «model»), shows
the dictionary's marks at once and the model's when they come, the model's winning where both mark one place.
Positions go to the page in UTF-16 units (JavaScript strings), so an emoji before a word does not shift its mark.
Before it leaves, code, links, e-mails and macros like {{char}} are blanked out with
spaces (offsets stay the same), and only the last `max_chars` characters are sent. The page asks
after a pause in typing; this part only builds the request, reads the answer and decides which
mistakes to show (not names, not the person's dictionary, not odd words) and which are sure enough
for automatic correction.
"""

from __future__ import annotations

import asyncio
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


MODEL_CACHE_SIZE = 200   # sentences already checked by the model (per connection, model, language)
MODEL_MAX_LINES = 12     # sentences in one model request; older ones wait for the next pause
# Thinking models (Gemini 2.5 / 3 think by default, and a plugin's client gets none of the connection's thinking
# settings) count their thinking in this cap: 700 left nothing for the answer, the reply came back empty and the
# check «did not work». The answer itself is short; the cap only stops a runaway.
MODEL_MAX_TOKENS = 8000
MODEL_MAX_ITEMS = 40
AUTO_MAX_DISTANCE = 2    # the dictionary's autocorrect: at most this many letters changed
MAX_WRONG = 60
LANGUAGE_NAMES = {"auto": "Russian or English", "ru": "Russian", "en": "English", "uk": "Ukrainian"}
KINDS = ("spelling", "grammar", "punctuation")
_model_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}

MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"},
            "start": {"type": "integer"},
            "end": {"type": "integer"},
            "wrong": {"type": "string"},
            "variants": {"type": "array", "items": {"type": "string"}},
            "kind": {"type": "string", "enum": list(KINDS)},
        },
        "required": ["n", "start", "end", "wrong", "variants", "kind"],
        "additionalProperties": False,
    }}},
    "required": ["items"],
    "additionalProperties": False,
}

MODEL_SYSTEM = """You proofread chat messages a person is typing ({language}). Find:
- spelling mistakes and typos, INCLUDING typos that make another real word ("Привит я бола" means "Привет, я была": both "Привит" and "бола" are mistakes);
- grammar mistakes (agreement, case, gender, verb forms);
- clearly missing or wrong punctuation.
Leave alone: style, word choice, slang or casual spelling used on purpose, roleplay actions in *asterisks*, and these names: {names}.
Each line is "N: text". For every mistake give:
n = the line number; wrong = the exact fragment as written, as short as possible (usually one word, never a whole sentence);
start, end = character offsets of `wrong` inside that line's text (0-based, end exclusive);
variants = 1 to 5 corrections of that fragment only, best first, same language;
kind = spelling | grammar | punctuation (a missing comma: wrong = the word before it, variant = the word with the comma).
No mistakes: {{"items": []}}."""


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


def distance(a: str, b: str) -> int:
    """Letters to add, drop or change to turn `a` into `b` (Levenshtein)."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


# *actions* and quoted speech: the dictionary's autocorrect leaves them as typed (underlines still show).
QUOTED = re.compile(r'\*[^*\n]+\*|"[^"\n]+"|«[^»\n]+»|“[^”\n]+”|„[^“”\n]+[“”]')


def is_sure(issue: dict[str, Any], text: str) -> bool:
    """Safe for the dictionary's autocorrect: an obvious typo of one word. One suggestion, itself one word, at most
    AUTO_MAX_DISTANCE letters away, not a name-looking word, not inside *actions* or quotes."""
    suggestions = issue["suggestions"]
    word = issue["word"]
    if len(suggestions) != 1 or len(word) < 3 or not word.isalpha():
        return False
    suggestion = suggestions[0]
    if not suggestion.isalpha() or suggestion.casefold() == word.casefold():
        return False
    if distance(word.casefold(), suggestion.casefold()) > AUTO_MAX_DISTANCE:
        return False
    if SequenceMatcher(None, word.casefold(), suggestion.casefold()).ratio() < 0.6:
        return False
    if any(m.start() <= issue["start"] and issue["end"] <= m.end() for m in QUOTED.finditer(text)):
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
    return sorted(out, key=lambda i: i["start"])


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


# -- the model as the checker ---------------------------------------------------------------------


def sentences(masked: str, offset: int = 0) -> list[tuple[int, str]]:
    """(absolute start, text) of each sentence with letters in `masked[offset:]`, trimmed, one line each."""
    out = []
    for m in re.finditer(r"[^.!?…\n]+[.!?…]*", masked[offset:]):
        piece = m.group(0)
        lead = len(piece) - len(piece.lstrip())
        piece = piece.strip()
        if piece and any(c.isalpha() for c in piece):
            out.append((offset + m.start() + lead, piece))
    return out


def model_messages(lines: list[str], language: str, names: list[str]) -> list[Any]:
    from tavern.providers.base import ChatMessage

    shown = ", ".join(dict.fromkeys(n for n in names if n))[:400] or "(none)"
    system = MODEL_SYSTEM.format(language=LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["auto"]), names=shown)
    return [ChatMessage("system", system),
            ChatMessage("user", "\n".join(f"{i}: {line}" for i, line in enumerate(lines, 1)))]


def place(line: str, wrong: str, hint: Any) -> tuple[int, int] | None:
    """Where `wrong` really is in the line: at the model's offset when it matches, else the occurrence
    nearest to it; None when the fragment is not in the line at all (the model made it up)."""
    if isinstance(hint, int) and not isinstance(hint, bool) and 0 <= hint and line[hint:hint + len(wrong)] == wrong:
        return hint, hint + len(wrong)
    found = [m.start() for m in re.finditer(re.escape(wrong), line)]
    if not found:
        return None
    near = hint if isinstance(hint, int) and not isinstance(hint, bool) else 0
    at = min(found, key=lambda f: abs(f - near))
    return at, at + len(wrong)


def read_model(value: Any, lines: list[str]) -> list[list[dict[str, Any]]]:
    """The model's answer as mistakes per line ({start, end, word, suggestions, kind}, offsets in the line).
    Items whose `wrong` is not in the line, without corrections or overlapping an earlier one are dropped."""
    per_line: list[list[dict[str, Any]]] = [[] for _ in lines]
    items = value.get("items") if isinstance(value, dict) else None
    for item in (items if isinstance(items, list) else [])[:MODEL_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        n, wrong = item.get("n"), item.get("wrong")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(lines) or not isinstance(wrong, str):
            continue
        wrong = wrong.strip()
        if not wrong or len(wrong) > MAX_WRONG:
            continue
        span = place(lines[n - 1], wrong, item.get("start"))
        if span is None:
            continue
        variants = [v.strip() for v in item.get("variants") or [] if isinstance(v, str) and v.strip() and len(v) <= 80]
        variants = [v for v in dict.fromkeys(variants) if v != wrong][:MAX_SUGGESTIONS]
        if not variants:
            continue
        found = per_line[n - 1]
        if any(span[0] < f["end"] and span[1] > f["start"] for f in found):
            continue
        kind = item.get("kind") if item.get("kind") in KINDS else "spelling"
        found.append({"start": span[0], "end": span[1], "word": wrong, "suggestions": variants, "kind": kind,
                      "source": "model"})
    return per_line


async def model_check(text: str, settings: dict[str, Any], names: list[str], llm: Any) -> list[dict[str, Any]]:
    """Mistakes of `text` found by a model (absolute positions, filtered like the services' ones). Only the
    sentences not checked before go out, in one request; `llm` is the plugin's model client."""
    if not text.strip() or text.lstrip().startswith("/"):
        return []
    offset, _ = window(text, settings.get("max_chars", 1500))
    found = sentences(mask(text), offset)
    language = settings.get("language", "auto")
    base = (settings.get("connection", ""), settings.get("model", ""), language)
    todo = list(dict.fromkeys(line for _, line in found if base + (line,) not in _model_cache))[-MODEL_MAX_LINES:]
    if todo:
        value = await ask_model(llm, model_messages(todo, language, names))
        for line, issues in zip(todo, read_model(value, todo)):
            if len(_model_cache) >= MODEL_CACHE_SIZE:
                _model_cache.pop(next(iter(_model_cache)))
            _model_cache[base + (line,)] = issues
    issues: list[dict[str, Any]] = []
    for start, line in found:
        for item in _model_cache.get(base + (line,), []):
            issues.append({**item, "start": item["start"] + start, "end": item["end"] + start})
    return select(issues, text, dictionary=settings.get("dictionary", ""), names=names)


async def ask_model(llm: Any, messages: list[Any]) -> Any:
    """The model's JSON answer; its failures become SpellingFailed with the reason in plain Russian (a refusal of
    the plugin gate, PluginRefused, passes as it is: it already says what to do)."""
    from tavern.core.llm import StructuredOutputError
    from tavern.providers.base import ProviderError

    try:
        return await llm.generate_json(messages, schema=MODEL_SCHEMA, max_tokens=MODEL_MAX_TOKENS, temperature=0)
    except StructuredOutputError as exc:
        if not str((exc.details or {}).get("text") or "").strip():
            raise SpellingFailed("модель прислала пустой ответ (возможно, всё ушло на размышления: "
                                 "выберите быструю модель без размышлений)") from None
        raise SpellingFailed("модель ответила не в том виде, попробуйте другую модель") from None
    except ProviderError as exc:
        raise SpellingFailed(f"модель не ответила: {exc}") from None


class Latest:
    """One model check at a time. A newer one waits for the one already asking the model instead of cancelling it:
    cancelling every older request while the person typed on meant a slow model never finished one, and a
    «спрашивать каждый раз» question closed under the finger. The finished one fills the sentence memory, so the
    newer one sends only what is left. Of the checks waiting, only the newest runs; the others get "stale"."""

    def __init__(self) -> None:
        self.task: asyncio.Future[Any] | None = None
        self.newest = 0

    async def run(self, work: Any) -> tuple[bool, Any]:
        """(fresh, result): fresh is False when a newer check came while this one waited its turn."""
        self.newest += 1
        mine = self.newest
        try:
            while self.task is not None and not self.task.done():
                await asyncio.wait({self.task})
        except asyncio.CancelledError:  # the request itself went away while waiting
            work.close()
            raise
        if mine != self.newest:
            work.close()
            return False, None
        task = asyncio.ensure_future(work)
        self.task = task
        # Shielded: when the page's request goes away the model's answer still lands in the memory.
        return True, await asyncio.shield(task)


def to_units(text: str, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Positions in `text` (Python characters) -> UTF-16 units, as the page's JavaScript counts them."""
    if all(ord(c) < 0x10000 for c in text):
        return issues
    units = [0]
    for c in text:
        units.append(units[-1] + (2 if ord(c) >= 0x10000 else 1))
    return [{**i, "start": units[i["start"]], "end": units[i["end"]]} for i in issues]


def merge(base: list[dict[str, Any]], model: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Both engines' marks without two on one place: the model's win where they overlap."""
    kept = [i for i in base if not any(i["start"] < m["end"] and i["end"] > m["start"] for m in model)]
    return sorted(kept + model, key=lambda i: i["start"])


def base_settings(settings: dict[str, Any]) -> dict[str, Any] | None:
    """The dictionary service's settings; None when there is none (the model alone: «base» is "off")."""
    if settings.get("engine") != "model":
        return settings
    base = settings.get("base", "languagetool")
    return {**settings, "engine": base} if base in DEFAULT_URLS else None


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
    latest = Latest()
    clients: dict[tuple[str, str, str], Any] = {}

    def client_for(chat_id: str) -> Any:
        """One model client per (connection, model, chat): «спрашивать каждый раз» asks once for it, not at
        every pause in typing (background=True); the permission and the money limits apply to every request."""
        key = (str(api.settings.get("connection") or ""), str(api.settings.get("model") or ""), chat_id)
        if key not in clients:
            if len(clients) > 16:
                clients.clear()
            clients[key] = api.llm(connection_id=key[0], model=key[1], chat_id=chat_id or None, background=True)
        return clients[key]

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
        """{text, chat_id, part?} -> {issues: [{start, end, word, suggestions, sure, kind?, source?}], stale?}; the
        page sends only the typed text. part: "dict" = the dictionary service (fast), "model" = the model; none =
        both, merged. Positions are UTF-16 units. stale: a newer model check came while this one waited."""
        text = payload.get("text")
        if not isinstance(text, str):
            raise SpellingFailed("Нет текста для проверки")
        chat_id = str(payload.get("chat_id") or "")
        settings = api.settings
        part = payload.get("part")
        model_on = settings.get("engine") == "model"
        base = base_settings(settings)
        names = names_of(chat_id)

        async def dictionary() -> list[dict[str, Any]]:
            return await check(text, base, names) if base is not None else []

        async def by_model() -> tuple[bool, list[dict[str, Any]]]:
            if not model_on:
                return True, []
            # A refusal (no permission, a limit) and a failing model are AppErrors with a message the page shows.
            return await latest.run(model_check(text, settings, names, client_for(chat_id)))

        if part == "dict":
            return {"issues": to_units(text, await dictionary())}
        if part == "model":
            fresh, issues = await by_model()
            return {"issues": to_units(text, issues)} if fresh else {"issues": [], "stale": True}
        found, (fresh, issues) = await asyncio.gather(dictionary(), by_model())
        if not fresh:
            return {"issues": [], "stale": True}
        return {"issues": to_units(text, merge(found, issues))}

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
