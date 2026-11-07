"""The regex worker: the only place where a user's pattern is run against text (stage 11).

Python's `re` cannot be interrupted, so a pattern like `(a+)+$` on a long line could hang the app for
hours. Every match therefore runs in THIS file as a separate process (core/regex_runner.py starts it with
`python -I -S`, talks to it by lines of JSON and kills it when a rule takes too long). The file imports only
the standard library and nothing of Tavern, so starting it is fast (also on a phone) and a runaway
pattern can touch nothing but this process.

A rule is a "spec": {p: Python pattern, f: re flags, g: all matches, y: sticky, parts: replacement parts,
trim: strings cut out of what is inserted}. See core/regex.py for how it is made.

Job (one line in):  {"specs": [spec, ...], "items": [{"text": str, "rules": [index, ...]}, ...], "find": bool}
Out, line by line:  "R <index>" before each rule starts (so the parent knows which one hung), then
                    {"outs": [str, ...], "hits": [[index, ...], ...], "errors": {index: text}, "matches": [...]}
`hits` are the rules that changed an item's text; `matches` (find mode, one item) are the spans found by
the one rule, for the try-out window.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

MAX_OUT = 8_000_000  # characters a single result may grow to
MAX_MATCHES = 500    # spans reported by the try-out window


def _clean(value: str | None, trim: list[str]) -> str:
    value = value or ""
    for cut in trim:
        value = value.replace(cut, "")
    return value


def _expand(parts: list[list[Any]], m: re.Match[str], text: str, trim: list[str]) -> str:
    out: list[str] = []
    for part in parts:
        kind = part[0]
        if kind == "l":
            out.append(part[1])
        elif kind == "m":
            out.append(_clean(m.group(0), trim))
        elif kind == "g":
            out.append(_clean(m.group(part[1]), trim))
        elif kind == "n":
            out.append(_clean(m.group(part[1]), trim))
        elif kind == "b":
            out.append(text[:m.start()])
        elif kind == "a":
            out.append(text[m.end():])
    return "".join(out)


def _matches(rx: re.Pattern[str], spec: dict[str, Any], text: str):
    """The matches in the order JavaScript would find them (all with g, else the first; y = glued together)."""
    if spec["y"]:
        pos = 0
        while pos <= len(text):
            m = rx.match(text, pos)
            if m is None:
                return
            yield m
            if not spec["g"]:
                return
            pos = m.end() + (1 if m.end() == m.start() else 0)
        return
    if spec["g"]:
        yield from rx.finditer(text)
    else:
        m = rx.search(text)
        if m is not None:
            yield m


def apply_rule(rx: re.Pattern[str], spec: dict[str, Any], text: str) -> str:
    pieces: list[str] = []
    last = 0
    size = 0
    for m in _matches(rx, spec, text):
        pieces.append(text[last:m.start()])
        piece = _expand(spec["parts"], m, text, spec["trim"])
        pieces.append(piece)
        size += m.start() - last + len(piece)
        if size > MAX_OUT:
            raise MemoryError("результат слишком большой")
        last = m.end()
    if not pieces:
        return text
    pieces.append(text[last:])
    return "".join(pieces)


def find_all(rx: re.Pattern[str], spec: dict[str, Any], text: str) -> list[dict[str, Any]]:
    found = []
    for m in _matches(rx, spec, text):
        if len(found) >= MAX_MATCHES:
            break
        found.append({"start": m.start(), "end": m.end(), "text": m.group(0),
                      "groups": list(m.groups()), "named": m.groupdict()})
    return found


_COMPILED: dict[tuple[str, int], re.Pattern[str]] = {}  # kept between jobs: `re` itself remembers only 512
_LITERAL = re.compile(r"[^\\.^$*+?{}\[\]|()]+")
_NOT_PLAIN = int(re.IGNORECASE | re.VERBOSE)


def _compile(spec: dict[str, Any]) -> re.Pattern[str]:
    key = (spec["p"], spec["f"])
    rx = _COMPILED.get(key)
    if rx is None:
        rx = re.compile(spec["p"], spec["f"])
        if len(_COMPILED) >= 4096:
            _COMPILED.clear()
        _COMPILED[key] = rx
    return rx


def _literal(spec: dict[str, Any]) -> str | None:
    """The pattern's text when it is a plain string (found exactly when `in` finds it); a quick pre-check."""
    p = spec["p"]
    if p and not spec["f"] & _NOT_PLAIN and _LITERAL.fullmatch(p):
        return p
    return None


def run_job(job: dict[str, Any], progress=None) -> dict[str, Any]:
    specs: list[dict[str, Any]] = job["specs"]
    items: list[dict[str, Any]] = job["items"]
    outs = [item["text"] for item in items]
    hits: list[list[int]] = [[] for _ in items]
    errors: dict[str, str] = {}
    compiled: dict[int, re.Pattern[str] | None] = {}
    by_rule: dict[int, list[int]] = {}  # rule -> the items it works on
    for n, item in enumerate(items):
        for index in set(item["rules"]):
            by_rule.setdefault(index, []).append(n)
    wanted = sorted(by_rule)  # rules run in their own order
    for index in wanted:
        if progress:
            progress(index)
        spec = specs[index]
        try:
            rx = compiled[index] = _compile(spec)
        except (re.error, RecursionError, OverflowError) as exc:
            errors[str(index)] = str(exc)
            continue
        literal = _literal(spec)
        try:
            for n in by_rule[index]:
                before = outs[n]
                if literal is not None and literal not in before:
                    continue
                outs[n] = apply_rule(rx, spec, before)
                if outs[n] != before:
                    hits[n].append(index)
        except (MemoryError, RecursionError, OverflowError, IndexError, re.error) as exc:
            errors[str(index)] = str(exc) or exc.__class__.__name__
    result: dict[str, Any] = {"outs": outs, "hits": hits, "errors": errors}
    if job.get("find") and items and wanted:
        rx = compiled.get(wanted[0])
        result["matches"] = find_all(rx, specs[wanted[0]], items[0]["text"]) if rx is not None else []
    return result


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    def progress(index: int) -> None:
        stdout.write(b"R %d\n" % index)
        stdout.flush()

    for line in iter(stdin.readline, b""):
        try:
            job = json.loads(line.decode("utf-8"))
            result = run_job(job, progress)
        except Exception as exc:  # noqa: BLE001 - any failure becomes an answer, the process stays up
            result = {"failed": f"{exc.__class__.__name__}: {exc}"}
        stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
        stdout.flush()


if __name__ == "__main__":
    main()
