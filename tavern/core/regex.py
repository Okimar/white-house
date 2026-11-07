"""Regex rules, part 1 (stage 11): the JavaScript pattern language, translated to Python `re`, and the rule shape.

People write these patterns for SillyTavern in JavaScript syntax (`/pattern/flags`), the server is
Python. `translate()` turns the JavaScript form into a Python pattern, keeping the MEANING: where
Python means something else it spells the JavaScript meaning out (`\\d` is only 0-9, `\\w` only
ASCII letters, `\\b` follows `\\w`, `$` is the very end, `.` stops at \\r and \\u2028 too, `[^]`,
`[]`, backreferences to groups that took no part). What cannot be said in Python is refused with a
Russian message that names the problem, never changed quietly (see docs/adr/0017-regex-rules.md).

Matching itself never happens here: it runs in a worker process (core/regex_runner.py) that can
be stopped. This module only builds the "spec" the worker takes: {p, f, g, y, parts, trim}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tavern.errors import AppError

MAX_PATTERN = 4000
MAX_REPLACE = 20_000
MAX_NAME = 120
MAX_TRIM = 30

PLACES = ("user", "assistant", "reasoning", "lore")
WHEN = ("display", "prompt", "both", "stored")
MACROS = ("none", "raw", "escaped")
FLAGS_KNOWN = "gimsuyd"

_NL = "\\n\\r\\u2028\\u2029"  # JavaScript line terminators, as they are written inside a Python pattern
_WS = [(9, 13), (32, 32), (0xA0, 0xA0), (0x1680, 0x1680), (0x2000, 0x200A), (0x2028, 0x2029),
       (0x202F, 0x202F), (0x205F, 0x205F), (0x3000, 0x3000), (0xFEFF, 0xFEFF)]
_DIGIT = [(48, 57)]
_WORD = [(48, 57), (65, 90), (95, 95), (97, 122)]
_MAX_CP = 0x10FFFF
_WORD_CLASS = "[A-Za-z0-9_]"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_QUANT = re.compile(r"\{(\d+)(?:,(\d*))?\}")


class RegexError(AppError):
    """A pattern or a rule that cannot be used. The message is Russian and says what is wrong."""
    code = "bad_regex"


# --- ranges ---------------------------------------------------------------------------------


def _complement(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    low = 0
    for lo, hi in sorted(ranges):
        if lo > low:
            out.append((low, lo - 1))
        low = max(low, hi + 1)
    if low <= _MAX_CP:
        out.append((low, _MAX_CP))
    return out


def _cp(code: int) -> str:
    """One code point as text that is safe inside and outside a Python character class."""
    char = chr(code)
    if char.isascii() and char.isalnum():
        return char
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def _class(ranges: list[tuple[int, int]], negate: bool = False) -> str:
    ranges = _merge(ranges)
    if ranges == [(0, _MAX_CP)]:
        ranges, negate = [], not negate
    if not ranges:
        return "[\\s\\S]" if negate else "(?!)"
    body = "".join(_cp(lo) if lo == hi else f"{_cp(lo)}-{_cp(hi)}" for lo, hi in ranges)
    return f"[{'^' if negate else ''}{body}]"


_SETS = {"d": _DIGIT, "w": _WORD, "s": _WS}


# --- the translator -------------------------------------------------------------------------


@dataclass(frozen=True)
class Pattern:
    """A translated pattern: Python source and flags, plus what the replacement needs to know."""
    source: str
    flags: int
    glob: bool
    sticky: bool
    groups: int
    names: tuple[str, ...]


def split_find(text: str) -> tuple[str, str]:
    """`/pattern/flags` -> (pattern, flags); anything else is a plain pattern without flags."""
    if len(text) >= 2 and text.startswith("/"):
        end = text.rfind("/")
        flags = text[end + 1:]
        if end >= 1 and re.fullmatch(r"[a-z]*", flags):
            return text[1:end], flags
    return text, ""


def _scan_groups(pattern: str) -> tuple[int, list[str]]:
    """Capturing groups of the JavaScript pattern, and the names among them."""
    count, names, i, in_class = 0, [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            if pattern.startswith("(?<", i) and not pattern.startswith(("(?<=", "(?<!"), i):
                end = pattern.find(">", i)
                if end > 0:
                    count += 1
                    names.append(pattern[i + 3:end])
            elif not pattern.startswith("(?", i):
                count += 1
        i += 1
    return count, names


class _Translator:
    def __init__(self, pattern: str, flags: str) -> None:
        self.p = pattern
        self.i = 0
        self.flags = flags
        self.unicode = "u" in flags
        self.multi = "m" in flags
        self.dotall = "s" in flags
        self.total, self.names = _scan_groups(pattern)
        self.out: list[str] = []

    def fail(self, text: str) -> RegexError:
        return RegexError(f"{text} (место в шаблоне: {self.i + 1})")

    def run(self) -> str:
        p = self.p
        while self.i < len(p):
            c = p[self.i]
            if c == "\\":
                self.out.append(self.escape())
            elif c == "[":
                self.out.append(self.char_class())
            elif c == "(":
                self.out.append(self.group_open())
            elif c == ".":
                self.out.append("[\\s\\S]" if self.dotall else f"[^{_NL}]")
                self.i += 1
            elif c == "^":
                self.out.append(f"(?<![^{_NL}])" if self.multi else "\\A")
                self.i += 1
            elif c == "$":
                self.out.append(f"(?![^{_NL}])" if self.multi else "\\Z")
                self.i += 1
            elif c in "*+?":
                self.out.append(c)
                self.i += 1
                if c in "*+?}" and self.i < len(p) and p[self.i] == "+":
                    raise self.fail("«+» после повтора (как «a*+» или «a++») в JavaScript не бывает: "
                                    "уберите лишний «+»")
            elif c == "{":
                m = _QUANT.match(p, self.i)
                if m:
                    if m.group(2) and int(m.group(2)) < int(m.group(1)):
                        raise self.fail("В «{n,m}» второе число меньше первого")
                    self.out.append(m.group(0))
                    self.i = m.end()
                    if self.i < len(p) and p[self.i] == "+":
                        raise self.fail("«+» после повтора в JavaScript не бывает: уберите лишний «+»")
                else:
                    self.out.append("\\{")
                    self.i += 1
            elif c in "}]":
                self.out.append("\\" + c)
                self.i += 1
            elif c in "|)":
                self.out.append(c)
                self.i += 1
            else:
                self.out.append(re.escape(c))
                self.i += 1
        return "".join(self.out)

    def group_open(self) -> str:
        p, i = self.p, self.i
        if not p.startswith("(?", i):
            self.i += 1
            return "("
        for head in ("(?:", "(?=", "(?!", "(?<=", "(?<!"):
            if p.startswith(head, i):
                self.i += len(head)
                return head
        if p.startswith("(?<", i):
            end = p.find(">", i)
            name = p[i + 3:end] if end > 0 else ""
            if not _NAME.match(name):
                raise self.fail(f"Имя группы «{name}» не подходит: только латинские буквы, цифры и «_», "
                                "и не с цифры")
            self.i = end + 1
            return f"(?P<{name}>"
        raise self.fail(f"Конструкция «{p[i:i + 4]}» в JavaScript не существует "
                        "(встроенные флаги вроде «(?i)» и атомарные группы «(?>» там не бывают)")

    def hex_run(self, start: int, count: int) -> int | None:
        text = self.p[start:start + count]
        if len(text) == count and re.fullmatch(r"[0-9A-Fa-f]+", text):
            return int(text, 16)
        return None

    def unicode_escape(self) -> tuple[int, int] | None:
        """`\\uHHHH`, a surrogate pair, or `\\u{H…}` (with the u flag), at self.i + 1 == 'u'.
        Returns (code point, length of the whole escape) or None when it is not one."""
        p, i = self.p, self.i
        if self.unicode and p.startswith("\\u{", i):
            end = p.find("}", i)
            body = p[i + 3:end] if end > 0 else ""
            if body and re.fullmatch(r"[0-9A-Fa-f]{1,6}", body) and int(body, 16) <= _MAX_CP:
                return int(body, 16), end - i + 1
            raise self.fail("Некорректный код символа в «\\u{…}»")
        code = self.hex_run(i + 2, 4)
        if code is None:
            return None
        if 0xD800 <= code <= 0xDBFF and p.startswith("\\u", i + 6):
            low = self.hex_run(i + 8, 4)
            if low is not None and 0xDC00 <= low <= 0xDFFF:
                return 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00), 12
        return code, 6

    def simple_escape(self, letter: str) -> int | None:
        """Escapes that stand for one character (outside and inside a class). Moves self.i past it."""
        p, i = self.p, self.i
        table = {"n": 10, "r": 13, "t": 9, "f": 12, "v": 11}
        if letter in table:
            self.i += 2
            return table[letter]
        if letter == "x":
            code = self.hex_run(i + 2, 2)
            if code is not None:
                self.i += 4
                return code
            self.i += 2
            return ord("x")
        if letter == "u":
            found = self.unicode_escape()
            if found:
                self.i += found[1]
                return found[0]
            self.i += 2
            return ord("u")
        if letter == "c" and i + 2 < len(p) and p[i + 2].isascii() and p[i + 2].isalpha():
            self.i += 3
            return ord(p[i + 2]) % 32
        if letter in "pP" and self.unicode and p.startswith("{", i + 2):
            raise self.fail("Свойства Юникода «\\p{…}» не поддерживаются: в Python их нет. "
                            "Задайте нужные символы явно, например «[а-яА-ЯёЁ]» вместо «\\p{L}»")
        return None

    def escape(self) -> str:
        p, i = self.p, self.i
        if i + 1 >= len(p):
            raise self.fail("Шаблон заканчивается одиночным «\\»")
        letter = p[i + 1]
        if letter in "dws":
            self.i += 2
            return _class(_SETS[letter])
        if letter in "DWS":
            self.i += 2
            return _class(_SETS[letter.lower()], negate=True)
        if letter in "bB":
            self.i += 2
            w = _WORD_CLASS
            edge = f"(?:(?<={w})(?!{w})|(?<!{w})(?={w}))"
            return edge if letter == "b" else f"(?:(?<={w})(?={w})|(?<!{w})(?!{w}))"
        if letter == "0" and not (i + 2 < len(p) and p[i + 2].isdigit()):
            self.i += 2
            return "\\x00"
        if letter.isdigit():
            m = re.compile(r"[1-9]\d*").match(p, i + 1)
            if not m:
                raise self.fail("Восьмеричные записи вроде «\\01» не поддерживаются")
            number = int(m.group(0))
            if number > self.total:
                raise self.fail(f"Ссылка «\\{number}» на группу, которой нет (групп в шаблоне: {self.total})")
            self.i = m.end()
            return f"(?({number})(?:\\{number})|)"  # a group that took no part matches empty, as in JavaScript
        if letter == "k":
            m = re.compile(r"<([^>]*)>").match(p, i + 2)
            if self.names:
                if not m or m.group(1) not in self.names:
                    raise self.fail("«\\k<имя>» ссылается на группу, которой нет")
                self.i = m.end()
                return f"(?({m.group(1)})(?P={m.group(1)})|)"
            self.i += 2
            return "k"
        code = self.simple_escape(letter)
        if code is not None:
            return re.escape(chr(code))
        self.i += 2
        return re.escape(letter)  # any other escaped character stands for itself

    def class_atom(self) -> tuple[str, Any]:
        """One item of a class: ("cp", code) or ("set", ranges)."""
        p = self.p
        if p[self.i] != "\\":
            code = ord(p[self.i])
            self.i += 1
            return "cp", code
        if self.i + 1 >= len(p):
            raise self.fail("Шаблон заканчивается одиночным «\\»")
        letter = p[self.i + 1]
        if letter in "dws":
            self.i += 2
            return "set", list(_SETS[letter])
        if letter in "DWS":
            self.i += 2
            return "set", _complement(_SETS[letter.lower()])
        if letter == "b":
            self.i += 2
            return "cp", 8
        if letter == "B":
            raise self.fail("«\\B» внутри «[…]» не бывает")
        if letter.isdigit():
            m = re.compile(r"[0-7]{1,3}").match(p, self.i + 1)
            if not m:
                raise self.fail("Некорректная запись «\\" + letter + "» внутри «[…]»")
            self.i = m.end()
            return "cp", int(m.group(0), 8)
        code = self.simple_escape(letter)
        if code is not None:
            return "cp", code
        self.i += 2
        return "cp", ord(letter)

    def char_class(self) -> str:
        p = self.p
        start = self.i
        self.i += 1
        negate = self.i < len(p) and p[self.i] == "^"
        if negate:
            self.i += 1
        ranges: list[tuple[int, int]] = []
        while True:
            if self.i >= len(p):
                self.i = start
                raise self.fail("Не закрыта «[»: не хватает «]»")
            if p[self.i] == "]":
                self.i += 1
                break
            kind, value = self.class_atom()
            if kind == "set":
                ranges.extend(value)
                continue
            if self.i + 1 < len(p) and p[self.i] == "-" and p[self.i + 1] != "]":
                self.i += 1
                kind2, value2 = self.class_atom()
                if kind2 == "cp":
                    if value2 < value:
                        raise self.fail("Диапазон в «[…]» задан наоборот (первый символ больше второго)")
                    ranges.append((value, value2))
                else:  # "a-\d": the dash is a plain dash
                    ranges.extend([(value, value), (45, 45), *value2])
                continue
            ranges.append((value, value))
        return _class(sorted(ranges), negate)


def _flag_bits(flags: str) -> int:
    bits = 0
    seen = set()
    for f in flags:
        if f not in FLAGS_KNOWN:
            hint = " Флаг «v» (новые наборы символов) не поддерживается." if f == "v" else ""
            raise RegexError(f"Неизвестный флаг «{f}». Бывают: g, i, m, s, u, y, d.{hint}")
        if f in seen:
            raise RegexError(f"Флаг «{f}» указан дважды")
        seen.add(f)
    if "i" in seen:
        bits |= re.IGNORECASE
    return bits


_ERRORS = (
    ("look-behind requires fixed-width", "Lookbehind «(?<=…)» / «(?<!…)» в Python обязан быть фиксированной длины "
     "(в JavaScript бывает любой). Уберите внутри него «*», «+», «?» и «{n,m}» или перепишите без него"),
    ("nothing to repeat", "Нечего повторять: перед «*», «+», «?» или «{…}» ничего нет"),
    ("multiple repeat", "Два повтора подряд («**», «+*» и подобное)"),
    ("unbalanced parenthesis", "Скобки «(» и «)» не сходятся"),
    ("missing )", "Не закрыта «(»: не хватает «)»"),
    ("unterminated character set", "Не закрыта «[»: не хватает «]»"),
    ("bad character range", "Диапазон в «[…]» задан наоборот"),
    ("invalid group reference", "Ссылка на группу, которой нет"),
    ("cannot refer to an open group", "Группа ссылается сама на себя"),
    ("redefinition of group name", "Две группы с одним именем"),
)


def translate(find: str, extra_flags: str = "") -> Pattern:
    """`/pattern/flags` (or a plain pattern) -> a Python pattern with the same meaning. RegexError if impossible."""
    if len(find) > MAX_PATTERN:
        raise RegexError(f"Шаблон длиннее {MAX_PATTERN} знаков")
    pattern, flags = split_find(find)
    flags += extra_flags
    if not pattern:
        raise RegexError("Шаблон пуст: укажите, что искать")
    bits = _flag_bits(flags)
    source = _Translator(pattern, flags).run()
    try:
        compiled = re.compile(source, bits)
    except re.error as exc:
        text = str(exc)
        for needle, russian in _ERRORS:
            if needle in text:
                raise RegexError(russian) from None
        raise RegexError(f"Шаблон не разобран: {text}") from None
    except (RecursionError, OverflowError):
        raise RegexError("Шаблон слишком сложный") from None
    except Warning as exc:  # pragma: no cover - Python warnings about odd sets
        raise RegexError(f"Шаблон вызывает предупреждение: {exc}") from None
    return Pattern(source, bits, "g" in flags, "y" in flags, compiled.groups, tuple(compiled.groupindex))


# --- the replacement ------------------------------------------------------------------------

_MATCH_MACRO = re.compile(r"\{\{match\}\}", re.IGNORECASE)


def parse_replacement(text: str, names: tuple[str, ...], groups: int, macros: dict[str, str]) -> list[list[Any]]:
    """The replace text -> parts the worker expands: ["l", text] | ["m"] | ["g", n] | ["n", name] | ["b"] | ["a"].

    `$1`, `$12`, `$<name>`, `$&`, `$0` (the whole match), `$$`, "$`" (before), `$'` (after) and `{{match}}`.
    {{char}} and {{user}} (macros) are put into the plain text only, so a name with a `$` in it stays a name."""
    parts: list[list[Any]] = []
    literal: list[str] = []

    def flush() -> None:
        if literal:
            joined = "".join(literal)
            for key, value in macros.items():
                joined = joined.replace("{{" + key + "}}", value)
            parts.append(["l", joined])
            literal.clear()

    for k, piece in enumerate(_MATCH_MACRO.split(text)):
        if k:
            flush()
            parts.append(["m"])
        i = 0
        while i < len(piece):
            c = piece[i]
            nxt = piece[i + 1] if i + 1 < len(piece) else ""
            if c != "$" or not nxt:
                literal.append(c)
                i += 1
            elif nxt == "$":
                literal.append("$")
                i += 2
            elif nxt in "&0":
                flush()
                parts.append(["m"])
                i += 2
            elif nxt == "`":
                flush()
                parts.append(["b"])
                i += 2
            elif nxt == "'":
                flush()
                parts.append(["a"])
                i += 2
            elif nxt == "<" and names:
                end = piece.find(">", i)
                if end < 0:
                    literal.append(c)
                    i += 1
                else:
                    name = piece[i + 2:end]
                    if name in names:
                        flush()
                        parts.append(["n", name])
                    i = end + 1
            elif nxt.isdigit():
                two = piece[i + 1:i + 3]
                if len(two) == 2 and two.isdigit() and 1 <= int(two) <= groups:
                    flush()
                    parts.append(["g", int(two)])
                    i += 3
                elif 1 <= int(nxt) <= groups:
                    flush()
                    parts.append(["g", int(nxt)])
                    i += 2
                else:
                    literal.append(c)
                    i += 1
            else:
                literal.append(c)
                i += 1
        flush()
    return parts


# --- rules ----------------------------------------------------------------------------------


def js_escape(text: str) -> str:
    """The value of a macro put into a pattern as plain text (the «escaped» way)."""
    return re.sub(r"[.*+?^${}()|\[\]\\/-]", lambda m: "\\" + m.group(0), text)


def build_spec(rule: dict[str, Any], char: str = "", user: str = "") -> tuple[dict[str, Any], Pattern]:
    """The thing the worker takes for one rule. RegexError for a rule that cannot be used."""
    find = rule.get("find") or ""
    mode = rule.get("macros", "none")
    if mode != "none" and "{{" in find:
        pattern, flags = split_find(find)
        wrapped = pattern != find
        for key, value in (("char", char), ("user", user)):
            pattern = pattern.replace("{{" + key + "}}", js_escape(value) if mode == "escaped" else value)
        find = f"/{pattern}/{flags}" if wrapped else pattern
    translated = translate(find)
    parts = parse_replacement(rule.get("replace") or "", translated.names, translated.groups,
                              {"char": char, "user": user})
    spec = {"p": translated.source, "f": translated.flags, "g": translated.glob, "y": translated.sticky,
            "parts": parts, "trim": [t for t in (rule.get("trim") or []) if t]}
    return spec, translated


def normalize_rule(raw: Any, *, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate the fields of a rule (from the page, an import or the assistant); fill in the defaults.

    With `base` (the stored rule) only the fields present in `raw` change. The pattern is compiled, so a
    rule that cannot work is refused at once with the reason."""
    if not isinstance(raw, dict):
        raise RegexError("Правило должно быть объектом")
    rule = dict(base) if base else {
        "name": "", "enabled": True, "find": "", "replace": "", "trim": [], "places": ["user", "assistant"],
        "when": "display", "min_depth": None, "max_depth": None, "run_on_edit": False, "macros": "none",
        "render_html": False, "extra": {},
    }
    for key in ("name", "find", "replace"):
        if key in raw:
            if not isinstance(raw[key], str):
                raise RegexError(f"Поле «{key}» должно быть текстом")
            rule[key] = raw[key]
    rule["name"] = " ".join(rule["name"].split())[:MAX_NAME]
    if len(rule["replace"]) > MAX_REPLACE:
        raise RegexError(f"Текст замены длиннее {MAX_REPLACE} знаков")
    for key in ("enabled", "run_on_edit", "render_html"):
        if key in raw:
            rule[key] = bool(raw[key])
    if "trim" in raw:
        trim = raw["trim"]
        if isinstance(trim, str):
            trim = trim.split("\n")
        if not isinstance(trim, list) or not all(isinstance(t, str) for t in trim) or len(trim) > MAX_TRIM:
            raise RegexError("«Вырезать» — список строк, не больше %d" % MAX_TRIM)
        rule["trim"] = [t for t in trim if t]
    if "places" in raw:
        places = raw["places"]
        if not isinstance(places, list) or any(p not in PLACES for p in places):
            raise RegexError("«Где работает»: user (ваши сообщения), assistant (ответы модели), lore (записи лорбука)")
        rule["places"] = [p for p in PLACES if p in places]
    if "when" in raw:
        if raw["when"] not in WHEN:
            raise RegexError("«Когда»: display (на экране), prompt (то, что видит модель), both, stored (в сохранённом тексте)")
        rule["when"] = raw["when"]
    if "macros" in raw:
        if raw["macros"] not in MACROS:
            raise RegexError("«Макросы в шаблоне»: none, raw или escaped")
        rule["macros"] = raw["macros"]
    for key in ("min_depth", "max_depth"):
        if key in raw:
            value = raw[key]
            if value in (None, ""):
                rule[key] = None
            elif isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100_000:
                raise RegexError("Глубина — целое число от 0 или пусто")
            else:
                rule[key] = value
    if rule["min_depth"] is not None and rule["max_depth"] is not None and rule["min_depth"] > rule["max_depth"]:
        raise RegexError("Глубина «от» больше глубины «до»")
    if "extra" in raw and isinstance(raw["extra"], dict):
        rule["extra"] = raw["extra"]
    if not rule["find"].strip():
        raise RegexError("Шаблон пуст: укажите, что искать")
    build_spec(rule, "Имя", "Вы")  # compiles the pattern (macros filled with stand-ins), so a bad one is refused now
    return rule


def applies(rule: dict[str, Any], place: str, depth: int, kind: str) -> bool:
    """Does the rule work on a text of this place ('user' | 'assistant' | 'lore') at this depth (0 = the
    newest message) for this purpose ('display' | 'prompt' | 'stored')?"""
    if not rule.get("enabled", True) or not rule.get("approved", True):
        return False
    when = rule.get("when", "display")
    if when != kind and not (when == "both" and kind in ("display", "prompt")):
        return False
    if place not in rule.get("places", ()):
        return False
    if place != "lore":
        low, high = rule.get("min_depth"), rule.get("max_depth")
        if (low is not None and depth < low) or (high is not None and depth > high):
            return False
    return True
