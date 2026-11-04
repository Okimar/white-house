"""The model's thinking written as text: split it out of a reply, and send old thinking back compactly.

Models asked to plan first (a preset's «Размышление» block) write `<thinking>…</thinking>` or
`<think>…</think>` inside the reply. `ThinkSplitter` takes the streamed text piece by piece and says
which part is thinking and which is the reply, so the chat shows a clean reply while the thinking
goes to the reply's «Размышление» like a model's own reasoning. Tags may be split across pieces.

`compact` drops lines a model repeats from one thinking to the next (template headings, "I write in
Russian…"), so old thinking sent back with the history costs little and keeps only what changed.
"""

from __future__ import annotations

import re

TAGS = ("thinking", "think")
SEND_TAG = TAGS[0]
_OPEN = re.compile(r"<(thinking|think)>", re.I)
_FULL = tuple(f"<{t}>" for t in TAGS) + tuple(f"</{t}>" for t in TAGS)
_LONGEST = max(len(f) for f in _FULL)


def _maybe_tag_start(tail: str) -> bool:
    """Could `tail` (starting with "<") grow into one of our tags with more text?"""
    low = tail.lower()
    return any(full.startswith(low) for full in _FULL)


class ThinkSplitter:
    """Feed streamed text; get back (kind, text) pieces, kind "text" or "reasoning"."""

    def __init__(self) -> None:
        self._buffer = ""
        self._inside: str | None = None  # the open tag's name
        self._shown = False  # some reply text went out already
        self._skip_space = False  # drop the blank lines right after a closing tag
        self._open_text: list[str] = []
        self.unclosed = ""  # thinking of a tag never closed (filled by finish)

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buffer += text
        out: list[tuple[str, str]] = []
        while self._buffer:
            if self._inside is None:
                match = _OPEN.search(self._buffer)
                if match:
                    self._text(self._buffer[:match.start()], out)
                    self._buffer = self._buffer[match.end():]
                    self._inside = match.group(1).lower()
                    self._open_text = []
                    continue
                cut = self._buffer.rfind("<")
                if cut != -1 and _maybe_tag_start(self._buffer[cut:]):
                    self._text(self._buffer[:cut], out)
                    self._buffer = self._buffer[cut:]
                    break
                self._text(self._buffer, out)
                self._buffer = ""
            else:
                close = re.search(rf"</{self._inside}>", self._buffer, re.I)
                if close:
                    self._think(self._buffer[:close.start()], out)
                    self._buffer = self._buffer[close.end():]
                    self._inside = None
                    self._skip_space = True
                    continue
                cut = self._buffer.rfind("<", max(len(self._buffer) - _LONGEST, 0))
                if cut != -1 and _maybe_tag_start(self._buffer[cut:]):
                    self._think(self._buffer[:cut], out)
                    self._buffer = self._buffer[cut:]
                    break
                self._think(self._buffer, out)
                self._buffer = ""
        return out

    def finish(self) -> list[tuple[str, str]]:
        """The stream ended. A tag never closed keeps its text in `unclosed` (the caller decides)."""
        out: list[tuple[str, str]] = []
        rest, self._buffer = self._buffer, ""
        if self._inside is None:
            self._text(rest, out)
        else:
            self._think(rest, out)
            self.unclosed = "".join(self._open_text)
            self._inside = None
        return out

    @property
    def shown(self) -> bool:
        return self._shown

    def _text(self, text: str, out: list[tuple[str, str]]) -> None:
        if self._skip_space or not self._shown:
            text = text.lstrip()
            if not text:
                return
            self._skip_space = False
        self._shown = True
        out.append(("text", text))

    def _think(self, text: str, out: list[tuple[str, str]]) -> None:
        if text:
            self._open_text.append(text)
            out.append(("reasoning", text))


def split(text: str) -> tuple[str, str]:
    """A whole reply -> (reply text, thinking). An unclosed tag with no reply keeps its text as the reply."""
    splitter = ThinkSplitter()
    pieces = splitter.feed(text) + splitter.finish()
    reply = "".join(t for k, t in pieces if k == "text")
    thinking = "".join(t for k, t in pieces if k == "reasoning")
    if splitter.unclosed and not reply.strip():
        reply = splitter.unclosed.strip()
    return reply, thinking


def _key(line: str) -> str:
    """A line's identity for repeats: letters and digits only, any case (numbers stay, so 40% != 43%)."""
    return re.sub(r"[\W_]+", " ", line.lower()).strip()


def compact(thoughts: list[str]) -> list[str]:
    """Oldest first. Each thinking keeps only lines not said in an earlier one (fully repeated -> "")."""
    seen: set[str] = set()
    out: list[str] = []
    for thought in thoughts:
        kept: list[str] = []
        for line in thought.strip().splitlines():
            key = _key(line)
            if not key:
                if kept and kept[-1] != "":
                    kept.append("")
                continue
            if key not in seen:
                kept.append(line.rstrip())
        seen.update(k for k in map(_key, thought.splitlines()) if k)
        out.append("\n".join(kept).strip())
    return out


def frame(template: str) -> str:
    """The «Размышление» block as sent to a model that thinks and writes in one go."""
    return (f"Before every reply, think it through inside <{SEND_TAG}>…</{SEND_TAG}>, filling in the template "
            f"below; the reader never sees it. Close the tag, then write the reply itself.\n\n"
            f"<{SEND_TAG}>\n{template.strip()}\n</{SEND_TAG}>")


def wrap(thinking: str, reply: str) -> str:
    """An old reply sent back with its (compacted) thinking, in the same tags the model writes."""
    return f"<{SEND_TAG}>\n{thinking.strip()}\n</{SEND_TAG}>\n\n{reply}"
