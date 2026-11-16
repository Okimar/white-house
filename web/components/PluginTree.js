// Plugin places that show a tree: under a message (folded) and side panels with a top-bar button.
// The tree comes from a plugin's web part as plain data (core/plugins.js), never HTML:
//   {title, open, items: [{title, text, state: "wait" | "done" | "error" | "", children: [...]}]}
import { html } from "../vendor/preact-htm.mjs";
import { togglePanel } from "../core/plugins.js";
import { Icon, IconButton } from "./Icon.js";
import { panelFolded, setPanelFolded, useLayout } from "../core/layout.js";

const MARK = { wait: "loader-circle", done: "check", error: "triangle-alert" };
const STATE_LABEL = { wait: "Выполняется…", done: "Готово", error: "Ошибка" };

function Item({ item }) {
  if (!item) return null;
  const children = Array.isArray(item.children) ? item.children : [];
  const stateLabel = STATE_LABEL[item.state] || item.state;
  return html`<li class="ptree-item" data-state=${item.state || ""}>
    <div class="ptree-head">${MARK[item.state] && html`<span class="ptree-mark" title=${stateLabel} aria-label=${stateLabel}><${Icon} name=${MARK[item.state]} /></span>`}
      <strong>${item.title}</strong></div>
    ${item.text && html`<div class="ptree-text">${item.text}</div>`}
    ${children.length > 0 && html`<ul class="ptree-list">${children.map((c, i) => html`<${Item} key=${c?.id || c?.title || i} item=${c} />`)}</ul>`}
  </li>`;
}

export function Tree({ tree }) {
  const items = Array.isArray(tree?.items) ? tree.items : [];
  return html`<ul class="ptree-list">${items.map((item, i) => html`<${Item} key=${item?.id || item?.title || i} item=${item} />`)}</ul>`;
}

export function MessageTree({ owner, tree }) {
  if (!tree) return null;
  return html`<details class="ptree msg-tree" data-slot="message-plugin-tree" data-plugin=${owner} open=${!!tree.open}>
    <summary>${tree.title}</summary><${Tree} tree=${tree} /></details>`;
}

// Buttons for the top bar: one per panel that has something to show.
export function PanelButtons({ panels }) {
  return panels.filter((p) => {
    try { return Boolean(p.render()); } catch { return false; }
  }).map((p) => html`<button key=${p.key} type="button" data-slot="plugin-panel-button"
    data-plugin=${p.owner} aria-pressed=${p.shown} title=${p.title} aria-label=${p.title}
    onClick=${() => togglePanel(p.key, !p.shown)}>${p.icon}</button>`);
}

// One side panel. It folds to a thin strip (a bar on a phone) and unfolds again; the choice is kept per panel.
function Panel({ panel: p }) {
  useLayout(); // re-render when the fold choice changes
  const folded = panelFolded(p.key);
  let tree = null;
  try {
    tree = p.render();
  } catch (err) {
    tree = { title: p.title, items: [{ title: "Ошибка", text: err.message, state: "error" }] };
  }
  const title = (tree && tree.title) || p.title;
  const hasItems = Array.isArray(tree?.items) && tree.items.length > 0;
  return html`<aside class=${"plugin-panel" + (folded ? " folded" : "")} data-slot="plugin-panel" data-plugin=${p.owner}
    data-folded=${folded ? "true" : "false"}>
    <header><${IconButton} icon=${folded ? "chevron-left" : "chevron-right"} class="fold-panel" data-slot="fold-panel"
        label=${folded ? "Развернуть панель" : "Свернуть панель"} aria-expanded=${!folded}
        onClick=${() => setPanelFolded(p.key, !folded)} />
      <strong>${p.icon} <span class="plugin-panel-title">${title}</span></strong><span class="spacer"></span>
      <${IconButton} icon="x" label="Закрыть" class="close-panel" onClick=${() => togglePanel(p.key, false)} /></header>
    ${!folded && (hasItems ? html`<${Tree} tree=${tree} />` : html`<div class="plugin-panel-empty muted" role="status"><${Icon} name="info" /><p>В этой панели пока нет данных.</p></div>`)}
  </aside>`;
}

export function Panels({ panels }) {
  return panels.filter((p) => p.shown).map((p) => html`<${Panel} key=${p.key} panel=${p} />`);
}
