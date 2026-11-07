"""JavaScript regex forms -> Python, and what the replacement does (stage 11, ADR 0017).

Each row is a JavaScript pattern, a text, a replacement and the result JavaScript gives (worked out from the
language rules, not from running a browser). Rows marked as "Python would say otherwise" are the places
where plain `re` differs and the translator spells the JavaScript meaning out."""

from __future__ import annotations

import re

import pytest

from tavern.core.regex import RegexError, build_spec, translate
from tavern.core.regex_worker import apply_rule, find_all


def run(find: str, text: str, replace: str = "", **rule: object) -> str:
    spec, _ = build_spec({"find": find, "replace": replace, **rule}, "Alice", "Bob")
    return apply_rule(re.compile(spec["p"], spec["f"]), spec, text)


CASES = [
    # digits, words, space: JavaScript means ASCII for \d and \w (Python would also take other scripts)
    (r"/\d+/g", "a12b٣4", "#", "a#b٣#"),
    (r"/\w+/g", "héllo wörld", "_", "_é_ _ö_"),
    (r"/\bcat\b/g", "cat concat cat.", "X", "X concat X."),
    (r"/\bслово\b/g", "слово", "X", "слово"),
    (r"/\s/g", "a b c d", "_", "a_b_c_d"),
    (r"/\S+/g", "ab cd", "x", "x x"),
    (r"/[\d-x]+/g", "1-x2", "_", "_"),
    # anchors and the dot
    (r"/^a/gm", "a\na\r\na", "-", "-\n-\r\n-"),
    (r"/a$/", "a\n", "X", "a\n"),            # Python's $ would match before the final newline
    (r"/a$/m", "a\nb", "X", "X\nb"),
    (r"/a.b/", "a\rb", "X", "a\rb"),         # JavaScript's dot stops at \r too
    (r"/a.b/s", "a\nb", "X", "X"),
    (r"/a.b/", "a b", "X", "a b"),
    # groups and replacement forms
    (r"/(?<y>\d{4})-(?<m>\d\d)/", "2024-05", "$<m>/$<y>", "05/2024"),
    (r"/(\w+) (\w+)/", "hello world", "$2 $1", "world hello"),
    (r"/o/g", "foo", "$&$&", "foooo"),
    (r"/o/", "foo", "0", "f0o"),
    (r"/x/g", "axb", "$$", "a$b"),
    (r"/b/", "abc", "[$`|$']", "a[a|c]c"),
    (r"/b/", "abc", "<{{match}}>", "a<b>c"),
    (r"/b/", "abc", "<{{MATCH}}>", "a<b>c"),
    (r"/(a)(b)/", "ab", "$3", "$3"),           # no such group: stays as written
    (r"/(a)/", "a", "$10", "a0"),              # $10 with one group is $1 and a zero
    (r"/(a)(b)/", "ab", "$0|$&", "ab|ab"),
    (r"/(?<n>a)/", "a", "$<zz>.", "."),        # an unknown name gives nothing
    (r"/a/", "a", "$<n>", "$<n>"),             # no named groups at all: literal
    (r"/(a)|b/g", "ab", "[$1]", "[a][]"),      # a group that took no part is empty
    (r"/x/", "x", "{{char}} & {{user}}", "Alice & Bob"),
    # plain patterns, flags
    ("foo", "foo foo", "bar", "bar foo"),
    (r"/ПРИВЕТ/i", "привет", "X", "X"),
    (r"/a{2}/", "aaa", "X", "Xa"),
    (r"/a{,2}/", "a{,2}", "X", "X"),           # not a quantifier in JavaScript: plain text
    (r"/a|/g", "ab", "-", "--b-"),
    (r"/x*/g", "abxd", "-", "-a-b--d-"),
    (r"/a/gy", "aab", "X", "XXb"),
    (r"/b/y", "ab", "X", "ab"),
    (r"/<.+?>/g", "<a><b>", "", ""),
    # classes
    (r"/[^]/g", "ab", "x", "xx"),
    (r"/[]/g", "ab", "x", "ab"),
    (r"/[\s\S]+/", "a\nb", "X", "X"),
    (r"/[a-z-]+/g", "ab-c d", "X", "X X"),
    (r"/[[]/g", "a[b", "X", "aXb"),
    (r"/[\]]/g", "a]b", "X", "aXb"),
    (r"/[^\d\s]+/g", "ab 12 cd", "X", "X 12 X"),
    (r"/[\W]/g", "a-b_c", "X", "aXb_c"),
    (r"/\//g", "a/b", "|", "a|b"),
    # lookarounds, backreferences
    (r"/(?<=a)b/", "ab", "X", "aX"),
    (r"/a(?!b)/g", "ab ac", "X", "ab Xc"),
    (r"/(a)\1/", "aa", "X", "X"),
    (r"/(a)?b\1/", "b", "X", "X"),             # JavaScript: a group with no match matches empty
    (r"/(?<q>['\"]).*?\k<q>/g", "\"a\" 'b'", "Q", "Q Q"),
    # escapes
    (r"/A\x42\cA?/", "AB", "X", "X"),
    (r"/\u{1F600}/u", "x\U0001f600y", "X", "xXy"),
    (r"/😀/", "\U0001f600", "X", "X"),
    (r"/\t\n\r/", "\t\n\r", "X", "X"),
    (r"/\-\_\!/", "-_!", "X", "X"),
    (r"/\0/", "a\x00b", "X", "aXb"),
    # trim: strings cut out of what is put in
    (r"/\[(.+?)\]/g", "[a**b] [c]", "<$1>", "<a**b> <c>"),
]


