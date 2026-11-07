"""What is worth saying aloud: a message cleaned of thinking, code and *actions*, cut into pieces.

Pure functions, no I/O. A voice that reads `<thinking>` blocks, code fences or "*smiles and waves*"
word for word is the first thing people switch off, so each of the three can be skipped.
"""

from __future__ import annotations

import re

_THINK_CLOSED = re.compile(r"<(think|thinking)>.*?</\1>", re.S | re.I)
_THINK_OPEN = re.compile(r"<(think|thinking)>.*\Z", re.S | re.I)  # a thought that never closed
_THINK_STRAY = re.compile(r"</?(think|thinking)>", re.I)
_FENCE = re.compile(r"```.*?```", re.S)
_FENCE_OPEN = re.compile(r"```.*\Z", re.S)  # a fence that never closed
_INLINE = re.compile(r"`[^`\n]*`")
_ACTION = re.compile(r"\*[^*]{1,400}\*", re.S)
_BOLD_MARK = "\x01"
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_TAG = re.compile(r"<[^>\n]{1,200}>")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_QUOTE = re.compile(r"^\s*>\s?", re.M)
_BULLET = re.compile(r"^\s*[-+•]\s+", re.M)
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def clean(text: str, *, thinking: bool = True, code: bool = True, actions: bool = True) -> str:
    """The text to speak. `thinking`, `code`, `actions`: True drops that part, False keeps what is in it."""
    out = text or ""
    if thinking:
        out = _THINK_CLOSED.sub(" ", out)
        out = _THINK_OPEN.sub(" ", out)
    else:
        out = _THINK_STRAY.sub(" ", out)
    if code:
        out = _FENCE.sub(" ", out)
        out = _FENCE_OPEN.sub(" ", out)
        out = _INLINE.sub(" ", out)
    else:
        out = out.replace("```", " ").replace("`", "")
    out = _IMAGE.sub(" ", out)
    out = _LINK.sub(r"\1", out)
    out = _URL.sub(" ", out)
    out = _TAG.sub(" ", out)
    out = out.replace("**", _BOLD_MARK)  # bold is emphasis, not an action: keep its words
    if actions:
        out = _ACTION.sub(" ", out)
    out = out.replace("*", "").replace(_BOLD_MARK, "")
    out = _HEADING.sub("", out)
    out = _QUOTE.sub("", out)
    out = _BULLET.sub("", out)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\s*\n\s*", "\n", out)
    return out.strip()


def chunks(text: str, size: int) -> list[str]:
    """Pieces of up to `size` characters, cut after a paragraph or a sentence (a word if nothing else)."""
    size = max(20, size)
    pieces: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            pieces.append(current.strip())
        current = ""

    for paragraph in text.split("\n"):
        for sentence in _SENTENCE_END.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            while len(sentence) > size:
                cut = sentence.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                if current:
                    flush()
                pieces.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if len(current) + len(sentence) + 1 > size:
                flush()
            current = f"{current} {sentence}" if current else sentence
        if current and len(current) + 1 > size // 2:
            flush()  # a paragraph ends a piece once it is half full: a pause lands there
    flush()
    return pieces


def shorten(text: str, limit: int) -> tuple[str, bool]:
    """At most `limit` characters, cut after the last full sentence. Second value: was it cut."""
    if limit <= 0 or len(text) <= limit:
        return text, False
    head = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?…]\s", head + " ")]
    if ends and ends[-1] > limit // 2:
        head = head[:ends[-1]]
    return head.strip(), True
