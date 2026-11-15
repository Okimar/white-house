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
    match       [str]  more texts this block looks in besides the messages (SillyTavern's per-entry scan sources):
                       persona, description, personality, depth_prompt, scenario, creator_notes (the caller's
                       `sources`)
    after_chain int    SillyTavern's «delay until recursion»: found only in a chain step, from level N on (1 = the
                       first level); it reads the messages and all the chain text so far
    scoring     bool   group scoring for this block (absent = the caller's `group_scoring`): of a group only the
                       blocks with the most different keywords found stay before the usual group rules
    triggers    [str]  only for these kinds of generation (normal, continue, impersonate, swipe, regenerate,
                       quiet); empty = always. Checked only when the caller says the kind

Decorators (SillyTavern): a text starting with the lines `@@activate` / `@@dont_activate` goes always / never; the
lines are cut from the text that is sent.

Chains (`recursion` > 0): the text of blocks that are going (plus `seed_text`) is looked through
for more green blocks, up to `recursion` steps. When a step finds nothing new and some blocks wait for a higher
`after_chain` level, the next level opens (still within the steps).

Lorebooks (`by_order`), the way SillyTavern does it: the pool's blocks are ranked by tier (`tiers`: chat, persona,
characters / global by strategy; default the book's place in the pool), then by `block_order` highest first, then
by their place among neighbours, instead of by how many keywords matched. Blue and found green blocks are taken
together in that rank against the budget, so a constant entry with a high order is kept before a keyword entry
with a low one; once one entry does not fit, every later one is dropped too (a `no_budget` entry is never cut).
The found list then goes lowest order first (SillyTavern's text order). `min_found`: while fewer blocks go, the scan
reaches one older message more (up to `min_depth_max`, 0 = the whole chat).

Timers (`sticky`, `cooldown`, `delay`) count replies (`generation`); with `clock` (the number of messages in the
chat) blocks from SillyTavern files count them in chat messages instead, as SillyTavern does.
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


DECORATORS = {"@@activate": "activate", "@@dont_activate": "dont_activate"}
SOURCES = ("persona", "description", "personality", "depth_prompt", "scenario", "creator_notes")
TRIGGERS = ("normal", "continue", "impersonate", "swipe", "regenerate", "quiet")


def split_decorators(content: str) -> tuple[set[str], str]:
    """SillyTavern's decorators: `@@activate` / `@@dont_activate` lines at the very start of a text. Returns the
    ones found and the text without those lines (the text as it is when there are none)."""
    if not content.startswith("@@"):
        return set(), content
    lines = content.split("\n")
    found: set[str] = set()
    index = 0
    while index < len(lines) and lines[index].strip() in DECORATORS:
        found.add(DECORATORS[lines[index].strip()])
        index += 1
    return (found, "\n".join(lines[index:])) if found else (set(), content)


def _undecorated(block: dict[str, Any]) -> dict[str, Any]:
    found, text = split_decorators(block.get("content") or "")
    if not found:
        return block
    mode = block["mode"]
    if "dont_activate" in found:
        mode = "off"
    elif mode != "off":
        mode = "always"
    return {**block, "content": text, "mode": mode}


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


def key_score(block: dict[str, Any], text: str) -> int:
    """For group scoring: how many DIFFERENT keywords of the block are in `text` (second keywords count too when
    they must be there: and_any / and_all)."""
    rules = block.get("rules") or {}
    whole, case = bool(rules.get("whole")), bool(rules.get("case"))
    words = ([block["title"]] if rules.get("title_key", True) else []) + list(block.get("keywords") or [])
    score = sum(1 for word in words if _occurrences(word, text, whole=whole, case=case) > 0)
    if rules.get("logic", "and_any") in ("and_any", "and_all"):
        score += sum(1 for word in rules.get("keys2") or []
                     if word.strip() and _occurrences(word, text, whole=whole, case=case) > 0)
    return score


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
    sources: dict[str, str] | None = None,
    kind: str | None = None,
    clock: int | None = None,
    group_scoring: bool = False,
    tiers: dict[str, int] | None = None,
    min_found: int = 0,
    scan_depth: int = 0,
    min_depth_max: int = 0,
) -> Selection:
    """`hits`: block id -> generation (or `clock`) it was last found at; `missed`: those of them whose chance
    failed then. `links`: pairs of linked block ids. `rng`: random.Random-like, for tests.
    `scan_messages`: the visible messages, newest last (for a block's own `scan` and for `min_found`); `present`:
    character ids taking part (for `filter`; None = no filter); `owner_names`: owner id -> name for
    a pool of several owners (in their order); `recursion`, `seed_text`, `by_order`: see above.
    `sources`: texts for the rule `match` (persona, description, …); `kind`: the kind of generation for the rule
    `triggers` (None = not checked); `clock`: the chat's message count for timers of SillyTavern blocks;
    `group_scoring`: the default of the rule `scoring`; `tiers`: owner id -> tier for `by_order`; `min_found`,
    `scan_depth` (how many messages `scan_text` holds), `min_depth_max`: SillyTavern's minimum activations."""
    hits = hits or {}
    meaning = meaning or {}
    missed = missed or set()
    rng = rng or random
    owner_names = owner_names or {}
    sources = {k: v for k, v in (sources or {}).items() if isinstance(v, str) and v.strip()}
    blocks = [_undecorated(b) for b in blocks]
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
    tier = tiers or {}

    def tier_of(block_id: str) -> int:
        owner = by_id[block_id].get("owner_id")
        return tier.get(owner, owner_rank.get(owner, 0)) if tier else owner_rank.get(owner, 0)

    # Lorebooks: tier (or book), then SillyTavern's order (highest first), then the place in the tree.
    prio = {b: (tier_of(b), -block_order(by_id[b]), rank[b]) for b in order} if by_order else {}

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

    def by_messages(block_id: str) -> bool:
        return clock is not None and from_st(by_id[block_id])

    def now(block_id: str) -> int:
        return clock if clock is not None and by_messages(block_id) else generation

    def held(block_id: str) -> bool:
        if block_id not in hits:
            return False
        since = now(block_id) - hits[block_id]
        # Counted in messages, a swipe sees the same count as the reply it replaces: that is the same moment, not
        # one message later, so it rolls and searches afresh.
        if by_messages(block_id) and since <= 0:
            return False
        return since <= by_id[block_id].get("sticky", 3)

    def allowed(block_id: str) -> bool:
        """Delay, the kind of generation and the character filter: may this block go at all right now?"""
        rule = rules(block_id)
        if now(block_id) < int(rule.get("delay") or 0):
            return False
        wanted_kinds = rule.get("triggers") or []
        if kind is not None and wanted_kinds and kind not in wanted_kinds:
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
        since = now(block_id) - hits[block_id]
        sticky = by_id[block_id].get("sticky", 3)
        return sticky < since <= sticky + cooldown

    def chain_level(block_id: str) -> int:
        value = rules(block_id).get("after_chain")
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    texts: dict[tuple[Any, ...], str] = {}

    def text_for(block_id: str, deeper: int = 0) -> str:
        """The text a block looks in: the last messages (its own `scan`, else the caller's, `deeper` messages more
        for `min_found`) and the sources its rule `match` names."""
        rule = rules(block_id)
        own = rule.get("scan") if isinstance(rule.get("scan"), int) and rule.get("scan") > 0 else None
        match = tuple(s for s in rule.get("match") or [] if s in sources)
        key = (own, 0 if own else deeper, match)
        if key not in texts:
            if scan_messages is None or (own is None and not deeper):
                base = scan_text or ""
            else:
                depth = own if own is not None else scan_depth + deeper
                base = "\n".join(scan_messages[-depth:]) if depth > 0 else ""
            texts[key] = "\n".join([base, *(sources[s] for s in match)]) if match else base
        return texts[key]

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
    full = False  # lorebooks: one entry did not fit, so every later one is dropped too (SillyTavern)
    dropped: list[str] = []
    fresh: list[str] = []
    groups_taken: set[str] = set()
    scores: dict[str, int] = {}  # group scoring: different keywords found, per block

    def take(block_id: str, reason: str) -> bool:
        nonlocal used, full
        group = [b for b in subtree(block_id) if b not in chosen]
        cost = sum(estimate_tokens(by_id[b]["content"]) for b in group if not ignores_budget(by_id[b]))
        if budget_tokens and cost and (full or used + cost > budget_tokens):
            dropped.append(by_id[block_id]["title"])
            full = full or by_order
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

    def scoring(block_id: str) -> bool:
        own = rules(block_id).get("scoring")
        return own if isinstance(own, bool) else group_scoring

    def one_per_group(found: list[tuple[Any, str, str]]) -> list[tuple[Any, str, str]]:
        """Of found blocks sharing a group only one stays (see `groups` above)."""
        result = list(found)
        names = sorted({g for _, b, _ in found for g in rules(b).get("groups") or []})
        for name in names:
            members = [item for item in result if name in (rules(item[1]).get("groups") or [])]
            if not members:
                continue
            scored = [m for m in members if scoring(m[1])]
            if scored and name not in groups_taken:
                best = max(scores.get(m[1], 0) for m in scored)
                low = [m for m in scored if scores.get(m[1], 0) < best and not held(m[1])]
                result = [item for item in result if item not in low]
                members = [m for m in members if m not in low]
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

    def candidates_in(text_of: Any, first: bool, exclude: set[str], level: int = 0,
                      reason: str | None = None) -> list[tuple[Any, str, str]]:
        """Green blocks found in the text: (rank, id, reason), best first. `first`: the chat scan
        (meaning and "stays" count too); otherwise a chain step (`level`: the open «after chain» level) or a deeper
        scan (`reason`)."""
        found: list[tuple[Any, str, str]] = []
        for block_id in order:
            block = by_id[block_id]
            if block["mode"] != "search" or block_id in chosen or block_id in exclude:
                continue
            wait = chain_level(block_id)
            if wait and (not level or wait > level):
                continue  # «delay until recursion»: only in a chain step of its level or later
            if not allowed(block_id) or (held(block_id) and block_id in missed):
                continue  # a chance failed a moment ago: out until the hold is over
            text = text_of(block_id)
            count = keyword_hits(block, text)
            if count and rules(block_id).get("groups") and scoring(block_id):
                scores[block_id] = key_score(block, text)
            score = meaning.get(block_id, 0.0) if first else 0.0
            if count and not cooling(block_id):
                found.append((0 - count, block_id, reason or ("keyword" if first else "chain")))
            elif first and score >= threshold and not cooling(block_id):
                found.append((1 - score, block_id, "meaning"))
            elif first and held(block_id):
                found.append((2.0, block_id, "sticky"))
        if by_order:
            found = [(prio[b], b, why) for _, b, why in found]
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
    levels = sorted({chain_level(b) for b in order if chain_level(b) and by_id[b]["mode"] == "search"})

    def chains(source: list[str], seed: str) -> None:
        level, extra, steps = 1, seed, 0
        chain_text: list[str] = []
        while steps < max(0, recursion):
            steps += 1
            text = "\n".join([extra, *(by_id[b]["content"] for b in source)]).strip()
            extra = ""
            if text:
                chain_text.append(text)
            whole = "\n".join(chain_text)
            before = set(chosen)
            if text or any(lv <= level for lv in levels):
                def text_of(block_id: str, text: str = text, whole: str = whole) -> str:
                    # a delayed block reads the messages and the whole chain so far; others the new text only
                    return f"{text_for(block_id)}\n{whole}" if chain_level(block_id) else text
                for _, block_id, reason in candidates_in(text_of, False, woken, level):
                    if block_id not in chosen:
                        take(block_id, reason)
            source = [b for b in chosen if b not in before and not rules(b).get("no_wake")]
            if not source:
                higher = [lv for lv in levels if lv > level]
                if not higher:
                    break
                level = higher[0]

    chains([b for b in chosen if not rules(b).get("no_wake")], seed_text)

    # Minimum activations (SillyTavern): too few went, so the scan reaches one older message more, and again.
    if min_found > 0 and scan_messages and scan_depth > 0:
        limit = len(scan_messages) if not min_depth_max else min(min_depth_max, len(scan_messages))
        deeper = 0
        while len(chosen) < min_found and scan_depth + deeper < limit:
            deeper += 1
            before = set(chosen)
            for _, block_id, reason in candidates_in(lambda b, d=deeper: text_for(b, d), False, set(), 0, "keyword"):
                if block_id not in chosen:
                    take(block_id, reason)
            new = [b for b in chosen if b not in before and not rules(b).get("no_wake")]
            if new:
                chains(new, "")

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

    going = [b for b in order if b in chosen and by_id[b]["content"].strip()]
    if by_order:  # SillyTavern's text order: the lowest order first
        going.sort(key=lambda b: (block_order(by_id[b]), prio[b]))
    found = [FoundBlock(by_id[b], paths[b], chosen[b]) for b in going]
    return Selection(found=found, fresh_hits=[b for b in fresh if b in chosen],
                     dropped=list(dict.fromkeys(dropped)), misses=misses)


def cosine(a: Any, b: Any) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0
