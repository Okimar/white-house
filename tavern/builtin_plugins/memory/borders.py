"""Where one chapter ends and the next begins (pure: no database, no network). ADR 0019 sections 1 and 4.2.

A scene is one place, continuous time, the same active participants, one goal or conflict. A border is any change
of these, or an explicit marker. Not a border: a shift of mood or topic inside one conversation, a short flashback,
someone dropping in for one line, OOC.

THE SCRIPT (no model). Every gap BEFORE a user message gets a score (the rest of the gaps are only used for the
last-resort cut). Evidence, summed and capped at 1:
  strong  1.0  an explicit marker at the start of the user's message or of the reply after it: `***`, `---`,
               «Глава», «Часть», Chapter;
          0.8  «Тем временем» / Meanwhile, or time words at the start: «На следующий день», «Утром», «Через час»,
               «Позже», «Спустя неделю», "The next day", "Hours later";
  medium  0.3  the cast changed: names (chat members, lorebook people and places, capitalised names inside
               sentences) in the 4 messages before vs the 4 after, Jaccard < 0.3; a name in one message only is a
               cameo and does not count;
          0.35 a cohesion valley: TextTiling over word stems (`core.retrieval.stem`), the depth of the dip in the
               similarity of the blocks before and after, counted only when it stands out from the other gaps;
          0.3  vector drift: 1 - cos of the mean vectors before and after (only where vectors exist), also only
               when it stands out;
  weak    0.15 a real-time pause longer than 6 hours.
A gap whose user message is OOC («((…))», «OOC:», «//») scores 0 and is never a border.

WHEN A CHAPTER CLOSES (`choose`): the open part is longer than N messages (default 40, setting) and the best gap
of [N/4 .. 3N] scores at least THRESHOLD (0.5: a strong sign, or two medium ones); among near-equal gaps the
earliest wins, so a marker in the middle is not skipped for a later one. When the open part is longer than 3N a
cut is forced at the best gap (the latest on a tie; any gap if there is no user message at all).

THE MODEL JUDGE (optional, off by default): the script proposes candidate gaps, a small model gets the open part
with message numbers and answers `{"finished": false}` or `{"finished": true, "ends_at": 47, "reason": "..."}`
(`ends_at` = the number of the last message of the finished scene). `judge_messages` builds the request,
`judge_answer` checks the answer. The call itself is made by work.py inside a job step.
"""

from __future__ import annotations

import math
import re
from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tavern.providers.base import ChatMessage
from tavern.core.retrieval import stem

from . import pieces

WINDOW = 4  # messages on each side of a gap
THRESHOLD = 0.5
TIE = 0.05  # gaps this close to the best are "as good": the earliest wins
STRONG_MARKER, STRONG_TIME, CAST, VALLEY_MAX, DRIFT_MAX, PAUSE = 1.0, 0.8, 0.3, 0.35, 0.3, 0.15
PAUSE_HOURS = 6.0
JACCARD_MAX = 0.3
DEFAULT_SIZE = 40

_LEAD = r"^[\s*_#>\[(«\"'—–-]*"
_SEPARATOR = re.compile(r"^\s*(?:\*\s*\*\s*\*+|-{3,}|_{3,}|={3,})(?=\s|$)")
_CHAPTER = re.compile(_LEAD + r"(глава|часть|chapter|part|эпилог|пролог|epilogue|prologue|акт)\b", re.I)
_MEANWHILE = re.compile(_LEAD + r"(тем\s+временем|meanwhile)\b", re.I)
_TIME = re.compile(_LEAD + r"(" + "|".join([
    r"на\s+следующ\w+\s+(день|утро|ночь|вечер)", r"следующ\w+\s+(утром|днём|днем|вечером|ночью)",
    r"(утром|днём|днем|вечером|ночью|на\s+рассвете|на\s+закате|к\s+вечеру|к\s+утру|позже|позднее|спустя)\b",
    r"через\s+(\S+\s+){0,2}(секунд|минут|час|ден|дн|недел|месяц|год|лет|мгновени)",
    r"(\S+\s+){0,2}(минут|час|дня|дней|недел|месяц|год|лет)\w*\s+спустя", r"прошл[оаи]\s+(\S+\s+){0,2}\w+",
    r"the\s+next\s+(day|morning|evening|night)", r"next\s+(day|morning)", r"that\s+(night|evening|morning)",
    r"(later|some\s+time\s+later|after\s+a\s+while)\b", r"(\S+\s+){0,2}(minutes|hours|days|weeks|months|years)\s+later",
    r"a\s+(few|couple\s+of)\s+(minutes|hours|days|weeks)",
]) + r")", re.I)
_OOC = re.compile(r"^\s*(\(\(|//|\[?\(?\s*ooc\b|\(\s*ooc\b)", re.I)


