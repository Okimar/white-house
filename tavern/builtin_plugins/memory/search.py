"""Scoring pieces against the reply's query (pure: no database, no network). ADR 0019 section 4.1.

Two parts, and either alone can find a piece (keywords weigh as much as vectors: names and numbers are exactly
what gets lost):
  * vector part: the best dot product of a piece's vector with a query vector (vectors are normalized, so a dot is
    the cosine), mapped to 0..1 above the same per-service threshold the bubbles use;
  * keyword part: the summed IDF weight (over this chat's pieces) of the query's stems that the piece has; a
    stem the piece marks as a name or a number (`*stem`) counts double; `s / (s + KW_HALF)` maps it to 0..1.
  item score = max(vec, kw) + 0.25 * min(vec, kw)   (both together rank higher)

Speed (ADR risk R1), in the order the ADR gives: vectors are normalized (a dot is the cosine) and kept in memory
between replies; `math.sumprod` does the dot product where Python has it (3.12+, Termux too), else
`sum(map(mul, ...))`; when pieces x queries x size would pass MAX_OPS the query vectors are averaged into one; and
when even one query would pass it, only the newest pieces and those with a keyword hit get a vector score.
Stale vectors (the text changed after the vector was made) are not scored; such a piece is found by keywords only.
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from operator import mul
from typing import Any

from tavern.core.retrieval import stem

from .pieces import MIN_WORD, STOP, _WORD

try:
    from math import sumprod as _dot
except ImportError:  # Python 3.11
    def _dot(a: Any, b: Any) -> float:
        return sum(map(mul, a, b))

KW_HALF = 3.0
KW_NAME_WEIGHT = 2.0
BOTH_BONUS = 0.25
MIN_SCORE = 0.25  # below this a piece is not offered
MAX_OPS = 3_000_000  # multiply-adds one reply may spend on vectors (5000 pieces of 512 numbers, one query)


@dataclass(frozen=True)
class Candidate:
    piece_id: str
    message_id: str
    index: int  # position of its message on the branch (story order)
    hash: str
    tokens: int
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class Scored:
    candidate: Candidate
    score: float
    vec: float
    kw: float


def unit(vector: Any) -> array:
    """A query vector as a normalized array('f')."""
    norm = math.sqrt(sum(x * x for x in vector))
    return array("f", [x / norm for x in vector] if norm else vector)


def query_stems(texts: list[str], names: list[str] | None = None) -> set[str]:
    """Stems of the words of the recent chat texts, plus the names of the chat's members."""
    out: set[str] = set()
    for text in [*texts, *(names or [])]:
        for word in _WORD.findall(text):
            low = word.lower()
            if any(ch.isdigit() for ch in low):
                out.add(low)
            elif len(low) >= MIN_WORD and low not in STOP:
                out.add(stem(low))
    return out


def idf_table(candidates: list[Candidate]) -> dict[str, float]:
    """stem -> IDF over the candidates (a stem in every piece weighs nearly nothing)."""
    df: dict[str, int] = {}
    for candidate in candidates:
        for keyword in {k.lstrip("*") for k in candidate.keywords}:
            df[keyword] = df.get(keyword, 0) + 1
    n = len(candidates)
    return {k: math.log(1 + n / (1 + count)) for k, count in df.items()}


def keyword_part(candidate: Candidate, stems: set[str], idf: dict[str, float]) -> float:
    total = 0.0
    for keyword in candidate.keywords:
        starred = keyword.startswith("*")
        bare = keyword.lstrip("*")
        if bare in stems:
            total += idf.get(bare, 0.0) * (KW_NAME_WEIGHT if starred else 1.0)
    return total / (total + KW_HALF) if total else 0.0


def vector_part(cosine: float, threshold: float) -> float:
    if threshold >= 1.0:
        return 1.0 if cosine >= 1.0 else 0.0
    return max(0.0, (cosine - threshold) / (1.0 - threshold))


def combine(vec: float, kw: float) -> float:
    return max(vec, kw) + BOTH_BONUS * min(vec, kw)


def bounded_queries(queries: list[array], pieces: int, size: int) -> list[array]:
    """The query vectors, or their average when scoring them all would pass MAX_OPS."""
    if len(queries) > 1 and pieces * len(queries) * size > MAX_OPS:
        mean = [sum(column) / len(queries) for column in zip(*queries)]
        return [unit(mean)]
    return queries


def score(candidates: list[Candidate], vectors: dict[str, tuple[str, array]], queries: list[array],
          stems: set[str], threshold: float) -> list[Scored]:
    """Every candidate with a score of at least MIN_SCORE, best first. `vectors`: piece id -> (hash, vector);
    `queries`: normalized query vectors (may be empty: keywords only)."""
    idf = idf_table(candidates)
    size = len(queries[0]) if queries else 0
    kws = {c.piece_id: (keyword_part(c, stems, idf) if stems else 0.0) for c in candidates}
    with_vectors = [c for c in candidates if c.piece_id in vectors]
    queries = bounded_queries(queries, len(with_vectors), size)
    allowed = {c.piece_id for c in with_vectors}
    if queries and len(with_vectors) * size > MAX_OPS:  # too many even for one query: keyword hits and the newest
        ranked = sorted(with_vectors, key=lambda c: (kws[c.piece_id] <= 0, -c.index))
        allowed = {c.piece_id for c in ranked[:max(1, MAX_OPS // size)]}
    out: list[Scored] = []
    for candidate in candidates:
        vec = 0.0
        stored = vectors.get(candidate.piece_id)
        if (stored is not None and candidate.piece_id in allowed and stored[0] == candidate.hash and queries
                and len(stored[1]) == size):
            best = max(_dot(query, stored[1]) for query in queries)
            vec = vector_part(best, threshold)
        kw = kws[candidate.piece_id]
        total = combine(vec, kw)
        if total >= MIN_SCORE:
            out.append(Scored(candidate, total, vec, kw))
    out.sort(key=lambda s: (-s.score, s.candidate.index))
    return out


# -- two levels: a chapter's summary or its pieces (M3) -----------------------------------------------

MARGIN = 0.1  # a piece must beat the summary by this much to go instead of it (ADR 0019 4.1, step 3)


def choose_form(summary: float | None, piece: float, margin: float = MARGIN) -> str:
    """"summary" or "pieces" for a found chapter. `summary`: the summary's score (None: the chapter has no summary
    to send); `piece`: its best piece's score (0: none was found). A piece goes instead of the summary only when it
    beats it by more than `margin`, so a vague match does not replace the retelling."""
    if summary is None:
        return "pieces"
    return "pieces" if piece > summary + margin else "summary"


def sticky_live(hit_gen: int, generation: int, sticky: int) -> bool:
    """A chapter found in reply `hit_gen` is kept for the next `sticky` replies (not for the reply it was found in:
    that one found it anew)."""
    return 0 < generation - hit_gen <= sticky


def member_allowed(participants: list[str], speaker: str, *, several: bool, remembers_all: bool = False) -> bool:
    """May the speaking member get a chapter word for word? In a chat of one character: always. In a group: only a
    member who was in the chapter, or one with «Помнит всё». No known speaker (the user's own turn): yes."""
    if not several or not speaker or remembers_all:
        return True
    return speaker in participants
