"""The page side of spelling, run in Node (skipped where it is missing, like Termux): marks layer pieces, the mark
under the caret, replacements that keep the caret, underlines that move with typing, the «spelling is off» hint,
and the plugin's web part end to end with a fake `t` (the owner's «Привит я бола»: underlined, tap, pick). Then the
owner's tablet report: with the model on, the dictionary still checks (both parts, merged, the model's mark wins),
one part failing leaves the other, the model's autocorrect applies all it found except the word being typed, the
dictionary's only its sure typos, and «Вернуть» takes every correction back."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
PLUGIN = (ROOT / "tavern" / "builtin_plugins" / "spelling" / "web.js").as_uri()
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def run_module(code: str) -> object:
    result = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, timeout=30, cwd=WEB)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def marks_js(expr: str) -> object:
    return run_module(f'import * as m from "./core/textMarks.js"; console.log(JSON.stringify({expr}))')


def shift(before: str, after: str, ranges: list[dict]) -> list[dict]:
    return run_module(f'import {{ shiftRanges }} from "{PLUGIN}";'
                      f"console.log(JSON.stringify(shiftRanges({json.dumps(before)}, {json.dumps(after)}, "
                      f"{json.dumps(ranges)})))")


# -- core/textMarks.js ------------------------------------------------------------------------------------------


def test_segments_cut_the_text_around_marks_and_end_with_a_character():
    got = marks_js('m.segments("Привит я бола\\n", [{start: 9, end: 13}, {start: 0, end: 6}, {start: 3, end: 5},'
                   ' {start: 20, end: 30}, {start: 4, end: 4}])')
    assert got == [{"text": "Привит", "mark": 1}, {"text": " я ", "mark": -1}, {"text": "бола", "mark": 0},
                   {"text": "\n​", "mark": -1}]
    assert "".join(s["text"] for s in got) == "Привит я бола\n​"


def test_the_mark_under_the_caret_counts_its_ends():
    marks = '[{start: 0, end: 6}, {start: 9, end: 13}]'
    assert marks_js(f"[0, 3, 6, 7, 9, 13, 14].map((c) => m.markAt({marks}, c))") == [0, 0, 0, -1, 1, 1, -1]


def test_a_replacement_keeps_the_caret_in_its_place():
    text = "Привит я бола и всё"
    # caret after «и»: both words before it are replaced, the caret moves by what they changed
    got = marks_js(f'm.applyEdits({json.dumps(text)}, [{{start: 0, end: 6, text: "Привет,"}},'
                   ' {start: 9, end: 13, text: "была"}], 15)')
    assert got == {"text": "Привет, я была и всё", "caret": 16}
    # caret inside the replaced word goes to the end of the new word; edits after it leave it alone
    assert marks_js(f'm.applyEdits({json.dumps(text)}, [{{start: 9, end: 13, text: "была"}}], 11)')["caret"] == 13
    assert marks_js(f'm.applyEdits({json.dumps(text)}, [{{start: 9, end: 13, text: "была"}}], 2)')["caret"] == 2
    assert marks_js(f'm.applyEdits({json.dumps(text)}, [{{start: 30, end: 40, text: "x"}}], 2)') is None


def test_the_off_hint_is_only_for_the_builtin_plugin_that_is_off_and_not_dismissed():
    assert marks_js('[m.spellingHint({builtin: true, enabled: false}, false), m.spellingHint({builtin: true, enabled: true},'
                    ' false), m.spellingHint({builtin: true, enabled: false}, true), m.spellingHint(null, false)]') == [
        True, False, False, False]


# -- underlines move with typing -----------------------------------------------------------------------------


def test_underlines_move_with_text_typed_before_them_and_stay_when_typing_after():
    ranges = [{"start": 0, "end": 6, "word": "Привит"}, {"start": 9, "end": 13, "word": "бола"}]
    assert shift("Привит я бола", "Ну, Привит я бола", ranges) == [
        {"start": 4, "end": 10, "word": "Привит"}, {"start": 13, "end": 17, "word": "бола"}]
    assert shift("Привит я бола", "Привит я бола и", ranges) == ranges
    assert shift("Привит я бола", "Привит я бола ", ranges) == ranges


def test_an_underline_goes_when_its_word_is_edited_or_letters_are_glued_to_it():
    ranges = [{"start": 0, "end": 6, "word": "Привит"}, {"start": 9, "end": 13, "word": "бола"}]
    assert shift("Привит я бола", "Привет я бола", ranges) == [{"start": 9, "end": 13, "word": "бола"}]
    assert shift("Привит я бола", "Привит я болас", ranges) == [{"start": 0, "end": 6, "word": "Привит"}]
    assert shift("Привит я бола", "Привит я xбола", ranges) == [{"start": 0, "end": 6, "word": "Привит"}]
    assert shift("Привит я бола", "", ranges) == []


# -- the plugin's web part with a fake `t` -------------------------------------------------------------------


FAKE_T = """
let text = "";
let caret = 0;
const calls = [];
const replaced = [];
let helper = null;
let answer = null;
const t = {
  settings: { pause: 300 },
  call: async (name, payload) => { calls.push([name, payload]); return answer(name, payload); },
  chatId: () => "c1",
  toast: () => {},
  refresh: () => {},
  reloadSettings: async () => {},
  on: () => {},
  composerHelper: (h) => { helper = h; },
  composer: {
    text: () => text,
    caret: () => caret,
    replace: (edits) => {
      for (const e of [...edits].sort((a, b) => b.start - a.start)) text = text.slice(0, e.start) + e.text + text.slice(e.end);
      replaced.push(edits);
      return true;
    },
  },
};
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const type = (value) => { text = value; caret = value.length; helper.onText(text, caret); };
"""


def test_the_owners_line_is_underlined_and_a_tap_on_a_variant_fixes_it():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + """
answer = (name, payload) => ({ issues: [
  { start: 0, end: 6, word: "Привит", suggestions: ["Привет,"], sure: false, kind: "spelling" },
  { start: 9, end: 13, word: "бола", suggestions: ["была"], sure: false, kind: "grammar" },
] });
setup(t);
type("Привит я бола");
const before = helper.view();
await wait(450);
const view = helper.view();
const marks = view.marks.map((m) => [m.start, m.end, m.word, m.kind, m.title, m.options.map((o) => o.label)]);
view.marks[1].options[0].onClick();
const after = helper.view();
console.log(JSON.stringify({ before, calls, marks, text, replaced, left: after.marks.map((m) => m.word) }));
""")
    assert got["before"] is None  # nothing before the pause
    assert got["calls"] == [["check", {"text": "Привит я бола", "chat_id": "c1", "part": "dict"}]]
    assert got["marks"] == [
        [0, 6, "Привит", "spelling", "Ошибка в слове", ["Привет,", "Пропустить", "В словарь"]],
        [9, 13, "бола", "grammar", "Грамматика", ["была", "Пропустить", "В словарь"]]]
    assert got["text"] == "Привит я была" and got["left"] == ["Привит"]


def test_a_failing_check_says_why():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + """
answer = () => { throw new Error("Дополнению «✎ Орфография» не разрешено обращаться к моделям."); };
setup(t);
type("Привит я бола");
await wait(450);
const note = helper.view().note;
console.log(JSON.stringify({ note }));
""")
    assert "не разрешено обращаться к моделям" in got["note"]
    assert "Сообщение можно отправить как обычно" in got["note"]


def test_found_ones_follow_the_text_typed_while_the_check_was_on_its_way():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + """
answer = async (name, payload) => {
  if (payload.text !== "Привит я бола") return { issues: [], stale: true };  // a newer check took over
  await wait(30);
  return { issues: [{ start: 9, end: 13, word: "бола", suggestions: ["была"], sure: false }] };
};
setup(t);
type("Привит я бола");
await wait(320);           // the check is on its way
type("Ну, Привит я бола"); // typed before the word meanwhile
await wait(40);
const marks = helper.view().marks.map((m) => [m.start, m.end, m.word]);
console.log(JSON.stringify({ marks }));
""")
    assert got["marks"] == [[13, 17, "бола"]]


# -- both parts, merged; autocorrect by part (owner, 2026-11-11) -----------------------------------------------


MODEL_ANSWER = """
const dictIssues = (text) => {
  const out = [];
  for (const [w, s, sure] of [["превет", "привет", true], ["бола", "болта", false]]) {
    const at = text.indexOf(w);
    if (at >= 0) out.push({ start: at, end: at + w.length, word: w, suggestions: [s], sure, kind: "spelling" });
  }
  return out.sort((a, b) => a.start - b.start);
};
const modelIssues = (text) => {
  const out = [];
  for (const [w, s, kind] of [["Привит", "Привет,", "spelling"], ["бола", "была", "grammar"], ["ещ", "ещё", "spelling"]]) {
    const at = text.indexOf(w);
    if (at >= 0) out.push({ start: at, end: at + w.length, word: w, suggestions: [s], sure: false, kind, source: "model" });
  }
  return out;
};
let modelWait = 60;
answer = async (name, payload) => {
  if (payload.part === "dict") return { issues: dictIssues(payload.text) };
  await wait(modelWait);
  return { issues: modelIssues(payload.text) };
};
t.settings.engine = "model";
"""


def test_with_the_model_on_the_dictionary_marks_come_first_and_the_models_win_on_one_place():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + MODEL_ANSWER + """
setup(t);
type("Привит я бола и превет");
await wait(330);
const early = helper.view().marks.map((m) => [m.word, m.options[0].label]);
await wait(100);
const late = helper.view().marks.map((m) => [m.word, m.options[0].label]);
console.log(JSON.stringify({ parts: calls.map((c) => c[1].part).sort(), early, late, text }));
""")
    assert got["parts"] == ["dict", "model"]
    assert got["early"] == [["бола", "болта"], ["превет", "привет"]]          # the dictionary at once
    assert got["late"] == [["Привит", "Привет,"], ["бола", "была"], ["превет", "привет"]]
    assert got["text"] == "Привит я бола и превет"                             # autocorrect is off


def test_one_part_failing_leaves_the_other_and_says_which():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + MODEL_ANSWER + """
answer = async (name, payload) => {
  if (payload.part === "model") throw new Error("модель прислала пустой ответ");
  return { issues: dictIssues(payload.text) };
};
setup(t);
type("Это превет");
await wait(380);
const view = helper.view();
console.log(JSON.stringify({ note: view.note, marks: view.marks.map((m) => m.word) }));
""")
    assert got["marks"] == ["превет"]
    assert got["note"].startswith("Проверка моделью не работает: модель прислала пустой ответ.")


def test_the_models_autocorrect_applies_everything_but_the_word_being_typed_and_vernut_takes_all_back():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + MODEL_ANSWER + """
t.settings.autocorrect = true;
setup(t);
type("Привит я бола и превет и ещ");
await wait(450);
const fixed = text;
const view = helper.view();
const left = view.marks.map((m) => m.word);
view.undo.onClick();
const back = text;
const after = helper.view();
console.log(JSON.stringify({ fixed, left, back, after: after && after.marks.map((m) => m.word) }));
""")
    # the dictionary fixed its sure «превет» at once; the model then fixed all of its own, but not «ещ» under the caret
    assert got["fixed"] == "Привет, я была и привет и ещ"
    assert got["left"] == ["ещ"]
    assert got["back"] == "Привит я бола и превет и ещ"   # «Вернуть»: every correction taken back
    assert got["after"] == ["ещ"]                        # the taken-back words are not marked again


def test_the_dictionarys_autocorrect_fixes_only_its_sure_typos():
    got = run_module(f'import setup from "{PLUGIN}";' + FAKE_T + MODEL_ANSWER + """
t.settings.engine = "languagetool";
t.settings.autocorrect = true;
setup(t);
type("Тут бола и превет и всё");
await wait(380);
console.log(JSON.stringify({ text, marks: helper.view().marks.map((m) => m.word), undo: !!helper.view().undo }));
""")
    assert got["text"] == "Тут бола и привет и всё"     # «бола» has an unsure suggestion: only underlined
    assert got["marks"] == ["бола"] and got["undo"] is True


def test_merge_and_map_through_edits():
    got = run_module(f'import {{ mergeMarks, mapThrough }} from "{PLUGIN}";' + """
const merged = mergeMarks([{ start: 0, end: 4, word: "a" }, { start: 10, end: 12, word: "b" }],
                          [{ start: 2, end: 6, word: "m" }]).map((r) => r.word);
const moved = mapThrough([{ start: 0, end: 3 }, { start: 5, end: 8 }, { start: 10, end: 12 }],
                         [{ start: 5, end: 8, text: "xxxx" }, { start: 0, end: 3, text: "y" }]);
console.log(JSON.stringify({ merged, moved }));
""")
    assert got["merged"] == ["m", "b"]
    assert got["moved"] == [{"start": 9, "end": 11}]  # -2 and +1 before it
