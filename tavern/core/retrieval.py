"""Which blocks ("bubbles") go into a prompt: blue always, green when their topic comes up.

Pure logic: the caller passes the blocks of one owner (a character) or a pool of lorebooks, the
text to look in (the last few messages), earlier hits for "stays N replies", and optional meaning
scores from vectors.

Rules (owner's decisions, see docs/ROADMAP.md, stage 4б):
- a block that is off, or under an off block, never goes;
- 'always' (blue) blocks always go, each on its own, unless they sit under a green one;
- a 'search' (green) block is found by its keywords or title (Russian endings are ignored, so
  "волосы" finds "волосами"), by meaning (vector score >= threshold), or because it was found
  less than `sticky` replies ago;
- layers (stage 10в): a found block brings its blue sub-bubbles along (and theirs, while they stay
  blue); a blue bubble under a green one goes only with it; a green sub-bubble waits for its own
  words, so a deep detail costs nothing until it comes up;
- every block carries its path ("Аня › Внешность › Волосы") so the model knows what it is;
- green blocks share a token budget; fresh keyword hits win over meaning, meaning over sticky.

After the owner's review (migration 0007):
- a free bubble (`detached`) and everything under it is not sent;
- a blue bubble with `every` = N skips N replies between sendings (0 = every reply);
- a green bubble with `chance` < 100 rolls when found; a failed roll keeps it out for its
  "stays N replies" too (so regenerating cannot force it in), sticky 0 = a new roll every time;
- a link between two bubbles pulls the other end along (with its blue sub-bubbles, not its parents),
  one step only; the pulled bubble rolls its own chance.

Finer rules (stage 5, `block["rules"]`, all optional; shown under «Ещё»):
    title_key   bool   the title counts as a keyword (default true)
    keys2       [str]  second keywords, checked with `logic`:
    logic       and_any (default) | and_all | not_any | not_all
    whole       bool   whole words only: no other endings
    case        bool   letter case matters
    scan        int    how many last messages this block looks in (default: the caller's)
    cooldown    int    after it was found and its "stays" ran out, it cannot be found for N replies
    delay       int    it cannot go before reply N of the chat
    groups      [str]  of the found blocks of one group only one goes: one still held wins, else
    main        bool   a main one (first in order), else a weighted draw by
    weight      int    (default 100)
    filter      {mode: only|except, ids: [character ids]} who must (not) take part in the chat
    no_wake     bool   its text does not wake other blocks (chains)
    not_woken   bool   it is not woken by the text of other blocks
    place       where a lorebook entry goes (core/assembly.py); not used here
    order       int    SillyTavern's «Order» of an entry (default 100; `block_order`): for lorebooks the higher
                       number is considered first, so it is the last one cut by the budget; at one depth the
                       entries go by it, lower first (core/assembly.py)
    no_budget   bool   SillyTavern's «ignore budget»: the entry costs nothing and is never cut by the budget

Chains (`recursion` > 0): the text of blocks that are going (plus `seed_text`) is looked through
for more green blocks, up to `recursion` steps.

Lorebooks (`by_order`), the way SillyTavern does it: the pool's blocks are ranked by book (the pool's order),
then by `block_order` highest first, then by their place among neighbours, instead of by how many keywords
matched. Blue and found green blocks are taken together in that rank against the budget, so a constant entry
with a high order is kept before a keyword entry with a low one (a `no_budget` entry is never cut).
"""

from __future__ import annotations

import random
import re
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache
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
LOGICS = ("and_any", "and_all", "not_any", "not_all")


@dataclass
class FoundBlock:
    block: dict[str, Any]
    path: str
    # always | keyword | meaning | sticky (for the group's root) | group (pulled along) | link | chain
    reason: str


@dataclass
class Selection:
    found: list[FoundBlock]
    fresh_hits: list[str] = field(default_factory=list)  # green blocks found now (refresh "stays")
    misses: list[str] = field(default_factory=list)  # green blocks found now whose chance failed
    dropped: list[str] = field(default_factory=list)  # titles that did not fit the budget


DEFAULT_ORDER = 100  # SillyTavern's default entry order


def _st_entry(block: dict[str, Any]) -> dict[str, Any]:
    extra = block.get("extra") if isinstance(block.get("extra"), dict) else {}
    entry = extra.get("st_entry")
    return entry if isinstance(entry, dict) else {}


def block_order(block: dict[str, Any]) -> int:
    """SillyTavern's «Order» of an entry: the rule `order`, else what the imported ST entry said (world files:
    `order`, card books: `insertion_order`), else 100."""
    own = (block.get("rules") or {}).get("order")
    if isinstance(own, int) and not isinstance(own, bool):
        return own
    entry = _st_entry(block)
    for key in ("order", "insertion_order"):
        value = entry.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return DEFAULT_ORDER


