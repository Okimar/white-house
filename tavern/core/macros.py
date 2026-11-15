"""Macros: the `{{…}}` words of prompt texts, expanded the way SillyTavern does.

Clean room: written from a plain-words description of SillyTavern's behaviour (docs/ST_PARITY.md section 7),
never from its code. `core/prompt.py:substitute` is the public entry; this module holds the engine.

How a text is expanded:
- `{{// …}}` comments go first and whole (also over several lines); nothing inside them runs.
- Macros are found as balanced `{{ … }}` spans, so `{{setvar::x::{{char}}}}` and `{{if {{getvar::x}}}}` work;
  inner macros are expanded before the outer one, left to right, so `{{setvar}}` before `{{getvar}}` holds.
- `{{if cond}}…{{else}}…{{/if}}` runs only the chosen branch.
- `{{trim}}` is resolved last: it removes itself and the line breaks around it (also ones `{{newline}}` made).
- An unknown macro stays as written (with its inner macros expanded), as in SillyTavern.

Context values (card fields, last messages, model, outlets, chat id) come from the `values` mapping, matched
without case. When the caller passes no `values` at all (a greeting at chat creation), those macros stay as
written so the assembly can fill them later; with `values` but a missing key they become their empty default.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from datetime import datetime, timedelta, timezone
import hashlib
import random
import re

TRIM_MARK = ""  # a private-use character standing for {{trim}} until the whole text is done
_TRIM = re.compile(r"(?:\r?\n)*" + TRIM_MARK + r"(?:\r?\n)*")
_ANGLE = re.compile(r"<(USER|BOT|CHAR|GROUP)>")  # upper case only: lower-case <user> is often an XML-like tag
_NAME = re.compile(r"\s*([A-Za-z_][\w]*)\s*(?::(.*)|\s+(.*))?", re.S)
_UTC = re.compile(r"\s*time_utc\s*([+-]\s*\d+(?:\.\d+)?)\s*", re.I)
_IF = re.compile(r"if(?:\s+|::)", re.I)
_DICE = re.compile(r"\s*(\d*)\s*d\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*", re.I)
FALSY = frozenset({"", "false", "0", "off", "no"})
MAX_DEPTH = 8  # nesting and values that hold macros (a description with {{description}} in it stops here)

# Names whose value comes from the chat / card / connection. Default when `values` is given without the key.
CONTEXT_DEFAULTS: dict[str, str] = {
    "description": "", "personality": "", "scenario": "", "persona": "", "mesexamples": "",
    "mesexamplesraw": "", "charprompt": "", "charinstruction": "", "chardepthprompt": "",
    "charcreatornotes": "", "charversion": "", "charfirstmessage": "", "systemprompt": "",
    "lastmessage": "", "lastusermessage": "", "lastcharmessage": "", "lastmessageid": "",
    "firstincludedmessageid": "", "currentswipeid": "", "lastswipeid": "", "lastgenerationtype": "normal",
    "input": "", "model": "", "maxcontext": "", "maxprompt": "", "maxresponse": "", "ismobile": "false",
}
ALIASES = {
    "chardescription": "description", "charpersonality": "personality", "charscenario": "scenario",
    "charjailbreak": "charinstruction", "greeting": "charfirstmessage", "char_version": "charversion",
    "mesexamplesraw": "mesexamples", "charifnotgroup": "group",
}
# Card texts may hold macros themselves ({{char}} in a description); they are expanded where they land.
RECURSIVE = frozenset({
    "description", "personality", "scenario", "persona", "mesexamples", "mesexamplesraw", "charprompt",
    "charinstruction", "chardepthprompt", "charcreatornotes", "charfirstmessage", "systemprompt",
})
_VAR_OPS = ("set", "get", "add", "inc", "dec", "has", "delete")

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def expand(
    text: str,
    *,
    char: str,
    user: str,
    rng: Callable[[int], int] | None = None,
    values: Mapping[str, str] | None = None,
    variables: MutableMapping[str, str] | None = None,
    global_variables: MutableMapping[str, str] | None = None,
    now: datetime | None = None,
) -> str:
    if not text or ("{{" not in text and "<" not in text):
        return text
    engine = _Engine(char, user, rng, values, variables, global_variables, now)
    return _TRIM.sub("", engine.expand(text, 0)).replace(TRIM_MARK, "")


def _spans(text: str) -> list[tuple[int, int]]:
    """Top-level balanced `{{ … }}` spans as (start, end); an opening that never closes is plain text."""
    spans: list[tuple[int, int]] = []
    i = text.find("{{")
    while i != -1:
        while text.startswith("{", i + 2):
            i += 1  # "{{{x}}}": the macro is the inner {{x}}, the outer braces are plain text
        depth, j, end = 1, i + 2, -1
        while depth:
            opening, closing = text.find("{{", j), text.find("}}", j)
            if closing == -1:
                break
            if opening != -1 and opening < closing:
                depth, j = depth + 1, opening + 2
            else:
                depth, j = depth - 1, closing + 2
                if not depth:
                    end = j
        if end == -1:
            i = text.find("{{", i + 2)
            continue
        spans.append((i, end))
        i = text.find("{{", end)
    return spans


def _number(text: str) -> float | None:
    try:
        return float(text.strip())
    except ValueError:
        return None


def _num_text(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(value)


def humanize(delta: timedelta, *, suffix: bool = False) -> str:
    """A duration in words, with the usual rounding thresholds ("a few seconds", "2 hours", "a month")."""
    seconds = delta.total_seconds()
    s = abs(seconds)
    minutes, hours, days = s / 60, s / 3600, s / 86400
    if s < 45:
        words = "a few seconds"
    elif s < 90:
        words = "a minute"
    elif minutes < 45:
        words = f"{round(minutes)} minutes"
    elif minutes < 90:
        words = "an hour"
    elif hours < 22:
        words = f"{round(hours)} hours"
    elif hours < 36:
        words = "a day"
    elif days < 26:
        words = f"{round(days)} days"
    elif days < 45:
        words = "a month"
    elif days < 320:
        words = f"{max(2, round(days / 30.4))} months"
    elif days < 548:
        words = "a year"
    else:
        words = f"{round(days / 365.25)} years"
    if not suffix:
        return words
    return f"in {words}" if seconds > 0 else f"{words} ago"


def _time(dt: datetime, seconds: bool = False) -> str:
    hour = dt.hour % 12 or 12
    tail = f":{dt.second:02d}" if seconds else ""
    return f"{hour}:{dt.minute:02d}{tail} {'AM' if dt.hour < 12 else 'PM'}"


def _date(dt: datetime) -> str:
    return f"{_MONTHS[dt.month - 1]} {dt.day}, {dt.year}"


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _offset(dt: datetime, colon: bool) -> str:
    off = dt.utcoffset() or timedelta()
    minutes = int(off.total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    h, m = divmod(abs(minutes), 60)
    return f"{sign}{h:02d}:{m:02d}" if colon else f"{sign}{h:02d}{m:02d}"


_FORMAT_TOKEN = re.compile(
    r"\[[^\]]*\]|LTS|LT|LLLL|LLL|LL|L|YYYY|YY|MMMM|MMM|MM|M|Do|DD|D|dddd|ddd|dd|d|HH|H|hh|h|mm|m|ss|s|A|a|ZZ|Z")


def format_datetime(dt: datetime, pattern: str) -> str:
    """The common date-format tokens (YYYY, MM, DD, HH, mm, dddd, LT, LL, [literal] …) in English."""
    def token(m: re.Match[str]) -> str:
        t = m.group(0)
        if t.startswith("["):
            return t[1:-1]
        weekday = dt.weekday()
        sunday_based = (weekday + 1) % 7
        table = {
            "LTS": lambda: _time(dt, True), "LT": lambda: _time(dt),
            "LLLL": lambda: f"{_DAYS[weekday]}, {_date(dt)} {_time(dt)}",
            "LLL": lambda: f"{_date(dt)} {_time(dt)}", "LL": lambda: _date(dt),
            "L": lambda: f"{dt.month:02d}/{dt.day:02d}/{dt.year}",
            "YYYY": lambda: f"{dt.year:04d}", "YY": lambda: f"{dt.year % 100:02d}",
            "MMMM": lambda: _MONTHS[dt.month - 1], "MMM": lambda: _MONTHS[dt.month - 1][:3],
            "MM": lambda: f"{dt.month:02d}", "M": lambda: str(dt.month),
            "Do": lambda: _ordinal(dt.day), "DD": lambda: f"{dt.day:02d}", "D": lambda: str(dt.day),
            "dddd": lambda: _DAYS[weekday], "ddd": lambda: _DAYS[weekday][:3], "dd": lambda: _DAYS[weekday][:2],
            "d": lambda: str(sunday_based),
            "HH": lambda: f"{dt.hour:02d}", "H": lambda: str(dt.hour),
            "hh": lambda: f"{dt.hour % 12 or 12:02d}", "h": lambda: str(dt.hour % 12 or 12),
            "mm": lambda: f"{dt.minute:02d}", "m": lambda: str(dt.minute),
            "ss": lambda: f"{dt.second:02d}", "s": lambda: str(dt.second),
            "A": lambda: "AM" if dt.hour < 12 else "PM", "a": lambda: "am" if dt.hour < 12 else "pm",
            "ZZ": lambda: _offset(dt, False), "Z": lambda: _offset(dt, True),
        }
        return table[t]()
    return _FORMAT_TOKEN.sub(token, pattern)


def _parse_date(text: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.astimezone()


class _Engine:
    def __init__(
        self,
        char: str,
        user: str,
        rng: Callable[[int], int] | None,
        values: Mapping[str, str] | None,
        variables: MutableMapping[str, str] | None,
        global_variables: MutableMapping[str, str] | None,
        now: datetime | None,
    ) -> None:
        self.char, self.user = char, user
        self.choose = rng or random.randrange
        self.values = {k.lower(): str(v) for k, v in values.items()} if values is not None else None
        self.variables = variables
        self.globals = global_variables if global_variables is not None else {}
        self._now = now

    @property
    def now(self) -> datetime:
        if self._now is None:
            self._now = datetime.now().astimezone()
        elif self._now.tzinfo is None:
            self._now = self._now.astimezone()
        return self._now

    # -- walking a text -------------------------------------------------------------------------------

    def angles(self, text: str) -> str:
        if "<" not in text:
            return text
        def one(m: re.Match[str]) -> str:
            word = m.group(1)
            if word == "USER":
                return self.user
            if word == "GROUP":
                return self.group()
            return self.char
        return _ANGLE.sub(one, text)

    def expand(self, text: str, depth: int) -> str:
        if "{{" not in text or depth > MAX_DEPTH:
            return self.angles(text)
        spans = _spans(text)
        out: list[str] = []
        pos, i = 0, 0
        while i < len(spans):
            start, end = spans[i]
            out.append(self.angles(text[pos:start]))
            body = text[start + 2:end - 2]
            head = body.strip()
            if head.startswith("//"):
                pos, i = end, i + 1
                continue
            if _IF.match(head):
                close, other = _match_if(text, spans, i)
                if close is not None:
                    then_to = spans[other][0] if other is not None else spans[close][0]
                    if self.truthy(head[2:].lstrip(":").strip(), depth):
                        branch = text[end:then_to]
                    else:
                        branch = text[spans[other][1]:spans[close][0]] if other is not None else ""
                    out.append(self.expand(branch, depth + 1))
                    pos, i = spans[close][1], close + 1
                    continue
            inner = self.expand(body, depth + 1) if "{{" in body else body
            value = self.call(inner, text, start, depth)
            out.append(value if value is not None else "{{" + inner + "}}")
            pos, i = end, i + 1
        out.append(self.angles(text[pos:]))
        return "".join(out)

    def truthy(self, cond: str, depth: int) -> bool:
        negate = cond.startswith("!")
        if negate:
            cond = cond[1:].strip()
        value = self.expand(cond, depth + 1).strip()
        if re.fullmatch(r"[A-Za-z_]\w*", value):
            named = self.simple(value.lower(), depth)
            if named is not None:
                value = named
        return (value.strip().lower() not in FALSY) != negate

    # -- one macro ------------------------------------------------------------------------------------

    def call(self, inner: str, text: str, start: int, depth: int) -> str | None:
        utc = _UTC.fullmatch(inner)
        if utc:
            hours = float(utc.group(1).replace(" ", ""))
            return _time(self.now.astimezone(timezone(timedelta(hours=hours))))
        args: list[str]
        rest: str | None
        if "::" in inner:
            name, *args = inner.split("::")
            name, rest = name.strip(), None
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                return None
        else:
            m = _NAME.fullmatch(inner)
            if not m:
                return None
            name, args = m.group(1), []
            rest = m.group(2) if m.group(2) is not None else m.group(3)
        lname = name.lower()
        has_args = bool(args) or rest is not None

        if lname in ("random", "pick"):
            options = args if args else ([o.strip() for o in rest.split(",")] if rest is not None else [])
            if not options:
                return ""
            if lname == "random":
                return options[self.choose(len(options))]
            chat = (self.values or {}).get("chatid", "")
            seed = hashlib.sha256(f"{chat}\x00{text}\x00{start}".encode()).digest()
            return options[int.from_bytes(seed[:8], "big") % len(options)]
        if lname == "roll":
            return self.roll(args[0] if args else (rest or ""))
        if lname == "reverse":
            return ("::".join(args) if args else (rest or ""))[::-1]
        if lname == "outlet":
            key = (args[0] if args else (rest or "")).strip()
            if self.values is None:
                return None
            return self.values.get(f"outlet::{key.lower()}", "")
        if lname == "datetimeformat":
            return format_datetime(self.now, "::".join(args) if args else (rest or "").strip())
        if lname == "timediff":
            if len(args) < 2:
                return None
            a, b = _parse_date(args[0]), _parse_date(args[1])
            return humanize(a - b, suffix=True) if a and b else ""
        if lname == "banned":
            return ""  # text-completion only: banned words are not sent as text
        if lname == "hasextension":
            return "false"  # SillyTavern extensions do not exist here
        var = self.variable(lname, args, rest)
        if var is not None or _is_var_name(lname):
            return var
        if has_args:
            return None
        return self.simple(lname, depth)

    def simple(self, lname: str, depth: int) -> str | None:
        """A macro without arguments; None = unknown here (stays as written)."""
        fixed = {"char": self.char, "user": self.user, "newline": "\n", "space": " ", "noop": "",
                 "trim": TRIM_MARK}
        if lname in ("original", "else", "if"):
            return None  # {{original}} belongs to card overrides (assembly); else / if outside a block stay
        if lname in fixed:
            return fixed[lname]
        if lname in ("time", "date", "weekday", "isotime", "isodate"):
            now = self.now
            return {"time": _time(now), "date": _date(now), "weekday": _DAYS[now.weekday()],
                    "isotime": f"{now.hour:02d}:{now.minute:02d}", "isodate": now.strftime("%Y-%m-%d")}[lname]
        if lname in ("group", "charifnotgroup", "groupnotmuted"):
            if self.values is not None and lname in self.values:
                return self.values[lname]
            return self.group() if lname != "groupnotmuted" else (
                (self.values or {}).get("group") or self.char)
        if lname == "notchar":
            return (self.values or {}).get("notchar") or self.user
        if self.values is None:
            return None
        if lname in self.values:
            return self.from_values(lname, depth)
        alias = ALIASES.get(lname)
        if alias and alias in self.values:
            return self.from_values(alias, depth)
        if lname == "idle_duration":
            since = _parse_date(self.values.get("lastusermessagetime", ""))
            return humanize(self.now - since) if since else "just now"
        return CONTEXT_DEFAULTS.get(lname)

    def from_values(self, key: str, depth: int) -> str:
        value = self.values[key] if self.values is not None else ""
        if key in RECURSIVE and ("{{" in value or "<" in value):
            return self.expand(value, depth + 1)
        return value

    def group(self) -> str:
        if self.values is not None and self.values.get("group"):
            return self.values["group"]
        return self.char

    def roll(self, spec: str) -> str | None:
        spec = spec.strip()
        if spec.isdigit():
            spec = "1d" + spec
        m = _DICE.fullmatch(spec)
        if not m:
            return None
        count, sides = int(m.group(1) or 1), int(m.group(2))
        if count < 1 or sides < 1 or count > 100 or sides > 100000:
            return None
        total = sum(self.choose(sides) + 1 for _ in range(count))
        if m.group(4):
            total += int(m.group(4)) * (1 if m.group(3) == "+" else -1)
        return str(total)

    def variable(self, lname: str, args: list[str], rest: str | None) -> str | None:
        if not _is_var_name(lname):
            return None
        is_global = "global" in lname
        op = lname.replace("global", "")[:-3]
        store = self.globals if is_global else self.variables
        if store is None:
            return None  # no chat store here: the assembly expands it later
        if not args and rest is not None:
            args = [rest]
        if not args:
            return None
        key = args[0].strip()
        value = "::".join(args[1:])
        current = store.get(key, "")
        if op == "set":
            store[key] = _TRIM.sub("", value).replace(TRIM_MARK, "")
            return ""
        if op == "get":
            return current
        if op == "has":
            return "true" if key in store else "false"
        if op == "delete":
            store.pop(key, None)
            return ""
        if op == "add":
            a, b = _number(current) if current else 0.0, _number(value)
            store[key] = _num_text(a + b) if a is not None and b is not None else current + value
            return ""
        step = 1 if op == "inc" else -1
        store[key] = _num_text((_number(current) or 0.0) + step)
        return store[key]


def _is_var_name(lname: str) -> bool:
    base = lname.replace("global", "", 1) if lname.count("global") == 1 else lname
    if not base.endswith("var"):
        return False
    op = base[:-3]
    if op not in _VAR_OPS:
        return False
    return lname in (f"{op}var", f"{op}globalvar")


def _match_if(text: str, spans: list[tuple[int, int]], at: int) -> tuple[int | None, int | None]:
    """The `{{/if}}` closing the `{{if}}` at index `at`, and its own `{{else}}` (None when absent)."""
    level, other = 0, None
    for j in range(at + 1, len(spans)):
        head = text[spans[j][0] + 2:spans[j][1] - 2].strip()
        if _IF.match(head):
            level += 1
        elif head.lower() == "/if":
            if not level:
                return j, other
            level -= 1
        elif head.lower() == "else" and not level and other is None:
            other = j
    return None, None
