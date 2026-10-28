"""Which character blocks go into a prompt: blue always, green when their topic comes up.

Pure logic: the caller passes the blocks of one character, the text to look in (the last few
messages), earlier hits for "stays N replies", and optional meaning scores from vectors.

Rules (owner's decisions, see docs/ROADMAP.md, stage 4б):
- a block that is off, or under an off block, never goes;
- 'always' (blue) blocks always go, each on its own;
- a 'search' (green) block is found by its keywords or title (Russian endings are ignored, so
  "волосы" finds "волосами"), by meaning (vector score >= threshold), or because it was found
  less than `sticky` replies ago; a found block brings its whole subtree (the group) along;
- every block carries its path ("Аня › Внешность › Волосы") so the model knows what it is;
- green blocks share a token budget; fresh keyword hits win over meaning, meaning over sticky.

After the owner's review (migration 0007):
- a free bubble (`detached`) and everything under it is not sent;
- a blue bubble with `every` = N skips N replies between sendings (0 = every reply);
- a green bubble with `chance` < 100 rolls when found; a failed roll keeps it out for its
  "stays N replies" too (so regenerating cannot force it in), sticky 0 = a new roll every time;
- a link between two bubbles pulls the other end along (with its sub-bubbles, not its parents),
  one step only; the pulled bubble rolls its own chance.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from tavern.core.prompt import estimate_tokens

PATH_SEPARATOR = " › "
# Longest first. Enough to make a keyword match its Russian forms; not a full stemmer.
RU_ENDINGS = sorted({
    "иями", "ями", "ами", "ией", "иях", "ого", "его", "ому", "ему", "ыми", "ими", "ая", "яя", "ое",
    "ее", "ые", "ие", "ый", "ий", "ой", "ей", "ов", "ев", "ом", "ем", "ам", "ям", "ах", "ях", "ую",
    "юю", "ою", "ею", "ия", "ие", "ы", "и", "а", "я", "о", "е", "у", "ю", "ь", "й",
}, key=len, reverse=True)
EN_ENDINGS = ("ing", "es", "ed", "s")
CYRILLIC = re.compile(r"[а-яё]", re.I)


@dataclass
class FoundBlock:
    block: dict[str, Any]
    path: str
    reason: str  # always | keyword | meaning | sticky (for the group's root) | group (pulled along)


@dataclass
class Selection:
    found: list[FoundBlock]
    fresh_hits: list[str] = field(default_factory=list)  # green blocks found now (refresh "stays")
    misses: list[str] = field(default_factory=list)  # green blocks found now whose chance failed
    dropped: list[str] = field(default_factory=list)  # titles that did not fit the budget


def stem(word: str) -> str:
    word = word.lower()
    endings = RU_ENDINGS if CYRILLIC.search(word) else EN_ENDINGS
    for ending in endings:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def keyword_pattern(keyword: str) -> re.Pattern[str] | None:
    """A keyword as a pattern: words by stem (any ending), /regex/ as written (ST style)."""
    keyword = keyword.strip()
    if not keyword:
        return None
    if len(keyword) > 2 and keyword.startswith("/") and keyword.rstrip("gimsuy").endswith("/"):
        body = keyword[1:keyword.rstrip("gimsuy").rfind("/")]
        try:
            return re.compile(body, re.I)
        except re.error:
            return None
    words = re.findall(r"\w+", keyword)
    if not words:
        return None
    parts = [re.escape(stem(w)) + r"\w*" for w in words]
    return re.compile(r"(?<!\w)" + r"\W+".join(parts), re.I)


def _tree(blocks: list[dict[str, Any]]) -> tuple[dict[str, dict], dict[str | None, list[dict]]]:
    by_id = {b["id"]: b for b in blocks}
    children: dict[str | None, list[dict]] = {}
    for block in blocks:
        parent = block["parent_id"] if block["parent_id"] in by_id else None
        children.setdefault(parent, []).append(block)
    for items in children.values():
        items.sort(key=lambda b: (b["position"], b.get("created_at", "")))
    return by_id, children


def select_blocks(
    blocks: list[dict[str, Any]],
    *,
    owner_name: str,
    scan_text: str,
    hits: dict[str, int] | None = None,
    generation: int = 0,
    budget_tokens: int = 2000,
    meaning: dict[str, float] | None = None,
    threshold: float = 0.55,
    missed: set[str] | None = None,
    links: list[tuple[str, str]] | None = None,
    rng: Any = None,
) -> Selection:
    """`hits`: block id -> generation it was last found at; `missed`: those of them whose chance
    failed then. `links`: pairs of linked block ids. `rng`: random.Random-like, for tests."""
    hits = hits or {}
    meaning = meaning or {}
    missed = missed or set()
    rng = rng or random
    by_id, children = _tree(blocks)

    order: list[str] = []  # tree order of usable blocks (not off, not under off)
    paths: dict[str, str] = {}

    def walk(parent: str | None, prefix: str) -> None:
        for block in children.get(parent, []):
            if block["mode"] == "off" or block.get("detached"):
                continue
            path = f"{prefix}{PATH_SEPARATOR}{block['title']}"
            paths[block["id"]] = path
            order.append(block["id"])
            walk(block["id"], path)

    walk(None, owner_name)

    def subtree(block_id: str) -> list[str]:
        result = [block_id]
        for child in children.get(block_id, []):
            if child["id"] in paths:
                result.extend(subtree(child["id"]))
        return result

    def held(block_id: str) -> bool:
        return block_id in hits and generation - hits[block_id] <= by_id[block_id].get("sticky", 3)

    misses: list[str] = []

    def lucky(block_id: str) -> bool:
        """Chance roll for a freshly found green block; a failed one is remembered as a miss."""
        chance = by_id[block_id].get("chance", 100)
        if chance >= 100 or (held(block_id) and block_id not in missed):
            return True
        if rng.random() * 100 < chance:
            return True
        misses.append(block_id)
        return False

    chosen: dict[str, str] = {}  # block id -> reason
    for block_id in order:
        block = by_id[block_id]
        if block["mode"] == "always" and (generation - 1) % (block.get("every", 0) + 1) == 0:
            chosen[block_id] = "always"

    # Candidate green groups with their reason and rank (lower = first).
    candidates: list[tuple[float, str, str]] = []
    fresh: list[str] = []
    text = scan_text or ""
    for block_id in order:
        block = by_id[block_id]
        if block["mode"] != "search":
            continue
        count = 0
        for keyword in [block["title"], *block.get("keywords", [])]:
            pattern = keyword_pattern(keyword)
            if pattern is not None:
                count += len(pattern.findall(text))
        score = meaning.get(block_id, 0.0)
        if held(block_id) and block_id in missed:
            continue  # its chance failed a moment ago: out until the hold is over
        if count:
            candidates.append((0 - count, block_id, "keyword"))
        elif score >= threshold:
            candidates.append((1 - score, block_id, "meaning"))
        elif held(block_id):
            candidates.append((2.0, block_id, "sticky"))
    candidates.sort()

    used = 0
    dropped: list[str] = []

    def take(block_id: str, reason: str) -> None:
        nonlocal used
        group = [b for b in subtree(block_id) if b not in chosen]
        cost = sum(estimate_tokens(by_id[b]["content"]) for b in group)
        if budget_tokens and used + cost > budget_tokens:
            dropped.append(by_id[block_id]["title"])
            return
        if reason != "sticky" and by_id[block_id]["mode"] == "search":
            if not lucky(block_id):
                return
            fresh.append(block_id)
        used += cost
        chosen[block_id] = reason
        for member in group[1:]:
            chosen.setdefault(member, "group")

    for _, block_id, reason in candidates:
        if block_id not in chosen:
            take(block_id, reason)

    # Links: one step from what is going, to the linked bubble and its sub-bubbles only.
    partners: dict[str, list[str]] = {}
    for a, b in links or []:
        partners.setdefault(a, []).append(b)
        partners.setdefault(b, []).append(a)
    for block_id in list(chosen):
        for other in partners.get(block_id, []):
            if other in paths and other not in chosen and not (held(other) and other in missed):
                take(other, "link")

    found = [FoundBlock(by_id[b], paths[b], chosen[b]) for b in order
             if b in chosen and by_id[b]["content"].strip()]
    return Selection(found=found, fresh_hits=[b for b in fresh if b in chosen], dropped=dropped, misses=misses)


def cosine(a: Any, b: Any) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0
