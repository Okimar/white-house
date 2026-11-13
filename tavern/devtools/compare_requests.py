"""Compare two saved model requests (from `tavern.devtools.capture`) and print what differs.

    python -m tavern.devtools.compare_requests A.json B.json

Works for OpenAI chat, Claude and Gemini shaped bodies (a capture file with its `body` wrapper or a bare
body). Messages are put in one shape, (role, text); everything else in the body is a parameter. In tests:

    messages, params = normalise(body)
    differences = compare(body_a, body_b)

Invisible whitespace is made visible in the text diff: spaces at the ends or doubled show as `·`, tabs as
`<TAB>`, and every unusual blank or format character (U+2800, U+200B, no-break space ...) as `<U+XXXX>`.
Exit code is 1 when the requests differ, 0 when not.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Body keys that carry the conversation; everything else is a parameter.
_MESSAGE_KEYS = {"messages", "contents", "system", "systemInstruction", "system_instruction", "prompt"}
_ROLE_ALIASES = {"model": "assistant"}

Message = tuple[str, str]


@dataclass(frozen=True)
class Difference:
    """kind: count | merged | role | text | only_a | only_b | param."""

    kind: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.where}\n{self.detail}" if self.detail else f"[{self.kind}] {self.where}"


# ---- normalising ----


def _part_text(part: Any) -> str:
    if isinstance(part, str):
        return part
    if not isinstance(part, dict):
        return json.dumps(part, ensure_ascii=False, sort_keys=True)
    if isinstance(part.get("text"), str):
        return part["text"]
    kind = part.get("type")
    if kind == "image_url" or "inlineData" in part or "inline_data" in part or kind == "image":
        return f"[{kind or 'image'}]"
    return json.dumps(part, ensure_ascii=False, sort_keys=True)


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_part_text(p) for p in content)
    return _part_text(content)


def normalise(body: dict[str, Any]) -> tuple[list[Message], dict[str, Any]]:
    """(messages as (role, text), parameters flattened to dotted keys)."""
    body = unwrap(body)
    messages: list[Message] = []
    for key in ("systemInstruction", "system_instruction"):
        if body.get(key) is not None:
            item = body[key]
            parts = item.get("parts", item) if isinstance(item, dict) else item
            messages.append(("system", _content_text(parts)))
    if body.get("system") is not None:  # Claude's own top-level system
        messages.append(("system", _content_text(body["system"])))
    for item in body.get("messages") or []:
        if isinstance(item, dict):
            messages.append((_role(item.get("role", "")), _content_text(item.get("content"))))
    for item in body.get("contents") or []:
        if isinstance(item, dict):
            messages.append((_role(item.get("role", "user")), _content_text(item.get("parts"))))
    if isinstance(body.get("prompt"), str):
        messages.append(("prompt", body["prompt"]))
    params: dict[str, Any] = {}
    _flatten({k: v for k, v in body.items() if k not in _MESSAGE_KEYS}, "", params)
    return messages, params


def _role(role: Any) -> str:
    role = str(role)
    return _ROLE_ALIASES.get(role, role)


def _flatten(value: Any, prefix: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict) and value:
        for key, item in value.items():
            _flatten(item, f"{prefix}.{key}" if prefix else str(key), out)
    elif prefix:
        out[prefix] = value


def unwrap(data: dict[str, Any]) -> dict[str, Any]:
    """A capture file keeps the request in `body`; a bare body is returned as it is."""
    if isinstance(data.get("body"), dict) and "path" in data and "headers" in data:
        return data["body"]
    return data


def load(path: str | Path) -> dict[str, Any]:
    return unwrap(json.loads(Path(path).read_text(encoding="utf-8")))


# ---- making whitespace visible ----


def visible(text: str) -> str:
    """The text with invisible things spelled out (newlines stay, so the diff works by line)."""
    out = []
    for char in text:
        if char in " \n":
            out.append(char)
        elif char == "\t":
            out.append("<TAB>")
        elif char == "\r":
            out.append("<CR>")
        elif unicodedata.category(char) in {"Zs", "Zl", "Zp", "Cf", "Cc", "Co", "Cn"} or char == "⠀":
            out.append(f"<U+{ord(char):04X}>")
        else:
            out.append(char)
    spaced = "".join(out)
    return "\n".join(re.sub(r"^ +| +$| {2,}", lambda m: "·" * len(m.group()), line) for line in spaced.split("\n"))


def text_diff(a: str, b: str) -> str:
    lines = list(difflib.unified_diff(visible(a).split("\n"), visible(b).split("\n"), "A", "B", lineterm="", n=2))
    return "\n".join(lines)


# ---- comparing ----


def _merge(messages: list[Message]) -> list[Message]:
    merged: list[Message] = []
    for role, text in messages:
        if merged and merged[-1][0] == role:
            merged[-1] = (role, merged[-1][1] + "\n" + text)
        else:
            merged.append((role, text))
    return merged


def _squash(text: str) -> str:
    return "".join(text.split())


def _same_role_runs(messages: list[Message]) -> list[tuple[int, int, str]]:
    runs, start = [], 0
    for i in range(1, len(messages) + 1):
        if i == len(messages) or messages[i][0] != messages[start][0]:
            if i - start > 1:
                runs.append((start + 1, i, messages[start][0]))
            start = i
    return runs


def _short(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def compare(a: dict[str, Any], b: dict[str, Any]) -> list[Difference]:
    """Every difference between two request bodies (A and B), in reading order."""
    msgs_a, params_a = normalise(a)
    msgs_b, params_b = normalise(b)
    found: list[Difference] = []

    if len(msgs_a) != len(msgs_b):
        found.append(Difference("count", "message count", f"A has {len(msgs_a)}, B has {len(msgs_b)}"))
    runs_a, runs_b = _same_role_runs(msgs_a), _same_role_runs(msgs_b)
    if runs_a != runs_b:  # neighbours with one role are fine when both programs have them alike
        for label, runs in (("A", runs_a), ("B", runs_b)):
            for first, last, role in runs:
                found.append(Difference("merged", f"{label}: messages {first}-{last} are neighbours with role {role!r}", ""))

    merged_a, merged_b = _merge(msgs_a), _merge(msgs_b)
    if (len(msgs_a) != len(msgs_b) and len(merged_a) == len(merged_b)
            and all(x[0] == y[0] and _squash(x[1]) == _squash(y[1]) for x, y in zip(merged_a, merged_b))):
        found.append(Difference("merged", "same text once neighbours with one role are joined",
                                "the programs differ only in how they split / merge messages"))
    else:
        found.extend(_compare_messages(msgs_a, msgs_b))

    for key in sorted(set(params_a) | set(params_b)):
        in_a, in_b = key in params_a, key in params_b
        if in_a and in_b and params_a[key] == params_b[key]:
            continue
        found.append(Difference("param", key, f"A: {_short(params_a[key]) if in_a else '(missing)'}\n"
                                              f"B: {_short(params_b[key]) if in_b else '(missing)'}"))
    return found


def _compare_messages(msgs_a: list[Message], msgs_b: list[Message]) -> list[Difference]:
    found: list[Difference] = []
    matcher = difflib.SequenceMatcher(None, msgs_a, msgs_b, autojunk=False)
    for op, a1, a2, b1, b2 in matcher.get_opcodes():
        if op == "equal":
            continue
        paired = min(a2 - a1, b2 - b1) if op == "replace" else 0
        for k in range(paired):
            found.extend(_compare_pair(a1 + k, msgs_a[a1 + k], b1 + k, msgs_b[b1 + k]))
        for i in range(a1 + paired, a2):
            found.append(Difference("only_a", f"A message {i + 1} ({msgs_a[i][0]})", visible(msgs_a[i][1])))
        for j in range(b1 + paired, b2):
            found.append(Difference("only_b", f"B message {j + 1} ({msgs_b[j][0]})", visible(msgs_b[j][1])))
    return found


def _compare_pair(i: int, a: Message, j: int, b: Message) -> list[Difference]:
    found: list[Difference] = []
    where = f"A message {i + 1} / B message {j + 1}"
    if a[0] != b[0]:
        found.append(Difference("role", where, f"A: {a[0]}\nB: {b[0]}"))
    if a[1] != b[1]:
        found.append(Difference("text", f"{where} ({a[0]})", text_diff(a[1], b[1])))
    return found


def report(diffs: list[Difference]) -> str:
    if not diffs:
        return "No differences."
    return "\n\n".join(str(d) for d in diffs) + f"\n\n{len(diffs)} difference(s)."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare two captured model requests")
    parser.add_argument("a", help="first request (capture file or bare body)")
    parser.add_argument("b", help="second request")
    args = parser.parse_args(argv)
    try:
        body_a, body_b = load(args.a), load(args.b)
    except (OSError, ValueError) as exc:
        print(f"cannot read: {exc}", file=sys.stderr)
        return 2
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    diffs = compare(body_a, body_b)
    print(report(diffs))
    return 1 if diffs else 0


if __name__ == "__main__":
    raise SystemExit(main())