def ignores_budget(block: dict[str, Any]) -> bool:
    """SillyTavern's «ignore budget»: the rule `no_budget`, or the imported entry's `ignoreBudget`."""
    return bool((block.get("rules") or {}).get("no_budget") or _st_entry(block).get("ignoreBudget") is True)


def from_st(block: dict[str, Any]) -> bool:
    """The block came from a SillyTavern file (a world file or a card's book): its text goes to the model bare."""
    return bool(_st_entry(block))


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    word = word.lower()
    endings = RU_ENDINGS if CYRILLIC.search(word) else EN_ENDINGS
    for ending in endings:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


@lru_cache(maxsize=50_000)
def keyword_pattern(keyword: str, *, whole: bool = False, case: bool = False) -> re.Pattern[str] | None:
    """A keyword as a pattern: words by stem (any ending), /regex/ as written (ST style).
    `whole`: the words exactly, no other endings; `case`: letter case matters."""
    keyword = keyword.strip()
    if not keyword:
        return None
    flags = 0 if case else re.I
    if len(keyword) > 2 and keyword.startswith("/") and keyword.rstrip("gimsuy").endswith("/"):
        body = keyword[1:keyword.rstrip("gimsuy").rfind("/")]
        try:
            return re.compile(body, flags)
        except re.error:
            return None
    words = re.findall(r"\w+", keyword)
    if not words:
        return None
    if whole:
        return re.compile(r"(?<!\w)" + r"\W+".join(re.escape(w) for w in words) + r"(?!\w)", flags)
    # The stem keeps the word's own letters (and their case) up to where the ending starts.
    parts = [re.escape(w[: len(stem(w))]) + r"\w*" for w in words]
    return re.compile(r"(?<!\w)" + r"\W+".join(parts), flags)


class _Words:
    """The words of a text, sorted, so a one-word keyword is counted by binary search instead of a
    regex pass over the whole text (thousands of lorebook entries x several keywords each)."""

    def __init__(self, text: str) -> None:
        tokens = re.findall(r"\w+", text)
        self.exact = sorted(tokens)
        self.lower = sorted(t.lower() for t in tokens)

    @staticmethod
    def _between(words: list[str], low: str, high: str) -> int:
        return bisect_left(words, high) - bisect_left(words, low)

    def count(self, word: str, *, whole: bool, case: bool) -> int:
        words = self.exact if case else self.lower
        word = word if case else word.lower()
        if whole:
            return self._between(words, word, word + "\0")
        start = word[: len(stem(word))]  # any ending, as keyword_pattern does
        return self._between(words, start, start + "\U0010ffff")


@lru_cache(maxsize=64)
def _words(text: str) -> _Words:
    return _Words(text)


SIMPLE_WORD = re.compile(r"\w+")


def _occurrences(word: str, text: str, *, whole: bool, case: bool) -> int:
    word = word.strip()
    if SIMPLE_WORD.fullmatch(word):
        return _words(text).count(word, whole=whole, case=case)
    is_regex = len(word) > 2 and word.startswith("/") and word.rstrip("gimsuy").endswith("/")
    if not is_regex and any(not _words(text).count(part, whole=whole, case=case)
                            for part in SIMPLE_WORD.findall(word)):
        return 0  # a phrase with a word the text does not have: no need to run the pattern
    pattern = keyword_pattern(word, whole=whole, case=case)  # phrases and /regex/
    return len(pattern.findall(text)) if pattern is not None else 0


def keyword_hits(block: dict[str, Any], text: str) -> int:
    """How many times the block's keywords occur in `text`, after its second-keyword logic."""
    rules = block.get("rules") or {}
    whole, case = bool(rules.get("whole")), bool(rules.get("case"))
    words = ([block["title"]] if rules.get("title_key", True) else []) + list(block.get("keywords") or [])
    count = 0
    for word in words:
        count += _occurrences(word, text, whole=whole, case=case)
    second = [w for w in rules.get("keys2") or [] if w.strip()]
    if not count or not second:
        return count
    seen = [_occurrences(word, text, whole=whole, case=case) > 0 for word in second]
    logic = rules.get("logic", "and_any")
    ok = {"and_any": any(seen), "and_all": all(seen), "not_any": not any(seen),
          "not_all": not all(seen)}.get(logic, any(seen))
    return count if ok else 0