@dataclass(frozen=True)
class Gap:
    """The gap BEFORE message `index` of the list that was scored."""

    index: int
    score: float
    reasons: tuple[str, ...]
    cut_ok: bool  # before a user message and not OOC: the script may cut here


@dataclass(frozen=True)
class Cut:
    index: int  # the first message of the next chapter
    score: float
    reason: str
    forced: bool = False


def explicit(text: str) -> tuple[float, str]:
    """(strength, label) of a scene marker at the start of a message; (0, "") when there is none."""
    head = text[:300]
    if _SEPARATOR.match(head):
        return STRONG_MARKER, "маркер «***»"
    found = _CHAPTER.match(head)
    if found:
        return STRONG_MARKER, f"маркер «{found.group(1)}»"
    found = _MEANWHILE.match(head)
    if found:
        return STRONG_TIME, f"«{found.group(1)}»"
    found = _TIME.match(head)
    if found:
        return STRONG_TIME, f"слова о времени: «{found.group(1).strip()}»"
    return 0.0, ""


def is_ooc(text: str) -> bool:
    return bool(_OOC.match(text))


def known_stems(names: list[str]) -> frozenset[str]:
    """Stems of the words of the given names (chat members, lorebook people and places)."""
    out: set[str] = set()
    for name in names:
        for word in pieces._WORD.findall(name):
            if len(word) >= pieces.MIN_WORD:
                out.add(stem(word.lower()))
    return frozenset(out)


def _names(message: dict[str, Any], known: frozenset[str]) -> set[str]:
    text = message.get("content") or ""
    found = {k[1:] for k in pieces.keywords(text) if k.startswith("*") and not any(ch.isdigit() for ch in k)}
    for word in pieces._WORD.findall(text):
        key = stem(word.lower())
        if key in known:
            found.add(key)
    speaker = (message.get("name") or "").strip()
    if speaker:
        found.update(stem(w.lower()) for w in pieces._WORD.findall(speaker) if len(w) >= pieces.MIN_WORD)
    return found


def _words(text: str) -> Counter[str]:
    out: Counter[str] = Counter()
    for word in pieces._WORD.findall(text.lower()):
        if len(word) >= pieces.MIN_WORD and word not in pieces.STOP and not word.isdigit():
            out[stem(word)] += 1
    return out


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(count * b.get(key, 0) for key, count in a.items())
    return dot / (math.sqrt(sum(c * c for c in a.values())) * math.sqrt(sum(c * c for c in b.values())))


def _when(message: dict[str, Any]) -> datetime | None:
    raw = str(message.get("created_at") or "")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _stands_out(values: list[float], floor: float) -> float:
    """The level above which a value counts as a dip / a jump: the mean plus half a deviation, never below `floor`."""
    if len(values) < 4:
        return math.inf
    mean = sum(values) / len(values)
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return max(floor, mean + 0.5 * spread)


def _mean_vector(vectors: list[array | None]) -> array | None:
    present = [v for v in vectors if v is not None]
    if not present:
        return None
    total = [sum(column) for column in zip(*present)]
    norm = math.sqrt(sum(x * x for x in total))
    return array("f", [x / norm for x in total] if norm else total)


