"""Which character blocks go into the prompt (stage 4б rules)."""

from __future__ import annotations

from tavern.core.prompt import estimate_tokens
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


# --- stage 5: finer rules, chains, lorebook pools ---------------------------------------------


def test_fast_word_count_agrees_with_the_patterns():
    from tavern.core.retrieval import _occurrences

    text = "Кот и котята. КОТ спит; Rats and rat, a Rat-king. Рыжую косу заплели, рыжая коса! Кошка_1"
    words = ["кот", "Кот", "котята", "rat", "Rat", "rats", "коса", "рыжая коса", "Рыжую косу", "кошка_1", "пёс",
             "/ко[тш]/"]
    for word in words:
        for whole in (False, True):
            for case in (False, True):
                expected = len(keyword_pattern(word, whole=whole, case=case).findall(text))
                assert _occurrences(word, text, whole=whole, case=case) == expected, (word, whole, case)


def ruled(id, title, content="", rules=None, **kw):
    b = block(id, title, content, **kw)
    b["rules"] = rules or {}
    return b


def ids(selection):
    return {f.block["id"]: f.reason for f in selection.found}


def test_second_keywords_with_and_or_not():
    tree = [ruled("a", "Замок", "Старый.", rules={"keys2": ["ночь", "луна"], "logic": "and_any"}),
            ruled("b", "Башня", "Высокая.", rules={"keys2": ["ночь", "луна"], "logic": "and_all"}),
            ruled("c", "Ров", "Глубокий.", rules={"keys2": ["зима"], "logic": "not_any"}),
            ruled("d", "Мост", "Узкий.", rules={"keys2": ["ночь", "луна"], "logic": "not_all"})]
    found = ids(select_blocks(tree, owner_name="Мир", scan_text="замок башня ров мост ночью"))
    assert found == {"a": "keyword", "c": "keyword", "d": "keyword"}  # башня needs the moon too
    found = ids(select_blocks(tree, owner_name="Мир", scan_text="замок башня ров мост ночь луна зима"))
    assert found == {"a": "keyword", "b": "keyword"}


def test_whole_words_case_and_title_switch():
    tree = [ruled("a", "Кот", "Рыжий.", rules={"whole": True}),
            ruled("b", "Rat", "A gang.", rules={"case": True}),
            ruled("c", "Записка про гильдию", "Текст.", keywords=["гильдия"], rules={"title_key": False})]
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="котята rat записка")) == {}
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="кот Rats гильдии")) == \
        {"a": "keyword", "b": "keyword", "c": "keyword"}


def test_own_scan_depth_delay_and_cooldown():
    old, new = "Говорили о драконе.", "А теперь о погоде."
    tree = [ruled("d", "Дракон", "Красный.", rules={"scan": 2}), ruled("e", "Эльф", "Лучник.")]
    selection = select_blocks(tree, owner_name="Мир", scan_text=new, scan_messages=[old, new])
    assert ids(selection) == {"d": "keyword"}  # it looks two messages back, the rest only one
    late = [ruled("x", "Пещера", "Тёмная.", rules={"delay": 3}, mode="always")]
    assert ids(select_blocks(late, owner_name="Мир", scan_text="", generation=2)) == {}
    assert ids(select_blocks(late, owner_name="Мир", scan_text="", generation=3)) == {"x": "always"}
    cool = [ruled("c", "Ворон", "Чёрный.", rules={"cooldown": 2}, sticky=1)]
    # found at 1, held at 2, resting at 3 and 4, found again at 5
    assert ids(select_blocks(cool, owner_name="Мир", scan_text="ворон", generation=3, hits={"c": 1})) == {}
    assert ids(select_blocks(cool, owner_name="Мир", scan_text="ворон", generation=5, hits={"c": 1})) == {"c": "keyword"}


def test_one_per_group_held_main_or_weighted():
    tree = [ruled("a", "Погода", "Дождь.", rules={"groups": ["weather"], "weight": 1}),
            ruled("b", "Небо", "Солнце.", rules={"groups": ["weather"], "weight": 3})]
    text = "погода небо"
    assert ids(select_blocks(tree, owner_name="Мир", scan_text=text, rng=Dice(0.1))) == {"a": "keyword"}
    assert ids(select_blocks(tree, owner_name="Мир", scan_text=text, rng=Dice(0.5))) == {"b": "keyword"}
    tree[1]["rules"]["main"] = True
    assert ids(select_blocks(tree, owner_name="Мир", scan_text=text, rng=Dice(0.1))) == {"b": "keyword"}
    held = select_blocks(tree, owner_name="Мир", scan_text=text, generation=2, hits={"a": 1}, rng=Dice(0.9))
    assert ids(held) == {"a": "keyword"}  # still held: it keeps its place


def test_filter_by_who_takes_part():
    tree = [ruled("a", "Тайна Ани", "Ведьма.", rules={"filter": {"mode": "only", "ids": ["ann"]}}, mode="always"),
            ruled("b", "Для чужих", "Слухи.", rules={"filter": {"mode": "except", "ids": ["ann"]}}, mode="always")]
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="", present={"ann", "bob"})) == {"a": "always"}
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="", present={"bob"})) == {"b": "always"}


def test_chains_wake_other_entries_step_by_step():
    tree = [ruled("guild", "Гильдия воров", "Главарь по прозвищу Крыса.", keywords=["гильдия"]),
            ruled("rat", "Крыса", "Хромой старик, прячется в порту."),
            ruled("port", "Порт", "Туманный."),
            ruled("shy", "Туман", "Густой.", rules={"not_woken": True})]
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="гильдия", recursion=0)) == {"guild": "keyword"}
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="гильдия", recursion=1)) == \
        {"guild": "keyword", "rat": "chain"}
    deep = ids(select_blocks(tree, owner_name="Мир", scan_text="гильдия", recursion=5))
    assert deep == {"guild": "keyword", "rat": "chain", "port": "chain"}  # "Туман" is never woken
    tree[0]["rules"] = {"no_wake": True}
    assert ids(select_blocks(tree, owner_name="Мир", scan_text="гильдия", recursion=5)) == {"guild": "keyword"}
    seeded = select_blocks(tree, owner_name="Мир", scan_text="", recursion=1, seed_text="видели Крысу")
    assert ids(seeded) == {"rat": "chain"}  # text from outside the pool (the characters' bubbles)


def test_a_pool_of_books_goes_by_order_and_blue_ones_share_the_budget():
    def entry(id, owner, title, size, position, mode="search"):
        b = block(id, title, "слово " * size, mode=mode, position=position)
        b["owner_id"] = owner
        return b

    pool = [entry("w2", "world", "Река", 40, 1), entry("w1", "world", "Город", 40, 0, mode="always"),
            entry("t1", "town", "Рынок", 40, 0), entry("t2", "town", "Мост", 40, 1)]
    one = estimate_tokens("слово " * 40)
    selection = select_blocks(pool, owner_name="", scan_text="река рынок мост", by_order=True,
                              budget_tokens=3 * one + 1, owner_names={"world": "Мир", "town": "Город N"})
    assert list(ids(selection)) == ["w1", "w2", "t1"]  # the last one by number is left out
    assert selection.dropped == ["Мост"]
    assert next(f.path for f in selection.found if f.block["id"] == "t1") == "Город N › Рынок"
