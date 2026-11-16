"""Token counts without a tokenizer library (wave 3 of the SillyTavern parity plan, docs/ST_PARITY.md section 10).

Pure Python (Termux: no tiktoken). Two parts:

`count(text)` estimates what a modern tokenizer (OpenAI o200k, Gemini, Claude) makes of a text, by script: a Latin
word is about one token per 5 letters, a Cyrillic word one per 3.5 (Russian costs more than English in every
model), each CJK character one, digits in groups of three, each run of punctuation and each line break one. The old
rule «4 characters = 1 token» undercounted Russian by about a third, so long Russian chats overflowed the window.
Results are remembered per text (an LRU cache): a chat's messages are counted once, not on every trimming step.

Calibration: SillyTavern counts with the model's own tokenizer where it has one and guesses elsewhere. We ask the
service instead: every chat reply reports how many tokens the prompt really had, and `learn()` keeps a running
ratio real / estimated per connection and model (settings `token_ratio`). `window()` turns a window in real tokens
into the estimate's units, so the budget matches what the service will count, for any model.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any

# One pattern, one pass: which class each run belongs to (the group that matched).
_RUNS = re.compile(
    r"(?P<cjk>[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿])"
    r"|(?P<cyr>[Ѐ-ӿ]+)"
    r"|(?P<lat>[A-Za-zÀ-ɏ]+)"
    r"|(?P<num>\d+)"
    r"|(?P<nl>\n+)"
    r"|(?P<sp>[ \t\r\f\v]+)"
    r"|(?P<word>[^\W\d_]+)"
    r"|(?P<sym>[^\w\s]+)"
)
LATIN_LETTERS = 5.0  # letters per token in a Latin word
CYRILLIC_LETTERS = 3.5
OTHER_LETTERS = 2.5  # Greek, Arabic, Hebrew, Indic…: costly everywhere
DIGITS = 3  # numbers are split into groups of up to three digits
SPACES = 4  # a long run of spaces (indentation): one token per four; a single space rides with the next word

RATIO_KEY = "token_ratio"  # settings: {"<connection id>|<model>": ratio}
RATIO_MIN, RATIO_MAX = 0.5, 3.0
LEARN_RATE = 0.3  # weight of the newest reply in the running ratio
# Shorter prompts say too little. Kept low on purpose: with a high ratio and a small window every prompt is small,
# and a higher floor would never let a wrong ratio come down. The service's few tokens per message for the roles
# stay inside the ratio: the budget then errs on the safe side (a little less room, never an overflow).
MIN_LEARN_TOKENS = 50


@lru_cache(maxsize=8192)
def count(text: str) -> int:
    """Estimated tokens of `text` (0 for an empty text, else at least 1)."""
    if not text:
        return 0
    total = 0.0
    for match in _RUNS.finditer(text):
        kind = match.lastgroup
        size = match.end() - match.start()
        if kind == "lat":
            total += math.ceil(size / LATIN_LETTERS)
        elif kind == "cyr":
            total += math.ceil(size / CYRILLIC_LETTERS)
        elif kind == "cjk":
            total += 1
        elif kind == "num":
            total += math.ceil(size / DIGITS)
        elif kind == "nl":
            total += 1
        elif kind == "sp":
            total += size // SPACES if size > 1 else 0
        elif kind == "word":
            total += math.ceil(size / OTHER_LETTERS)
        else:  # punctuation, symbols, emoji: a run of the same kind is often one token, mixed ones more
            total += max(1, math.ceil(size / 2))
    return max(1, int(total))


def chars_for(text: str, max_tokens: int) -> int:
    """How many leading characters of `text` fit `max_tokens` by `count` (a binary search: counts grow with the
    length). For cutting a text to a token size without guessing characters per token."""
    if max_tokens <= 0 or not text:
        return 0
    if count(text) <= max_tokens:
        return len(text)
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if count(text[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return low


def ratio_key(connection_id: str, model: str) -> str:
    return f"{connection_id}|{model}"


def ratio(settings: dict[str, Any] | None, connection_id: str, model: str) -> float:
    """Real tokens per estimated token for this connection and model (1.0 until a reply has taught it)."""
    stored = (settings or {}).get(ratio_key(connection_id, model))
    if isinstance(stored, (int, float)) and not isinstance(stored, bool) and RATIO_MIN <= stored <= RATIO_MAX:
        return float(stored)
    return 1.0


def learn(old: float | None, estimated: int, real: int) -> float | None:
    """The running ratio after one more reply: None when this reply says nothing (too short, no count)."""
    if estimated < MIN_LEARN_TOKENS or real <= 0:
        return None
    seen = min(RATIO_MAX, max(RATIO_MIN, real / estimated))
    if old is None:
        return round(seen, 4)
    return round(old + LEARN_RATE * (seen - old), 4)


def window(real_tokens: int, factor: float) -> int:
    """A window in the service's tokens -> the same window in estimated tokens (0 stays «no limit»)."""
    if real_tokens <= 0:
        return 0
    return max(1, int(real_tokens / (factor or 1.0)))
