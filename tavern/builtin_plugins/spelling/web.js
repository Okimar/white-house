// ✎ Орфография: a strip above the message box lists the misspelled words; a tap on a word shows the
// suggestions (and «В словарь»). With «Исправлять автоматически» on, sure single-suggestion mistakes are
// fixed after a pause, and «Вернуть» undoes the last correction. The text is checked only after a
// pause in typing and never holds up sending; a failing service is reported once, quietly.
export default function setup(t) {
  let issues = [];            // {start, end, word, suggestions, sure}, valid for the text they were found in
  let note = null;            // a quiet line when the service does not answer
  let undo = null;            // {edits: [{to, from, at}]} of the last automatic correction
  let timer = null;
  let busy = false;
  let blockedUntil = 0;       // after a failure the service is left alone for a while
  const skipped = new Set();  // words skipped for now (and ones the person took back), lower case

  const wordOf = (i) => i.word.toLowerCase();
  const pause = () => Math.max(500, Number(t.settings.pause) || 1200);

  // Where the word is now: at the old place, else the first place it occurs.
  const locate = (text, issue) => {
    if (text.slice(issue.start, issue.end) === issue.word) return issue.start;
    const at = text.indexOf(issue.word);
    return at;
  };

  const apply = (issue, replacement) => {
    const text = t.composer.text();
    const at = locate(text, issue);
    if (at < 0) { t.toast("Слово уже изменено"); return; }
    t.composer.replace([{ start: at, end: at + issue.word.length, text: replacement }]);
    issues = issues.filter((i) => i !== issue);
    t.refresh();
  };

  const autocorrect = (text, caret, found) => {
    const todo = found.filter((i) => i.sure && !skipped.has(wordOf(i)) && (caret < i.start || caret > i.end));
    if (!todo.length) return false;
    const edits = todo.map((i) => ({ start: i.start, end: i.end, text: i.suggestions[0], from: i.word }));
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
      let at = text.slice(e.at, e.at + e.to.length) === e.to ? e.at : text.indexOf(e.to);
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
    if (busy) { schedule(); return; }
    busy = true;
    try {
      const result = await t.call("check", { text: sent, chat_id: t.chatId() || "" });
      note = null;
      if (t.composer.text() !== sent) return;   // typed on meanwhile: the next pause checks again
      issues = result.issues || [];
      if (t.settings.autocorrect === true && autocorrect(sent, t.composer.caret(), issues)) return;
      t.refresh();
    } catch (e) {
      blockedUntil = Date.now() + 30000;
      if (note === null) note = "Проверка орфографии сейчас недоступна. Сообщение можно отправить как обычно.";
      t.refresh();
    } finally {
      busy = false;
    }
  };

  function schedule() {
    clearTimeout(timer);
    const wait = Math.max(pause(), blockedUntil - Date.now());
    timer = setTimeout(() => { const text = t.composer.text(); if (text.trim()) run(text); }, wait);
  }

  t.composerHelper({
    onText: (text) => {
      if (!text.trim()) {                       // sent or cleared
        clearTimeout(timer);
        issues = []; undo = null; note = null; skipped.clear();
        t.refresh();
        return;
      }
      issues = issues.filter((i) => text.slice(i.start, i.end) === i.word);
      t.refresh();
      schedule();
    },
    view: () => {
      const shown = issues.filter((i) => !skipped.has(wordOf(i))).slice(0, 8);
      if (!shown.length && !note && !undo) return null;
      return {
        note,
        items: shown.map((i) => ({
          label: i.word,
          title: "Ошибка. Нажмите, чтобы выбрать исправление",
          options: [
            ...i.suggestions.map((s) => ({ label: s, onClick: () => apply(i, s) })),
            { label: "В словарь", onClick: async () => {
              try { await t.call("add_word", { word: i.word }); } catch (e) { t.toast(e.message); return; }
              skipped.add(wordOf(i));
              t.refresh();
            } },
            { label: "Пропустить", onClick: () => { skipped.add(wordOf(i)); t.refresh(); } },
          ],
        })),
        undo: undo ? { label: "↩ Вернуть исправление", onClick: takeBack } : null,
      };
    },
  });

  t.on("chat", () => { issues = []; undo = null; note = null; skipped.clear(); t.refresh(); });
  return () => clearTimeout(timer);
}
