// ✎ Орфография: mistakes are underlined right in the message box (the composer's marks layer); a tap on an
// underlined word opens its corrections, «Пропустить» and «В словарь». The text is checked after a pause in
// typing and never holds up sending. Two parts are asked apart: the dictionary service ("dict", fast, its marks
// show at once) and, with the engine «model», the model ("model", a few seconds later); where both mark one place
// the model's mark is shown. One failing part does not stop the other; its reason is said once, quietly, above the
// box. With «Исправлять автоматически» on, the dictionary fixes only its sure one-word typos and the model applies
// everything it found (the owner wants that), never the word the caret is in; «Вернуть» (a strip above the box)
// takes back every correction not yet taken back.

const KIND_TITLES = { spelling: "Ошибка в слове", grammar: "Грамматика", punctuation: "Пунктуация" };
const LETTER = /[\p{L}\p{N}]/u;
const PART_NAMES = { dict: "Обычная проверка орфографии", model: "Проверка моделью" };

// Ranges found in `before` moved to `after` (one edit between them, found as the common start and end): ranges
// before the edit stay, ranges after it shift, ranges the edit touches are dropped, as is one the edit glued
// letters onto (its word changed). Each range keeps its other fields; `word` must still be there.
export function shiftRanges(before, after, ranges) {
  if (before === after) return ranges;
  let p = 0;
  const most = Math.min(before.length, after.length);
  while (p < most && before[p] === after[p]) p++;
  let s = 0;
  while (s < most - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  const oldEnd = before.length - s;
  const delta = after.length - before.length;
  const out = [];
  for (const r of ranges) {
    let start;
    if (r.end <= p) start = r.start;
    else if (r.start >= oldEnd) start = r.start + delta;
    else continue;
    const end = start + (r.end - r.start);
    if (r.word !== undefined && after.slice(start, end) !== r.word) continue;
    if (r.end === p && LETTER.test(after[end] || "")) continue;              // letters typed onto its end
    if (r.start === oldEnd && LETTER.test(after[start - 1] || "")) continue; // or onto its start
    out.push(start === r.start ? r : { ...r, start, end });
  }
  return out;
}

// Ranges moved through several known edits ({start, end, text} of the old text, not overlapping): a range an edit
// touches is dropped, the others shift by what the edits before them changed.
export function mapThrough(ranges, edits) {
  const sorted = [...edits].sort((a, b) => a.start - b.start);
  const out = [];
  for (const r of ranges) {
    let delta = 0;
    let hit = false;
    for (const e of sorted) {
      if (e.start < r.end && e.end > r.start) { hit = true; break; }
      if (e.end <= r.start) delta += e.text.length - (e.end - e.start);
    }
    if (!hit) out.push(delta ? { ...r, start: r.start + delta, end: r.end + delta } : r);
  }
  return out;
}

// The text after the edits, and where each edit's new text stands in it.
function applied(text, edits) {
  const sorted = [...edits].sort((a, b) => a.start - b.start);
  let out = "";
  let last = 0;
  const places = [];
  for (const e of sorted) {
    out += text.slice(last, e.start);
    places.push({ edit: e, at: out.length });
    out += e.text;
    last = e.end;
  }
  return { text: out + text.slice(last), places };
}

const overlaps = (a, b) => a.start < b.end && a.end > b.start;

// The marks to show: all the model's, the dictionary's where the model has none.
export function mergeMarks(dict, model) {
  return [...model, ...dict.filter((d) => !model.some((m) => overlaps(d, m)))].sort((a, b) => a.start - b.start);
}

export default function setup(t) {
  const lists = { dict: [], model: [] }; // {start, end, word, suggestions, sure, kind} in the text `basis`
  let basis = "";
  const notes = { dict: null, model: null }; // the reason a part does not work
  let undo = null;            // {edits: [{start, end, word (the correction), from}]} in `basis`
  let timer = null;
  const seq = { dict: 0, model: 0 };        // the newest request of each part; older answers are dropped
  const checked = { dict: null, model: null };
  const blockedUntil = { dict: 0, model: 0 }; // after a failure a part is left alone for a while
  let own = null;             // {from, to, edits}: the text before and after our last autocorrection
  const skipped = new Set();  // words skipped for now (and ones the person took back), lower case

  const wordOf = (i) => i.word.toLowerCase();
  const pause = () => Math.max(300, Number(t.settings.pause) || 700);
  const parts = () => (t.settings.engine === "model"
    ? (t.settings.base === "off" ? ["model"] : ["dict", "model"]) : ["dict"]);

  // Bring the issues to the text now in the box (called while the box renders, so marks never lag a frame).
  const follow = (text) => {
    if (text === basis) return;
    lists.dict = shiftRanges(basis, text, lists.dict);
    lists.model = shiftRanges(basis, text, lists.model);
    if (undo) {
      undo.edits = shiftRanges(basis, text, undo.edits);
      if (!undo.edits.length) undo = null;
    }
    basis = text;
  };

  const reset = () => {
    lists.dict = []; lists.model = []; basis = ""; undo = null; notes.dict = null; notes.model = null;
    checked.dict = null; checked.model = null; own = null; skipped.clear();
  };

  // Edits of the box with every list moved through them first (so nothing between them is lost). `false` when the
  // box refused them.
  const edit = (edits) => {
    const text = t.composer.text();
    follow(text);
    const after = applied(text, edits);
    const saved = { dict: lists.dict, model: lists.model, undo, basis };
    lists.dict = mapThrough(lists.dict, edits);
    lists.model = mapThrough(lists.model, edits);
    if (undo) undo = { edits: mapThrough(undo.edits, edits) };
    basis = after.text;
    if (!t.composer.replace(edits.map(({ start, end, text: to }) => ({ start, end, text: to })))) {
      lists.dict = saved.dict; lists.model = saved.model; undo = saved.undo; basis = saved.basis;
      return null;
    }
    return after;
  };

  const apply = (issue, replacement) => {
    follow(t.composer.text());
    if (basis.slice(issue.start, issue.end) !== issue.word) { t.toast("Слово уже изменено"); return; }
    edit([{ start: issue.start, end: issue.end, text: replacement }]);
    t.refresh();
  };

  const autocorrect = (part) => {
    const caret = t.composer.caret();
    const free = (i) => i.suggestions.length && !skipped.has(wordOf(i)) && (caret < i.start || caret > i.end);
    const todo = part === "model"
      ? lists.model.filter(free)
      : lists.dict.filter((i) => free(i) && i.sure && (i.kind || "spelling") === "spelling"
        && !lists.model.some((m) => overlaps(i, m)));
    if (!todo.length) return false;
    const edits = todo.map((i) => ({ start: i.start, end: i.end, text: i.suggestions[0], from: i.word }));
    const basisBefore = basis;
    const after = edit(edits);
    if (!after) return false;
    const mine = after.places.map(({ edit: e, at }) => ({ start: at, end: at + e.text.length, word: e.text, from: e.from }));
    undo = { edits: [...(undo ? undo.edits : []), ...mine].sort((a, b) => a.start - b.start) };
    own = { from: basisBefore, to: after.text, edits };
    t.refresh();
    return true;
  };

  const takeBack = () => {
    if (!undo) return;
    follow(t.composer.text());
    const edits = undo.edits.filter((e) => basis.slice(e.start, e.end) === e.word)
      .map((e) => ({ start: e.start, end: e.end, text: e.from }));
    for (const e of undo.edits) skipped.add(e.from.toLowerCase());
    undo = null;
    if (edits.length) edit(edits);
    t.refresh();
  };

  const run = async (part, sent) => {
    const mine = ++seq[part];
    checked[part] = sent;
    try {
      const result = await t.call("check", { text: sent, chat_id: t.chatId() || "", part });
      if (mine !== seq[part] || result.stale) return;   // a newer check is on its way
      notes[part] = null;
      const now = t.composer.text();
      follow(now);
      // Our own autocorrection came in between: the found ones move through its edits; typed on meanwhile: they
      // move with the text.
      const ours = own && own.from === sent && own.to === now;
      lists[part] = ours ? mapThrough(result.issues || [], own.edits) : shiftRanges(sent, now, result.issues || []);
      if (t.settings.autocorrect === true && (now === sent || ours) && autocorrect(part)) return;
      t.refresh();
    } catch (e) {
      if (mine !== seq[part]) return;
      checked[part] = null;
      blockedUntil[part] = Date.now() + 15000;
      notes[part] = String((e && e.message) || "сервис не ответил").replace(/[\s.!?…]*$/, ".");
      t.refresh();
    }
  };

  function schedule() {
    clearTimeout(timer);
    const now = Date.now();
    const wait = Math.max(pause(), Math.min(...parts().map((p) => blockedUntil[p] - now)));
    timer = setTimeout(() => {
      const text = t.composer.text();
      if (!text.trim()) return;
      for (const part of parts()) {
        if (text !== checked[part] && Date.now() >= blockedUntil[part]) run(part, text);
      }
    }, wait);
  }

  const noteText = () => {
    const on = parts();
    const broken = on.filter((p) => notes[p]);
    if (!broken.length) return null;
    const said = broken.map((p) => (on.length === 1 && p === "dict"
      ? `Проверка орфографии не работает: ${notes[p]}` : `${PART_NAMES[p]} не работает: ${notes[p]}`));
    return `${said.join(" ")} Сообщение можно отправить как обычно.`;
  };

  const marks = () => mergeMarks(lists.dict, lists.model).filter((i) => !skipped.has(wordOf(i))).map((i) => ({
    start: i.start,
    end: i.end,
    word: i.word,
    kind: i.kind || "spelling",
    title: KIND_TITLES[i.kind] || KIND_TITLES.spelling,
    options: [
      ...i.suggestions.map((s) => ({ label: s, onClick: () => apply(i, s) })),
      { label: "Пропустить", quiet: true, onClick: () => { skipped.add(wordOf(i)); t.refresh(); } },
      { label: "В словарь", quiet: true, onClick: async () => {
        skipped.add(wordOf(i));
        t.refresh();
        try { await t.call("add_word", { word: i.word }); await t.reloadSettings(); } catch (e) { t.toast(e.message); }
      } },
    ],
  }));

  t.composerHelper({
    onText: (text) => {
      if (!text.trim()) {                       // sent or cleared
        clearTimeout(timer);
        seq.dict++; seq.model++;
        const had = lists.dict.length || lists.model.length || notes.dict || notes.model || undo;
        reset();
        if (had) t.refresh();
        return;
      }
      follow(text);
      schedule();
    },
    view: () => {
      follow(t.composer.text());
      const shown = marks();
      const note = noteText();
      if (!shown.length && !note && !undo) return null;
      return {
        note,
        items: [],
        marks: shown,
        undo: undo ? { label: "↩ Вернуть исправление", onClick: takeBack } : null,
      };
    },
  });

  t.on("chat", () => { clearTimeout(timer); seq.dict++; seq.model++; reset(); t.refresh(); });
  return () => { clearTimeout(timer); seq.dict++; seq.model++; };
}
