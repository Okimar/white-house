"""Which character blocks go into the prompt (stage 4б rules)."""

from __future__ import annotations

from tavern.core.retrieval import keyword_pattern, select_blocks, stem


def block(id, title, content="", mode="search", parent=None, keywords=(), sticky=3, position=0):
    return {"id": id, "title": title, "content": content or f"{title} text", "mode": mode,
            "parent_id": parent, "keywords": list(keywords), "sticky": sticky, "position": position}


TREE = [
    block("look", "Внешность", "Высокая.", mode="always"),
    block("hair", "Волосы", "Рыжие, до пояса.", parent="look", keywords=["коса", "причёска"], position=0),
    block("eyes", "Глаза", "Зелёные.", parent="look", position=1),
    block("past", "Прошлое", "Сирота.", keywords=["детство"]),
    block("village", "Деревня", "Росла у реки.", parent="past"),
    block("secret", "Тайна", "Ведьма.", mode="off"),
    block("spell", "Заклинание", "Огонь.", parent="secret", mode="always"),
]


def pick(**kw):
    selection = select_blocks(TREE, owner_name="Аня", **kw)
    return {f.block["id"]: f.reason for f in selection.found}, selection


def test_stem_and_patterns_match_russian_forms():
    assert stem("волосы") == "волос" and stem("глаза") == "глаз"
    assert keyword_pattern("волосы").search("Он гладит её волосами")
    assert keyword_pattern("рыжая коса").search("Рыжую косу заплели")
    assert keyword_pattern("/кос[аы]/i").search("КОСЫ")
    assert not keyword_pattern("глаза").search("галактика")


def test_blue_always_green_by_keyword_off_never():
    found, _ = pick(scan_text="Я разглядываю её волосы")
    assert found == {"look": "always", "hair": "keyword"}
    assert "spell" not in found  # under an off block


def test_keyword_hit_brings_the_group_and_a_path():
    found, selection = pick(scan_text="Расскажи про детство")
    assert found["past"] == "keyword" and found["village"] == "group"
    village = next(f for f in selection.found if f.block["id"] == "village")
    assert village.path == "Аня › Прошлое › Деревня"
    assert selection.fresh_hits == ["past"]


def test_meaning_and_sticky():
    found, _ = pick(scan_text="ничего", meaning={"eyes": 0.8, "hair": 0.3})
    assert found.get("eyes") == "meaning" and "hair" not in found
    found, selection = pick(scan_text="ничего", hits={"hair": 5}, generation=8)
    assert found.get("hair") == "sticky" and selection.fresh_hits == []
    found, _ = pick(scan_text="ничего", hits={"hair": 5}, generation=9)
    assert "hair" not in found


def test_budget_keeps_the_strongest_hits():
    big = [block("a", "Волосы", "слово " * 400), block("b", "Глаза", "коротко")]
    selection = select_blocks(big, owner_name="Аня", scan_text="глаза глаза и волосы", budget_tokens=50)
    assert [f.block["id"] for f in selection.found] == ["b"]
    assert selection.dropped == ["Волосы"]


# --- after the owner's review: free bubbles, chance, "через N", links -----------------------------


class Dice:
    """random.Random stand-in: hands out the given numbers in [0, 1) one by one."""

    def __init__(self, *values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)


def test_a_free_bubble_and_everything_under_it_is_not_sent():
    tree = [block("free", "Шрам", "На щеке.", mode="always"), block("under", "Ожог", parent="free", mode="always")]
    tree[0]["detached"] = 1
    found = select_blocks(tree, owner_name="Аня", scan_text="шрам ожог").found
    assert found == []


def test_blue_bubble_skips_replies_between_sendings():
    tree = [block("look", "Внешность", "Высокая.", mode="always")]
    tree[0]["every"] = 2  # through two replies: generations 1, 4, 7
    sent = [g for g in range(1, 8) if select_blocks(tree, owner_name="Аня", scan_text="", generation=g).found]
    assert sent == [1, 4, 7]


def test_failed_chance_holds_for_the_sticky_replies_and_then_rolls_again():
    tree = [block("hair", "Волосы", "Рыжие.", keywords=["коса"], sticky=2)]
    tree[0]["chance"] = 50
    miss = select_blocks(tree, owner_name="Аня", scan_text="коса", generation=1, rng=Dice(0.9))
    assert miss.found == [] and miss.misses == ["hair"] and miss.fresh_hits == []
    # regenerating within the hold cannot pull it in, even with a lucky roll
    held = select_blocks(tree, owner_name="Аня", scan_text="коса", generation=2, hits={"hair": 1},
                         missed={"hair"}, rng=Dice(0.0))
    assert held.found == [] and held.misses == []
    later = select_blocks(tree, owner_name="Аня", scan_text="коса", generation=4, hits={"hair": 1},
                          missed={"hair"}, rng=Dice(0.1))
    assert [f.block["id"] for f in later.found] == ["hair"] and later.fresh_hits == ["hair"]


def test_chance_with_zero_sticky_rolls_every_time():
    tree = [block("hair", "Волосы", "Рыжие.", keywords=["коса"], sticky=0)]
    tree[0]["chance"] = 50
    again = select_blocks(tree, owner_name="Аня", scan_text="коса", generation=2, hits={"hair": 1},
                          missed={"hair"}, rng=Dice(0.2))
    assert [f.block["id"] for f in again.found] == ["hair"]


def test_a_link_pulls_only_the_linked_bubble_or_its_branch():
    tree = [
        block("hands", "Руки", "Тонкие.", keywords=["руки"]),
        block("fingers1", "Пальцы", "Кольцо на безымянном.", parent="hands", keywords=["кольцо"]),
        block("legs", "Ноги", "Длинные."),
        block("fingers2", "Пальцы", "Короткие пальцы ног.", parent="legs"),
        block("nail", "Ногти", "Красный лак.", parent="fingers2"),
    ]
    selection = select_blocks(tree, owner_name="Аня", scan_text="надеваю кольцо",
                              links=[("fingers1", "fingers2")])
    found = {f.block["id"]: f.reason for f in selection.found}
    assert found == {"fingers1": "keyword", "fingers2": "link", "nail": "group"}  # not all the legs
    assert next(f.path for f in selection.found if f.block["id"] == "fingers2") == "Аня › Ноги › Пальцы"