def score_gaps(messages: list[dict[str, Any]], *, known: frozenset[str] = frozenset(),
               vectors: list[array | None] | None = None, window: int = WINDOW) -> list[Gap]:
    """A Gap for each position 1..len-1 (the gap before that message). `vectors`: one per message (the mean of its
    pieces' vectors) or None where there is none. See the module docstring for the evidence and its weights."""
    n = len(messages)
    if n < 2:
        return []
    texts = [m.get("content") or "" for m in messages]
    names = [_names(m, known) for m in messages]
    words = [_words(t) for t in texts]
    marks = [explicit(t) for t in texts]

    # cohesion: similarity of the blocks around each gap (needs two messages on each side), then the dip's depth
    sims: list[float | None] = [None] * n
    for i in range(2, n - 1):
        before, after = Counter(), Counter()
        for w in words[max(0, i - window):i]:
            before.update(w)
        for w in words[i:i + window]:
            after.update(w)
        sims[i] = _cosine(before, after)

    def peak(i: int, step: int) -> float:
        best = sims[i] or 0.0
        j = i + step
        while 0 <= j < n and sims[j] is not None and sims[j] >= best:  # type: ignore[operator]
            best = sims[j]  # type: ignore[assignment]
            j += step
        return best

    depth = {i: peak(i, -1) + peak(i, 1) - 2 * (sims[i] or 0.0) for i in range(n) if sims[i] is not None}
    valley_from = _stands_out(list(depth.values()), 0.15)

    drift: dict[int, float] = {}
    if vectors:
        for i in range(1, n):
            a = _mean_vector(vectors[max(0, i - window):i])
            b = _mean_vector(vectors[i:i + window])
            if a is not None and b is not None and len(a) == len(b):
                drift[i] = 1.0 - sum(x * y for x, y in zip(a, b))
    drift_from = _stands_out(list(drift.values()), 0.08)

    gaps: list[Gap] = []
    for i in range(1, n):
        message = messages[i]
        ooc = is_ooc(texts[i])
        cut_ok = message.get("role") == "user" and not ooc
        if ooc:
            gaps.append(Gap(i, 0.0, ("OOC",), False))
            continue
        score = 0.0
        reasons: list[str] = []
        strength, label = marks[i]
        if i + 1 < n and messages[i + 1].get("role") == "assistant" and marks[i + 1][0] > strength:
            strength, label = marks[i + 1]
        if strength:
            score += strength
            reasons.append(label)
        before_names = _cameo_filter(names[max(0, i - window):i])
        after_names = _cameo_filter(names[i:i + window])
        if before_names and after_names:
            jaccard = len(before_names & after_names) / len(before_names | after_names)
            if jaccard < JACCARD_MAX:
                score += CAST
                reasons.append("сменились действующие лица")
        if i in depth and depth[i] >= valley_from:
            score += VALLEY_MAX * min(1.0, depth[i] / 0.5)
            reasons.append("смена темы разговора (слова)")
        if i in drift and drift[i] >= drift_from:
            score += DRIFT_MAX * min(1.0, drift[i] / 0.4)
            reasons.append("смена смысла (векторы)")
        before_time, after_time = _when(messages[i - 1]), _when(message)
        if before_time and after_time and (after_time - before_time).total_seconds() > PAUSE_HOURS * 3600:
            score += PAUSE
            reasons.append("долгая пауза")
        gaps.append(Gap(i, min(1.0, score), tuple(reasons), cut_ok))
    return gaps


def _cameo_filter(sets: list[set[str]]) -> set[str]:
    """Names in at least two messages of the window (one message only is a cameo; a one-message window counts it)."""
    need = min(2, len(sets))
    count: Counter[str] = Counter()
    for names in sets:
        count.update(names)
    return {name for name, c in count.items() if c >= need}


