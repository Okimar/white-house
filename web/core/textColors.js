// Coloured text in messages: the text between an opening mark and its closing mark is drawn in a colour, marks
// included, as SillyTavern colours "quotes". The person sets their own marks (setting `text_colors`, Settings →
// Основное → «Цветной текст»): any opening and closing text, a colour (empty = the theme's quote colour) and on / off.
// Without the setting the quotes "…", “…” and «…» are coloured. Marks do not nest; an opening without its closing
// mark stays plain (so a reply being written colours its quote once the quote closes). Text only: no HTML is made.
import { html } from "../vendor/preact-htm.mjs";

export const DEFAULT_TEXT_COLORS = [
  { open: "\"", close: "\"", color: "", on: true },
  { open: "“", close: "”", color: "", on: true },
  { open: "«", close: "»", color: "", on: true },
];

export function textColorRules(settings) {
  const list = Array.isArray(settings && settings.text_colors) ? settings.text_colors : DEFAULT_TEXT_COLORS;
  return list.filter((r) => r && r.on !== false && r.open && r.close);
}

// text -> the same text as strings and coloured <span>s (or the text itself when nothing matches)
export function colorize(text, rules) {
  if (!text || !rules || !rules.length) return text;
  const out = [];
  let plain = 0; // where the plain text not yet put out starts
  let at = 0;
  while (at < text.length) {
    let best = null;
    for (const rule of rules) {
      const start = text.indexOf(rule.open, at);
      if (start === -1 || (best && start >= best.start)) continue;
      const end = text.indexOf(rule.close, start + rule.open.length);
      if (end === -1) continue;
      best = { start, end: end + rule.close.length, rule };
    }
    if (!best) break;
    if (best.start > plain) out.push(text.slice(plain, best.start));
    const color = best.rule.color || null;
    out.push(html`<span class="text-mark" data-slot="text-mark" style=${color ? { color } : null}>${text.slice(best.start, best.end)}</span>`);
    plain = at = best.end;
  }
  if (!out.length) return text;
  if (plain < text.length) out.push(text.slice(plain));
  return out;
}
