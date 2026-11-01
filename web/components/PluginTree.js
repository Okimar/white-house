// Plugin places that show a tree: under a message (folded) and side panels with a top-bar button.
// The tree comes from a plugin's web part as plain data (core/plugins.js), never HTML:
//   {title, open, items: [{title, text, state: "wait" | "done" | "error" | "", children: [...]}]}
import { html } from "../vendor/preact-htm.mjs";
import { togglePanel } from "../core/plugins.js";

const MARK = { wait: "⏳", done: "✓", error: "⚠" };

function Item({ item }) {
  const children = item.children || [];
  return html`<li class="ptree-item" data-state=${item.state || ""}>
    <div class="ptree-head">${MARK[item.state] && html`<span class="ptree-mark">${MARK[item.state]}</span>`}
      <strong>${item.title}</strong></div>
    ${item.text && html`<div class="ptree-text">${item.text}</div>`}
    ${children.length > 0 && html`<ul class="ptree-list">${children.map((c, i) => html`<${Item} key=${i} item=${c} />`)}</ul>`}
  </li>`;
}

export function Tree({ tree }) {
  return html`<ul class="ptree-list">${(tree.items || []).map((item, i) => html`<${Item} key=${i} item=${item} />`)}</ul>`;
}

export function MessageTree({ owner, tree }) {
  return html`<details class="ptree msg-tree" data-slot="message-plugin-tree" data-plugin=${owner} open=${!!tree.open}>
    <summary>${tree.title}</summary><${Tree} tree=${tree} /></details>`;
}

// Buttons for the top bar: one per panel that has something to show.
export function PanelButtons({ panels }) {
  return panels.filter((p) => p.render()).map((p) => html`<button key=${p.key} data-slot="plugin-panel-button"
    data-plugin=${p.owner} aria-pressed=${p.shown} title=${p.title}
    onClick=${() => togglePanel(p.key, !p.shown)}>${p.icon}</button>`);
}

export function Panels({ panels }) {
  return panels.filter((p) => p.shown).map((p) => {
    const tree = p.render();
    return html`<aside key=${p.key} class="plugin-panel" data-slot="plugin-panel" data-plugin=${p.owner}>
      <header><strong>${p.icon} ${(tree && tree.title) || p.title}</strong><span class="spacer"></span>
        <button title="Закрыть" onClick=${() => togglePanel(p.key, false)}>✕</button></header>
      ${tree ? html`<${Tree} tree=${tree} />` : html`<p class="muted">Пока пусто.</p>`}
    </aside>`;
  });
}
