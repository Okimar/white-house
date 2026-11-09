"""Memory++ M1: scoring pieces with the reply's query vectors and keywords, and its speed (ADR risk R1)."""

from __future__ import annotations

import importlib
import random
import time
from array import array

search = importlib.import_module("tavern.builtin_plugins.memory.search")
pieces = importlib.import_module("tavern.builtin_plugins.memory.pieces")


def cand(piece_id, index, words, *, hash_="h"):
    return search.Candidate(piece_id, f"m{index}", index, hash_ or "h", 10, tuple(pieces.keywords(words)))


def unit(*xs):
    return search.unit(list(xs))


def test_a_vector_alone_finds_a_piece_and_an_unrelated_one_is_not_offered():
    near, far = cand("near", 0, "ничего общего"), cand("far", 1, "совсем другое")
    vectors = {"near": ("h", unit(1, 0.1)), "far": ("h", unit(0, 1))}
    scored = search.score([near, far], vectors, [unit(1, 0)], set(), threshold=0.55)
    assert [s.candidate.piece_id for s in scored] == ["near"] and scored[0].vec > 0.9


def test_keywords_alone_find_a_name_or_a_number():
    names = cand("names", 0, "Вчера Мария купила яблоки у Ивана за 15 монет.")
    other = cand("other", 1, "Дождь шёл всю ночь над городом.")
    stems = search.query_stems(["а сколько заплатила мария"], [])
    scored = search.score([names, other], {}, [], stems, threshold=0.55)
    assert [s.candidate.piece_id for s in scored] == ["names"] and scored[0].vec == 0 and scored[0].kw > 0.3
    numbered = search.score([names, other], {}, [], search.query_stems(["15"], []), threshold=0.55)
    assert numbered and numbered[0].candidate.piece_id == "names"


def test_a_name_counts_double():
    plain = cand("plain", 0, "яблоки лежали рядом с кузнецом")
    named = cand("named", 1, "Вчера Яблоко упало, кузнец ушёл")
    filler = [cand(f"f{i}", 2 + i, "ничего особенного") for i in range(6)]
    stems = search.query_stems(["яблоко"], [])
    idf = search.idf_table([plain, named, *filler])
    assert search.keyword_part(named, stems, idf) > search.keyword_part(plain, stems, idf)


def test_both_parts_together_rank_higher_than_either():
    both = cand("both", 0, "Мария яблоки")
    only_vec = cand("vec", 1, "ничего")
    vectors = {"both": ("h", unit(1, 0)), "vec": ("h", unit(1, 0))}
    scored = search.score([both, only_vec], vectors, [unit(1, 0)], search.query_stems(["мария"], []), 0.55)
    assert scored[0].candidate.piece_id == "both" and scored[0].score > scored[1].score


def test_a_stale_vector_is_not_scored_but_keywords_still_work():
    piece = cand("p", 0, "Мария яблоки", hash_="new")
    filler = [cand(f"f{i}", 1 + i, f"слово{i} дело{i}") for i in range(8)]
    vectors = {"p": ("old", unit(1, 0))}  # made from older text
    scored = search.score([piece, *filler], vectors, [unit(1, 0)], search.query_stems(["мария"], []), 0.55)
    assert scored and scored[0].candidate.piece_id == "p" and scored[0].vec == 0.0 and scored[0].kw > 0


def test_a_vector_of_another_size_is_ignored():
    piece = cand("p", 0, "ничего")
    assert search.score([piece], {"p": ("h", unit(1, 0, 0))}, [unit(1, 0)], set(), 0.55) == []


def test_a_word_in_every_piece_weighs_almost_nothing():
    pieces_ = [cand(f"p{i}", i, "таверна стол") for i in range(10)]
    stems = search.query_stems(["таверна"], [])
    assert search.score(pieces_, {}, [], stems, 0.55) == []


def test_many_query_vectors_are_averaged_when_the_work_would_pass_the_cap(monkeypatch):
    queries = [unit(1, 0), unit(0, 1)]
    assert len(search.bounded_queries(queries, 10, 2)) == 2
    monkeypatch.setattr(search, "MAX_OPS", 10)
    [mean] = search.bounded_queries(queries, 10, 2)
    assert abs(mean[0] - mean[1]) < 1e-6


def test_scoring_5000_pieces_stays_fast():
    """ADR risk R1: the budget is 300 ms on the tablet. Here, on a desktop, a loose bound with real sizes:
    5000 pieces, 512 numbers, 3 query vectors."""
    rng = random.Random(1)
    size, count = 512, 5000
    vectors, candidates = {}, []
    for i in range(count):
        vectors[f"p{i}"] = ("h", search.unit([rng.gauss(0, 1) for _ in range(size)]))
        candidates.append(search.Candidate(f"p{i}", f"m{i}", i, "h", 50,
                                           tuple(f"w{rng.randrange(400)}" for _ in range(30))))
    queries = [search.unit([rng.gauss(0, 1) for _ in range(size)]) for _ in range(3)]
    stems = {f"w{n}" for n in range(0, 40, 7)}
    started = time.perf_counter()
    search.score(candidates, vectors, queries, stems, 0.55)
    elapsed = time.perf_counter() - started
    print(f"scored {count} pieces x 3 queries x {size} numbers in {elapsed * 1000:.0f} ms")
    assert elapsed < 1.5
    assert isinstance(vectors["p0"][1], array)


def test_too_many_pieces_for_one_query_score_vectors_only_for_keyword_hits_and_the_newest(monkeypatch):
    monkeypatch.setattr(search, "MAX_OPS", 8)  # room for 4 vectors of 2 numbers
    candidates = [cand(f"p{i}", i, "ничего") for i in range(10)]
    candidates[2] = cand("named", 2, "Мария яблоки")
    vectors = {c.piece_id: ("h", unit(1, 0)) for c in candidates}
    scored = search.score(candidates, vectors, [unit(1, 0)], search.query_stems(["мария"], []), 0.55)
    ids = {s.candidate.piece_id for s in scored if s.vec > 0}
    assert ids == {"named", "p9", "p8", "p7"}
