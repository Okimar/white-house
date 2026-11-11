// Marks in the message box: plugins (spelling) mark ranges of the typed text, the composer draws them as wavy
// underlines on a layer over the textarea (a textarea cannot style its own text) and opens a small menu when the
// caret lands in one. Pure parts here, tested in Node (tests/unit/test_web_logic.py); drawing is in Composer.js.

// The text cut into pieces for the layer: [{text, mark: index | -1}]. Marks are {start, end}; ones out of the text,
// empty or overlapping an earlier one are left out (they are drawn as plain text). The last piece always ends with
// a character so a trailing newline still takes its line, as it does in the textarea.
export function segments(text, marks) {
  const list = (marks || [])
    .map((m, index) => ({ start: m.start, end: m.end, index }))
    .filter((m) => Number.isInteger(m.start) && Number.isInteger(m.end) && m.start >= 0 && m.end <= text.length
      && m.start < m.end)
    .sort((a, b) => a.start - b.start);
  const out = [];
  let at = 0;
  for (const m of list) {
    if (m.start < at) continue; // overlaps the one before
    if (m.start > at) out.push({ text: text.slice(at, m.start), mark: -1 });
    out.push({ text: text.slice(m.start, m.end), mark: m.index });
    at = m.end;
  }
  out.push({ text: text.slice(at) + "​", mark: -1 });
  return out;
}

// The mark the caret is in (its ends count: a tap just after a word opens that word), or -1.
export function markAt(marks, caret) {
  for (let i = 0; i < (marks || []).length; i++) {
    const m = marks[i];
    if (m && m.start <= caret && caret <= m.end && m.start < m.end) return i;
  }
  return -1;
}

// Edits [{start, end, text}] applied to `text`, and where the caret goes: it keeps its place in the text around
// it (moves with the edits before it; one inside an edited range goes to the end of the new text).
// Returns {text, caret} or null when an edit does not fit the text.
export function applyEdits(text, edits, caret) {
  let next = text;
  let shifted = Math.max(0, Math.min(caret, text.length));
  const at = shifted;
  for (const e of [...edits].sort((a, b) => b.start - a.start)) {
    if (!(e.start >= 0 && e.end <= next.length && e.start <= e.end)) return null;
    next = next.slice(0, e.start) + e.text + next.slice(e.end);
    if (e.end <= at) shifted += e.text.length - (e.end - e.start);
    else if (e.start < at) shifted = e.start + e.text.length;
  }
  return { text: next, caret: shifted };
}

// Should the message box say once that spelling is off? Only for the built-in plugin that is not on, and only until
// the person has closed the hint.
export function spellingHint(plugin, dismissed) {
  return Boolean(plugin && plugin.builtin && !plugin.enabled && !dismissed);
}