def _tree(blocks: list[dict[str, Any]], owner_rank: dict[str, int] | None = None
          ) -> tuple[dict[str, dict], dict[str | None, list[dict]]]:
    by_id = {b["id"]: b for b in blocks}
    rank = owner_rank or {}
    children: dict[str | None, list[dict]] = {}
    for block in blocks:
        parent = block["parent_id"] if block["parent_id"] in by_id else None
        children.setdefault(parent, []).append(block)
    for items in children.values():
        items.sort(key=lambda b: (rank.get(b.get("owner_id"), 0), b["position"], b.get("created_at", "")))
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
    scan_messages: list[str] | None = None,
    recursion: int = 0,
    seed_text: str = "",
    present: set[str] | None = None,
    owner_names: dict[str, str] | None = None,
    by_order: bool = False,
) -> Selection:
    """`hits`: block id -> generation it was last found at; `missed`: those of them whose chance
    failed then. `links`: pairs of linked block ids. `rng`: random.Random-like, for tests.
    `scan_messages`: the visible messages, newest last (for a block's own `scan`); `present`:
    character ids taking part (for `filter`; None = no filter); `owner_names`: owner id -> name for
    a pool of several owners (in their order); `recursion`, `seed_text`, `by_order`: see above."""
    hits = hits or {}
    meaning = meaning or {}
    missed = missed or set()
    rng = rng or random
    owner_names = owner_names or {}
    by_id, children = _tree(blocks, {owner: i for i, owner in enumerate(owner_names)})

    order: list[str] = []  # tree order of usable blocks (not off, not under off)
    paths: dict[str, str] = {}
    layered: set[str] = set()  # blocks under a green one: they go only when it is found

    def walk(parent: str | None, prefix: str | None, under_green: bool) -> None:
        for block in children.get(parent, []):
            if block["mode"] == "off" or block.get("detached"):
                continue
            start = prefix if prefix is not None else owner_names.get(block.get("owner_id"), owner_name)
            path = f"{start}{PATH_SEPARATOR}{block['title']}"
            paths[block["id"]] = path
            order.append(block["id"])
            if under_green:
                layered.add(block["id"])
            walk(block["id"], path, under_green or block["mode"] == "search")

    walk(None, None, False)
    rank = {block_id: i for i, block_id in enumerate(order)}
    owner_rank = {owner: i for i, owner in enumerate(owner_names)}
    # Lorebooks: book, then SillyTavern's order (highest first), then the place in the tree.
    prio = {b: (owner_rank.get(by_id[b].get("owner_id"), 0), -block_order(by_id[b]), rank[b]) for b in order} \
        if by_order else {}

    def rules(block_id: str) -> dict[str, Any]:
        return by_id[block_id].get("rules") or {}

    def subtree(block_id: str) -> list[str]:
        """The block and its blue sub-bubbles, down while they stay blue: green ones wait for their
        own words (layers, stage 10в)."""
        result = [block_id]
        for child in children.get(block_id, []):
            if child["id"] in paths and child["mode"] == "always":
                result.extend(subtree(child["id"]))
        return result

    def held(block_id: str) -> bool:
        return block_id in hits and generation - hits[block_id] <= by_id[block_id].get("sticky", 3)

    def allowed(block_id: str) -> bool:
        """Delay and the character filter: may this block go at all right now?"""
        rule = rules(block_id)
        if generation < int(rule.get("delay") or 0):
            return False
        wanted = rule.get("filter") or {}
        ids = set(wanted.get("ids") or [])
        if present is not None and ids:
            if wanted.get("mode") == "except" and ids & present:
                return False
            if wanted.get("mode", "only") == "only" and not ids & present:
                return False
        return True

    def cooling(block_id: str) -> bool:
        cooldown = int(rules(block_id).get("cooldown") or 0)
        if not cooldown or block_id not in hits:
            return False
        since = generation - hits[block_id]
        sticky = by_id[block_id].get("sticky", 3)
        return sticky < since <= sticky + cooldown

    texts: dict[int, str] = {}

    def text_for(block_id: str) -> str:
        own = rules(block_id).get("scan")
        if scan_messages is None or not isinstance(own, int) or own <= 0:
            return scan_text or ""
        if own not in texts:
            texts[own] = "\n".join(scan_messages[-own:])
        return texts[own]

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
    used = 0
    dropped: list[str] = []
    fresh: list[str] = []
    groups_taken: set[str] = set()

    def take(block_id: str, reason: str) -> bool:
        nonlocal used
        group = [b for b in subtree(block_id) if b not in chosen]
        cost = sum(estimate_tokens(by_id[b]["content"]) for b in group if not ignores_budget(by_id[b]))
        if budget_tokens and used + cost > budget_tokens:
            dropped.append(by_id[block_id]["title"])
            return False
        if reason not in ("sticky", "always") and by_id[block_id]["mode"] == "search":
            if not lucky(block_id):
                return False
            fresh.append(block_id)
        used += cost
        chosen[block_id] = reason
        for member in group[1:]:
            chosen.setdefault(member, "group")
        groups_taken.update(rules(block_id).get("groups") or [])
        return True

    constants = [b for b in order if by_id[b]["mode"] == "always" and b not in layered
                 and (generation - 1) % (by_id[b].get("every", 0) + 1) == 0 and allowed(b)]
    if not by_order:
        for block_id in constants:
            chosen[block_id] = "always"

    def one_per_group(found: list[tuple[float, str, str]]) -> list[tuple[float, str, str]]:
        """Of found blocks sharing a group only one stays (see `groups` above)."""
        result = list(found)
        names = sorted({g for _, b, _ in found for g in rules(b).get("groups") or []})
        for name in names:
            members = [item for item in result if name in (rules(item[1]).get("groups") or [])]
            if not members:
                continue
            if name in groups_taken:
                winner = None
            else:
                winner = next((m for m in members if held(m[1])), None) \
                    or next((m for m in members if rules(m[1]).get("main")), None)
                if winner is None:
                    weights = [max(0, int(rules(m[1]).get("weight", 100) or 0)) for m in members]
                    if sum(weights) <= 0:
                        weights = [1] * len(members)
                    point, winner = rng.random() * sum(weights), members[-1]
                    for member, weight in zip(members, weights):
                        if point < weight:
                            winner = member
                            break
                        point -= weight
            result = [item for item in result if item not in members or item is winner]
        return result

    def candidates_in(text_of: Any, first: bool, exclude: set[str]) -> list[tuple[float, str, str]]:
        """Green blocks found in the text: (rank, id, reason), best first. `first`: the chat scan
        (meaning and "stays" count too); otherwise a chain step."""
        found: list[tuple[float, str, str]] = []
        for block_id in order:
            block = by_id[block_id]
            if block["mode"] != "search" or block_id in chosen or block_id in exclude:
                continue
            if not allowed(block_id) or (held(block_id) and block_id in missed):
                continue  # a chance failed a moment ago: out until the hold is over
            count = keyword_hits(block, text_of(block_id))
            score = meaning.get(block_id, 0.0) if first else 0.0
            if count and not cooling(block_id):
                found.append((0 - count, block_id, "keyword" if first else "chain"))
            elif first and score >= threshold and not cooling(block_id):
                found.append((1 - score, block_id, "meaning"))
            elif first and held(block_id):
                found.append((2.0, block_id, "sticky"))
        if by_order:
            found = [(prio[b], b, reason) for _, b, reason in found]
        return one_per_group(sorted(found))

    if by_order:
        # Constant and found entries together, by rank: a constant does not jump the queue (SillyTavern), but a
        # group a constant belongs to is still taken by it.
        held_groups = set(groups_taken)
        groups_taken.update(g for b in constants for g in rules(b).get("groups") or [])
        first = candidates_in(text_for, True, set())
        groups_taken.clear()
        groups_taken.update(held_groups)
        for _, block_id, reason in sorted([(prio[b], b, "always") for b in constants] + first):
            if block_id not in chosen:
                take(block_id, reason)
    else:
        for _, block_id, reason in candidates_in(text_for, True, set()):
            if block_id not in chosen:
                take(block_id, reason)

    # Chains: what is going now is read for more topics, step by step.
    woken = {b for b in order if rules(b).get("not_woken")}
    source = [b for b in chosen if not rules(b).get("no_wake")]
    extra = seed_text
    for _ in range(max(0, recursion)):
        text = "\n".join([extra, *(by_id[b]["content"] for b in source)]).strip()
        extra = ""
        if not text:
            break
        before = set(chosen)
        for _, block_id, reason in candidates_in(lambda _b: text, False, woken):
            if block_id not in chosen:
                take(block_id, reason)
        source = [b for b in chosen if b not in before and not rules(b).get("no_wake")]
        if not source:
            break

    # Links: one step from what is going, to the linked bubble and its sub-bubbles only.
    partners: dict[str, list[str]] = {}
    for a, b in links or []:
        partners.setdefault(a, []).append(b)
        partners.setdefault(b, []).append(a)
    for block_id in list(chosen):
        for other in partners.get(block_id, []):
            if other in paths and other not in chosen and not (held(other) and other in missed) \
                    and allowed(other):
                take(other, "link")

    found = [FoundBlock(by_id[b], paths[b], chosen[b]) for b in order
             if b in chosen and by_id[b]["content"].strip()]
    return Selection(found=found, fresh_hits=[b for b in fresh if b in chosen], dropped=dropped, misses=misses)


def cosine(a: Any, b: Any) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0
