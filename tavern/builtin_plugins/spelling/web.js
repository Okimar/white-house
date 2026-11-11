// ✎ Орфография: mistakes are underlined right in the message box (the composer's marks layer); a tap on an
// underlined word opens its corrections, «Пропустить» and «В словарь». With «Исправлять автоматически» on, sure
// single-suggestion mistakes are fixed after a pause, and «Вернуть» (a strip above the box) undoes the last
// correction. The text is checked after a pause in typing and never holds up sending; a newer check replaces an
// older one (the server cancels the model request still running), and the reason of a failure is said once,
// quietly, above the box.

const KIND_TITLES = { spelling: "Ошибка в слове", grammar: "Грамматика", punctuation: "Пунктуация" };
const LETTER = /[\p{L}\p{N}]/u;

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

export default function setup(t) {
  let issues = [];            // {start, end, word, suggestions, sure, kind} in the text `basis`
  let basis = "";
  let note = null;            // a quiet line when the check does not work, with the reason
  let undo = null;            // {edits: [{to, from, at}]} of the last automatic correction
  let timer = null;
  let seq = 0;                // the newest request; answers of older ones are dropped
  let checked = null;         // the text last checked (no second request for the same text)
  let blockedUntil = 0;       // after a failure the checker is left alone for a while
  const skipped = new Set();  // words skipped for now (and ones the person took back), lower case

  const wordOf = (i) => i.word.toLowerCase();
  const pause = () => Math.max(300, Number(t.settings.pause) || 700);

  // Bring the issues to the text now in the box (called while the box renders, so marks never lag a frame).
  const follow = (text) => {
    if (text === basis) return;
    issues = shiftRanges(basis, text, issues);
    basis = text;
  };

  const reset = () => { issues = []; basis = ""; undo = null; note = null; checked = null; skipped.clear(); };

  const apply = (issue, replacement) => {
    const text = t.composer.text();
    follow(text);
    if (text.slice(issue.start, issue.end) !== issue.word) { t.toast("Слово уже изменено"); return; }
    issues = issues.filter((i) => !(i.start === issue.start && i.end === issue.end));
    t.composer.replace([{ start: issue.start, end: issue.end, text: replacement }]);
    t.refresh();
  };

  const autocorrect = (caret, found) => {
    const todo = found.filter((i) => i.sure && (i.kind || "spelling") === "spelling" && !skipped.has(wordOf(i))
      && (caret < i.start || caret > i.end));
    if (!todo.length) return false;
    const edits = todo.map((i) => ({ start: i.start, end: i.end, text: i.suggestions[0], from: i.word }));
    issues = issues.filter((i) => !todo.includes(i));
    if (!t.composer.replace(edits.map(({ start, end, text: to }) => ({ start, end, text: to })))) return false;
    // Positions of the corrections in the new text, to take them back later.
    let delta = 0;
    undo = {
      edits: [...edits].sort((a, b) => a.start - b.start).map((e) => {
        const at = e.start + delta;
        delta += e.text.length - (e.end - e.start);
        return { at, to: e.text, from: e.from };
      }),
    };
    return true;
  };

  const takeBack = () => {
    if (!undo) return;
    const text = t.composer.text();
    const edits = [];
    for (const e of undo.edits) {
      const at = text.slice(e.at, e.at + e.to.length) === e.to ? e.at : text.indexOf(e.to);
      if (at < 0) continue;
      edits.push({ start: at, end: at + e.to.length, text: e.from });
      skipped.add(e.from.toLowerCase());
    }
    undo = null;
    if (edits.length) t.composer.replace(edits);
    issues = issues.filter((i) => !skipped.has(wordOf(i)));
    t.refresh();
  };

  const run = async (sent) => {
    const mine = ++seq;
    checked = sent;
    try {
      const result = await t.call("check", { text: sent, chat_id: t.chatId() || "" });
      if (mine !== seq || result.stale) return;   // a newer check is on its way
      note = null;
      const now = t.composer.text();
      issues = result.issues || [];
      basis = sent;
      follow(now);                               // typed on meanwhile: the found ones move with the text
      if (now === sent && t.settings.autocorrect === true && autocorrect(t.composer.caret(), issues)) return;
      t.refresh();
    } catch (e) {
      if (mine !== seq) return;
      checked = null;
      blockedUntil = Date.now() + 15000;
      const reason = String((e && e.message) || "сервис не ответил").replace(/[\s.!?…]*$/, ".");
      note = `Проверка орфографии не работает: ${reason} Сообщение можно отправить как обычно.`;
      t.refresh();
    }
  };

  function schedule() {
    clearTimeout(timer);
    const wait = Math.max(pause(), blockedUntil - Date.now());
    timer = setTimeout(() => {
      const text = t.composer.text();
      if (text.trim() && text !== checked) run(text);
    }, wait);
  }

  const marks = () => issues.filter((i) => !skipped.has(wordOf(i))).map((i) => ({
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
        seq++;
        const had = issues.length || note || undo;
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
      if (!shown.length && !note && !undo) return null;
      return {
        note,
        items: [],
        marks: shown,
        undo: undo ? { label: "↩ Вернуть исправление", onClick: takeBack } : null,
      };
    },
  });

  t.on("chat", () => { clearTimeout(timer); seq++; reset(); t.refresh(); });
  return () => { clearTimeout(timer); seq++; };
}
