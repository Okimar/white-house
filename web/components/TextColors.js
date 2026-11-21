// Settings → Основное → «Цветной текст»: the person's marks for coloured text in messages (core/textColors.js).
// Each row: the opening mark, the closing mark, a colour, on / off, remove. Saves on every finished change.
import { html, useEffect, useState } from "../vendor/preact-htm.mjs";
import { DEFAULT_TEXT_COLORS, colorize } from "../core/textColors.js";
import { IconButton } from "./Icon.js";

const SAMPLE = "Она улыбнулась. \"Привет!\" — сказала она. *Тихо вздыхает.* «Пойдём».";

// the theme's quote colour as #rrggbb, for the colour field of a mark without its own colour
function themeColor() {
  try {
    const probe = document.createElement("span");
    probe.style.color = "var(--text-quote)";
    document.body.appendChild(probe);
    const [r, g, b] = getComputedStyle(probe).color.match(/\d+/g).map(Number);
    probe.remove();
    return "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("");
  } catch (e) { return "#9a5b13"; }
}

// The colour as a code: «#c0392b», «c0392b» or the short «#c33». Saved when the code is whole; empty = the theme's.
function HexField({ value, placeholder, onColor }) {
  const [draft, setDraft] = useState(value || "");
  const [bad, setBad] = useState(false);
  useEffect(() => { setDraft(value || ""); setBad(false); }, [value]);
  const take = (text) => {
    const raw = text.trim().replace(/^#/, "");
    if (!raw) { setBad(false); onColor(""); return; }
    const full = /^[0-9a-f]{3}$/i.test(raw) ? raw.split("").map((c) => c + c).join("") : raw;
    if (!/^[0-9a-f]{6}$/i.test(full)) { setBad(true); return; }
    setBad(false);
    onColor("#" + full.toLowerCase());
  };
  return html`<input class=${"hex" + (bad ? " invalid" : "")} value=${draft} maxlength="7" placeholder=${placeholder}
    aria-label="Цвет текста кодом, например #c0392b" aria-invalid=${bad ? "true" : null} spellcheck="false"
    autocapitalize="off" autocomplete="off"
    onInput=${(e) => setDraft(e.target.value)} onChange=${(e) => take(e.target.value)}
    onKeyDown=${(e) => { if (e.key === "Enter") { e.preventDefault(); take(e.target.value); } }} />`;
}

export function TextColors({ settings, saveSetting }) {
  const stored = Array.isArray(settings.text_colors) ? settings.text_colors : DEFAULT_TEXT_COLORS;
  const [rows, setRows] = useState(stored);
  useEffect(() => { setRows(stored); }, [settings.text_colors]);
  const theme = themeColor();
  const save = (next) => { setRows(next); saveSetting({ text_colors: next.filter((r) => r.open && r.close) }); };
  const edit = (i, patch, now = true) => {
    const next = rows.map((r, k) => (k === i ? { ...r, ...patch } : r));
    if (now) save(next); else setRows(next);
  };
  return html`<div class="text-colors stack" data-slot="text-colors">
    <ul class="text-colors-list">
      ${rows.map((r, i) => html`<li key=${i} class="text-colors-row">
        <input class="mark" value=${r.open} maxlength="10" aria-label="Открывающая метка" placeholder="от"
          onInput=${(e) => edit(i, { open: e.target.value }, false)} onChange=${() => save(rows)} />
        <input class="mark" value=${r.close} maxlength="10" aria-label="Закрывающая метка" placeholder="до"
          onInput=${(e) => edit(i, { close: e.target.value }, false)} onChange=${() => save(rows)} />
        <input type="color" value=${r.color || theme} aria-label="Цвет текста: палитра"
          onChange=${(e) => edit(i, { color: e.target.value })} />
        <${HexField} value=${r.color} placeholder=${theme} onColor=${(color) => edit(i, { color })} />
        <label class="text-colors-on" title="Включена"><input type="checkbox" checked=${r.on !== false}
          aria-label="Метка включена" onChange=${(e) => edit(i, { on: e.target.checked })} /></label>
        <${IconButton} icon="trash-2" label="Удалить метку" onClick=${() => save(rows.filter((_, k) => k !== i))} />
      </li>`)}
    </ul>
    <div class="row">
      <button type="button" disabled=${rows.length >= 30}
        onClick=${() => save([...rows, { open: "*", close: "*", color: "", on: true }])}>Добавить метку</button>
      <button type="button" onClick=${() => save(DEFAULT_TEXT_COLORS)}>Как в SillyTavern</button>
    </div>
    <p class="text-colors-sample" aria-label="Пример">${colorize(SAMPLE, rows.filter((r) => r.on !== false && r.open && r.close))}</p>
  </div>`;
}