def min_length(size: int) -> int:
    return max(2, size // 4)


def choose(gaps: list[Gap], count: int, size: int, threshold: float = THRESHOLD) -> Cut | None:
    """The cut that closes the next chapter of the open part of `count` messages, or None (see the docstring)."""
    if count <= size:
        return None
    low, high = min_length(size), min(3 * size, count - 1)
    inside = [g for g in gaps if low <= g.index <= high]
    good = [g for g in inside if g.cut_ok and g.score >= threshold]
    if good:
        best = max(g.score for g in good)
        pick = next(g for g in good if g.score >= best - TIE)
        return Cut(pick.index, pick.score, "; ".join(pick.reasons))
    if count <= 3 * size:
        return None
    pool = [g for g in inside if g.cut_ok] or inside
    if not pool:
        return None
    best = max(g.score for g in pool)
    pick = [g for g in pool if g.score >= best][-1]
    reason = "; ".join(("длинная глава: разрез по лучшему месту", *pick.reasons))
    return Cut(pick.index, pick.score, reason, forced=True)


# -- the optional model judge ----------------------------------------------------------------------

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "finished": {"type": "boolean"},
        "ends_at": {"type": "integer", "minimum": 0},
        "reason": {"type": "string"},
    },
    "required": ["finished"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """You split a long roleplay chat into chapters (scenes). You get the messages of the part that is \
not in a chapter yet, each with its number, and say whether the FIRST scene of this part is already finished.

A scene is one place, continuous time, the same active participants, one goal or conflict. A border between scenes \
is any change of these (another place, a time jump, a different set of people on stage, a new goal), or an explicit \
marker such as "***", "Chapter", "Meanwhile", "The next day". These are NOT borders: a shift of mood or topic \
inside one conversation, a short flashback, someone dropping in for one line, out-of-character remarks.

Answer with JSON only. If the first scene goes on to the end of the part (or you are not sure), answer \
{"finished": false}. If it is finished, answer {"finished": true, "ends_at": N, "reason": "..."} where N is the \
NUMBER of the last message that still belongs to the first scene (the next scene starts right after it), and \
"reason" is one short phrase in the language of the chat saying what changed."""

MAX_JUDGE_CHARS = 500


def judge_messages(window: list[dict[str, Any]], hints: list[Gap]) -> list[ChatMessage]:
    """The judge's request: the open part numbered by message number (depth + 1), the script's candidates as hints."""
    lines = []
    for message in window:
        text = " ".join((message.get("content") or "").split())
        if len(text) > MAX_JUDGE_CHARS:
            text = text[:MAX_JUDGE_CHARS] + "…"
        lines.append(f"[{message['depth'] + 1}] {message.get('name') or message['role']}: {text}")
    hint = ""
    if hints:
        numbers = ", ".join(str(window[g.index - 1]["depth"] + 1) for g in hints)
        hint = f"\n\nA script saw a possible border after the messages numbered: {numbers}. It may be wrong."
    return [ChatMessage("system", JUDGE_SYSTEM),
            ChatMessage("user", "Messages:\n" + "\n".join(lines) + hint)]


def hint_gaps(gaps: list[Gap], low: int, high: int, limit: int = 3) -> list[Gap]:
    """The script's best candidates inside [low, high], best first (at least 0.2)."""
    chosen = [g for g in gaps if low <= g.index <= high and g.cut_ok and g.score >= 0.2]
    return sorted(chosen, key=lambda g: -g.score)[:limit]


def judge_answer(value: Any, window: list[dict[str, Any]], size: int) -> Cut | None:
    """A checked answer: the Cut (index = first message of the next chapter) or None for «not finished» and for an
    answer that does not fit the window (a number that is not there, too early, or the last message)."""
    if not isinstance(value, dict) or value.get("finished") is not True:
        return None
    number = value.get("ends_at")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return None
    position = next((i for i, m in enumerate(window) if m["depth"] + 1 == int(number)), None)
    if position is None:
        return None
    index = position + 1
    if index < min_length(size) or index > len(window) - 1:
        return None
    return Cut(index, 1.0, str(value.get("reason") or "модель: сцена закончилась").strip()[:200])
