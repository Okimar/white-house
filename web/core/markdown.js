// Markdown in messages, the way SillyTavern shows a reply: **bold**, *italic* / _italic_, ***both***, ~~struck~~,
// `code`, ``` blocks, # headings, > quotes, lists, --- lines, [links](https://…). Built as elements from the text
// (never HTML from the text: a model or a card cannot inject markup). Line breaks stay as written. Pictures
// ![alt](address) become a link «🖼 alt»: the page's security rules load pictures only from this app (/assets/…
// is shown as a picture). Plain text pieces go through the «Цветной текст» marks (core/textColors.js); an
// emphasis whose own marks are one of those (* … *, _ … _) takes that mark's colour.
import { html } from "../vendor/preact-htm.mjs";
import { colorize } from "./textColors.js";

const CACHE = new Map();
const CACHE_MAX = 400;
const SAFE_URL = /^(https?:\/\/|\/assets\/)/i;

// Inline: the first of these that matches wins at each place; the rest of the text is plain.
const INLINE = [
  { re: /^!\[([^\]\n]*)\]\(([^)\s]+)\)/, kind: "img" },
  { re: /^\[([^\]\n]+)\]\(([^)\s]+)\)/, kind: "link" },
  { re: /^`([^`\n]+)`/, kind: "code" },
  { re: /^\*\*\*(?=\S)([\s\S]*?\S)\*\*\*/, kind: "strongem" },
  { re: /^\*\*(?=\S)([\s\S]*?\S)\*\*/, kind: "strong" },
  { re: /^__(?=\S)([\s\S]*?\S)__/, kind: "strong" },
  { re: /^~~(?=\S)([\s\S]*?\S)~~/, kind: "del" },
  { re: /^\*(?=[^\s*])([\s\S]*?[^\s*])\*(?!\*)/, kind: "em", mark: "*" },
  { re: /^_(?=[^\s_])([\s\S]*?[^\s_])_(?![\w])/, kind: "em", mark: "_" },
];
const STARTS = /[!\[`*_~]/;

function markColor(rules, mark) {
  const rule = (rules || []).find((r) => r.open === mark && r.close === mark);
  return rule ? { color: rule.color || "var(--text-quote)" } : null;
}

function inline(text, rules, key = "") {
  const out = [];
  let plain = "";
  const flush = () => {
    if (!plain) return;
    const colored = colorize(plain, rules);
    if (Array.isArray(colored)) out.push(...colored); else out.push(colored);
    plain = "";
  };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    // a word character right before _ is part of a name (snake_case), not emphasis
    if (!STARTS.test(ch) || (ch === "_" && i > 0 && /\w/.test(text[i - 1]))) { plain += ch; i += 1; continue; }
    const rest = text.slice(i);
    let hit = null;
    for (const rule of INLINE) {
      const m = rule.re.exec(rest);
      if (m) { hit = { rule, m }; break; }
    }
    if (!hit) { plain += ch; i += 1; continue; }
    flush();
    const { rule, m } = hit;
    const k = key + "." + out.length;
    if (rule.kind === "img") {
      const url = m[2];
      if (/^\/assets\//i.test(url)) out.push(html`<img key=${k} class="md-img" src=${url} alt=${m[1]} loading="lazy" />`);
      else if (SAFE_URL.test(url)) out.push(html`<a key=${k} class="md-img-link" href=${url} target="_blank" rel="noopener noreferrer">🖼 ${m[1] || "картинка"}</a>`);
      else out.push(m[0]);
    } else if (rule.kind === "link") {
      if (SAFE_URL.test(m[2])) out.push(html`<a key=${k} href=${m[2]} target="_blank" rel="noopener noreferrer">${inline(m[1], rules, k)}</a>`);
      else out.push(m[0]);
    } else if (rule.kind === "code") out.push(html`<code key=${k} class="md-code">${m[1]}</code>`);
    else if (rule.kind === "strongem") out.push(html`<strong key=${k}><em>${inline(m[1], rules, k)}</em></strong>`);
    else if (rule.kind === "strong") out.push(html`<strong key=${k}>${inline(m[1], rules, k)}</strong>`);
    else if (rule.kind === "del") out.push(html`<del key=${k}>${inline(m[1], rules, k)}</del>`);
    else out.push(html`<em key=${k} class="md-em" style=${markColor(rules, rule.mark)}>${inline(m[1], rules, k)}</em>`);
    i += m[0].length;
  }
  flush();
  return out;
}

// Blocks: runs of lines. Everything not a special block is kept as it was written, line breaks included.
function blocks(text, rules) {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  let para = [];
  const flushPara = () => {
    const body = para.join("\n").replace(/^\n+|\n+$/g, "");
    para = [];
    if (!body) return;
    // an empty line between paragraphs is a gap set by the chat density (CSS), not a whole blank line of text
    for (const piece of body.split(/\n[ \t]*\n+/)) {
      const k = "p" + out.length;
      out.push(html`<div key=${k} class="md-p">${inline(piece, rules, k)}</div>`);
    }
  };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = /^\s*```/.exec(line);
    if (fence) {
      const end = lines.findIndex((l, k) => k > i && /^\s*```/.test(l));
      if (end !== -1) {
        flushPara();
        out.push(html`<pre key=${"c" + i} class="md-pre"><code>${lines.slice(i + 1, end).join("\n")}</code></pre>`);
        i = end;
        continue;
      }
    }
    const head = /^(#{1,6})\s+(.*)$/.exec(line);
    if (head) {
      flushPara();
      out.push(html`<div key=${"h" + i} class=${"md-h md-h" + head[1].length} role="heading" aria-level=${head[1].length}>${inline(head[2], rules, "h" + i)}</div>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      flushPara();
      const quote = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { quote.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; }
      i -= 1;
      out.push(html`<blockquote key=${"q" + i} class="md-quote">${blocks(quote.join("\n"), rules)}</blockquote>`);
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); out.push(html`<hr key=${"r" + i} class="md-hr" />`); continue; }
    const item = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(line);
    if (item) {
      flushPara();
      const ordered = /\d/.test(item[1]);
      const items = [];
      while (i < lines.length) {
        const m = /^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(lines[i]);
        if (!m || /\d/.test(m[1]) !== ordered) break;
        items.push(m[2]);
        i += 1;
      }
      i -= 1;
      const List = ordered ? "ol" : "ul";
      out.push(html`<${List} key=${"l" + i} class="md-list">${items.map((t, k) => html`<li key=${k}>${inline(t, rules, "l" + i + k)}</li>`)}</${List}>`);
      continue;
    }
    para.push(line);
  }
  flushPara();
  return out;
}

// text -> elements; cached by text (and the marks), so long chats do not parse every message on every render
export function renderMarkdown(text, rules) {
  if (!text) return text;
  if (!/[*_`#>\[~\-!+]|\d[.)]|\n[ \t]*\n/.test(text)) return colorize(text, rules);
  const key = JSON.stringify(rules || []) + "\u0000" + text;
  let found = CACHE.get(key);
  if (!found) {
    found = blocks(text, rules);
    if (CACHE.size >= CACHE_MAX) CACHE.delete(CACHE.keys().next().value);
    CACHE.set(key, found);
  }
  return found;
}