@pytest.mark.parametrize(("find", "text", "replace", "expected"), CASES)
def test_js_forms(find: str, text: str, replace: str, expected: str) -> None:
    assert run(find, text, replace) == expected


def test_trim_strings_are_cut_from_inserted_text() -> None:
    assert run(r"/\[(.+?)\]/g", "[a**b] [c*]", "<$1>", trim=["*"]) == "<ab> <c>"
    assert run(r"/x/", "x", "[{{match}}]", trim=["x"]) == "[]"


def test_macros_in_the_pattern() -> None:
    assert run(r"/{{char}}:/", "Alice: hi", "", macros="raw") == " hi"
    assert run(r"/{{char}}:/", "Alice: hi", "", macros="none") == "Alice: hi"
    # escaped: a name with regex characters is searched as plain text
    spec, _ = build_spec({"find": r"/{{char}}/", "macros": "escaped"}, "A.B (x)", "Bob")
    assert re.search(spec["p"], "A.B (x)") and not re.search(spec["p"], "AxB (x)")
    spec, _ = build_spec({"find": r"/{{char}}/", "macros": "raw"}, "A.B", "Bob")
    assert re.search(spec["p"], "AxB")


def test_matches_report_groups_and_spans() -> None:
    spec, _ = build_spec({"find": r"/(?<w>b+)/g"}, "A", "B")
    found = find_all(re.compile(spec["p"], spec["f"]), spec, "abbcb")
    assert [(m["start"], m["end"], m["text"]) for m in found] == [(1, 3, "bb"), (4, 5, "b")]
    assert found[0]["named"] == {"w": "bb"} and found[0]["groups"] == ["bb"]


ERRORS = [
    (r"/(a/", "Не закрыта"),
    (r"/a)/", "Скобки"),
    (r"/[a/", "Не закрыта «[»"),
    (r"/*a/", "Нечего повторять"),
    (r"/a++/", "«+» после повтора"),
    (r"/a{3,2}/", "второе число меньше"),
    (r"/(?<=a+)b/", "Lookbehind"),
    (r"/\p{L}/u", "Свойства Юникода"),
    (r"/(?i)a/", "Конструкция"),
    (r"/(?>a)/", "Конструкция"),
    (r"/a/gg", "дважды"),
    (r"/a/x", "Неизвестный флаг"),
    (r"/a/v", "не поддерживается"),
    (r"/\2(a)/", "на группу, которой нет"),
    (r"/\k<zz>(?<a>x)/", "группу, которой нет"),
    (r"/(?<1a>x)/", "Имя группы"),
    (r"/[z-a]/", "наоборот"),
    (r"//", "пуст"),
    ("", "пуст"),
    ("/a\\", "одиночным"),
]


@pytest.mark.parametrize(("find", "message"), ERRORS)
def test_what_cannot_be_translated_is_refused_in_russian(find: str, message: str) -> None:
    with pytest.raises(RegexError) as caught:
        translate(find)
    assert message in caught.value.message


def test_translation_keeps_flags_and_group_facts() -> None:
    result = translate(r"/(?<a>x)(y)/gi")
    assert result.glob and not result.sticky and result.groups == 2 and result.names == ("a",)
    assert result.flags & re.IGNORECASE


def test_worker_literal_precheck_changes_nothing_and_rules_keep_their_order():
    from tavern.core import regex_worker
    from tavern.core.regex import build_spec

    def spec(find, replace=""):
        return build_spec({"find": find, "replace": replace})[0]

    specs = [spec("/cat/g", "dog"), spec("/CAT/gi", "bird"), spec("/dog/", "wolf"), spec(r"/c.t/g", "x"),
             spec("/zzz/g", "never")]
    job = {"specs": specs, "items": [{"text": "cat CAT", "rules": [4, 2, 0, 1]}, {"text": "nothing", "rules": [0, 3]},
                                     {"text": "a cat", "rules": [3, 0, 2]}], "find": False}
    out = regex_worker.run_job(job)
    assert out["outs"] == ["wolf bird", "nothing", "a wolf"] and out["hits"] == [[0, 1, 2], [], [0, 2]]
    # a literal that is regex syntax is not taken for plain text
    assert regex_worker._literal(spec("/a.b/")) is None and regex_worker._literal(spec("/ab/i")) is None
    assert regex_worker._literal(spec("/ab/")) == "ab"


def test_runner_sends_only_the_used_rules_and_names_the_right_ones():
    from tavern.core.regex import build_spec
    from tavern.core.regex_runner import Runner

    specs = [build_spec({"find": f"/w{i}/g", "replace": f"R{i}"})[0] for i in range(50)]
    specs.append(build_spec({"find": "/(a+)+$/"})[0])
    runner = Runner()
    try:
        out = runner.run(specs, [{"text": "w3 w40", "rules": [40, 3]}, {"text": "w49", "rules": [49]}])
        assert out.outs == ["R3 R40", "R49"] and out.hits == [[3, 40], [49]] and out.skipped == {}
        slow = runner.run(specs, [{"text": "a" * 40 + "b", "rules": [50]}], rule_seconds=0.5, job_seconds=3)
        assert slow.skipped == {50: "slow"}
    finally:
        runner.close()
