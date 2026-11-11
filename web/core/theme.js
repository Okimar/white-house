// Applies a theme: its stylesheet, its light/dark base and, if the user allowed it, its effects
// script. A failing script is switched off and reported; the app itself keeps working.
// Safe mode skips all of this, so a broken theme can always be fixed from the UI. It is on with /?safe in
// the address, with `python -m tavern safe-mode on` (kept) or `python -m tavern --safe` (one run); the
// server tells which (GET /api/look). The same call says whether an update switched the old look off.
//
// Effects script contract (see .claude/skills/make-theme/SKILL.md):
//   export default function setup(t) { ...; return () => { /* undo everything */ }; }
// where t = { id, scene, overlay, url(path), on(type, fn) -> off, reducedMotion, phone, signal }.
// Events: "chat" {chatId, characterId, characterName}, "generation" {running},
//         "message" {role, text}, "send" {text}.

import { parkedShown, parkedText } from "./lookNotice.js";

async function fetchLook() {
  try {
    const reply = await fetch("/api/look", { credentials: "same-origin" });
    if (reply.ok) return await reply.json();
  } catch (e) { /* server too old or unreachable: the address alone decides */ }
  return {};
}
export const LOOK = await fetchLook();
const SAFE_IN_ADDRESS = new URLSearchParams(location.search).has("safe");
export const SAFE_MODE = SAFE_IN_ADDRESS || !!LOOK.safe_mode;
const CACHE_KEY = "tavern.theme";
const bus = new EventTarget();
// State-like events are replayed to late subscribers: a script loads after the chat opened.
const STICKY = new Set(["chat", "generation"]);
const lastDetail = new Map();
let active = { key: null, cleanup: null, controller: null, added: [], classes: [] };

export function emit(type, detail) {
  if (STICKY.has(type)) lastDetail.set(type, detail);
  bus.dispatchEvent(new CustomEvent(type, { detail }));
}

// The same events for plugins' web parts (core/plugins.js). State-like ones are replayed at once.
export function on(type, fn, signal) {
  const handler = (e) => fn(e.detail);
  bus.addEventListener(type, handler, { signal });
  if (lastDetail.has(type)) queueMicrotask(() => { if (!signal || !signal.aborted) fn(lastDetail.get(type)); });
  return () => bus.removeEventListener(type, handler);
}

function stylesheet() {
  let link = document.getElementById("theme-css");
  if (!link) {
    link = document.createElement("link");
    link.id = "theme-css";
    link.rel = "stylesheet";
    document.head.appendChild(link);
  }
  return link;
}

// After a theme's stylesheet was changed from the page (colour editor): the browser asks for it again.
let cssRev = 0;
export function reloadThemeCss() {
  cssRev += 1;
  const link = document.getElementById("theme-css");
  const href = link && link.getAttribute("href");
  if (href) link.setAttribute("href", href.split("?")[0] + `?v=${cssRev}`);
}

function setLook(id, base, hasCss) {
  const root = document.documentElement;
  root.dataset.theme = id;
  if (base === "light" || base === "dark") root.dataset.base = base; else delete root.dataset.base;
  const link = stylesheet();
  const href = hasCss ? `/themes/${id}/theme.css` + (cssRev ? `?v=${cssRev}` : "") : "";
  if (!href) link.removeAttribute("href");
  else if (link.getAttribute("href") !== href) link.setAttribute("href", href);
}

// Before the first API answer: reuse the last theme's look so the page does not flash.
export function applyCachedLook() {
  if (SAFE_MODE) return;
  try {
    const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    // Only a built-in look is reused before the answer: a user theme may have been switched off by an update.
    if (cached && cached.builtin && /^[a-z0-9][a-z0-9_-]*$/.test(cached.id)) setLook(cached.id, cached.base, cached.hasCss);
  } catch (e) { /* storage off or junk: start with the base look */ }
}

function stopScript() {
  if (active.controller) active.controller.abort();
  const cleanup = active.cleanup;
  const added = active.added;
  const classes = active.classes;
  active.cleanup = null;
  active.controller = null;
  active.added = [];
  active.classes = [];
  if (typeof cleanup === "function") {
    try { cleanup(); } catch (e) { console.error("Theme cleanup failed", e); }
  }
  for (const layer of document.querySelectorAll(".scene, .overlay-layer")) layer.replaceChildren();
  // What the script's setup put outside its two layers (a moon on <body>, a <style> in <head>, a class on
  // <html>) goes too, even when its own undo forgot it: a theme switched off must leave nothing on screen.
  for (const node of added) { if (node.isConnected) node.remove(); }
  for (const [target, name] of classes) target.classList.remove(name);
}

// The app's own nodes at the top of <body> are never a theme's, whenever they were made.
const APP_NODES = new Set(["root", "menu-host", "select-host", "suggest-host"]);
function topNodes() { return [...document.head.children, ...document.body.children]; }
function classesOf() { return [document.documentElement, document.body].map((t) => [t, new Set(t.classList)]); }
function addedSince(nodes, classes) {
  const added = topNodes().filter((n) => !nodes.has(n) && !APP_NODES.has(n.id)
    && !(n.dataset && n.dataset.slot === "look-notices"));
  const newClasses = [];
  for (const [target, had] of classes) {
    for (const name of target.classList) if (!had.has(name)) newClasses.push([target, name]);
  }
  return { added, newClasses };
}

// theme: summary from /api/themes. runScript: the user allowed this exact script (or built-in).
export async function applyTheme(theme, { runScript, onScriptError }) {
  if (SAFE_MODE || !theme || theme.error) {
    stopScript();
    setLook("default", "auto", false);
    active.key = null;
    return;
  }
  const key = `${theme.id}:${runScript ? theme.script_sha256 : "-"}`;
  setLook(theme.id, theme.base, theme.has_css);
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify({ id: theme.id, base: theme.base, hasCss: theme.has_css, builtin: !!theme.builtin }));
  } catch (e) { /* storage off */ }
  if (key === active.key) return;
  stopScript();
  active.key = key;
  if (!theme.script || !runScript) return;

  const controller = new AbortController();
  active.controller = controller;
  const fail = (error) => {
    if (active.controller !== controller) return;
    console.error("Theme script failed", error);
    stopScript();
    onScriptError(`Скрипт темы «${theme.name}» упал и выключен: ${error && error.message || error}`);
  };
  const scene = document.querySelector(".scene");
  const overlay = document.querySelector(".overlay-layer");
  const fromTheme = (stack, file) => String(stack || file || "").includes(`/themes/${theme.id}/`);
  const onError = (e) => { if (fromTheme(e.error && e.error.stack, e.filename)) fail(e.error || e.message); };
  const onRejection = (e) => { if (fromTheme(e.reason && e.reason.stack)) fail(e.reason); };
  window.addEventListener("error", onError, { signal: controller.signal });
  window.addEventListener("unhandledrejection", onRejection, { signal: controller.signal });

  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const api = {
    id: theme.id,
    scene,
    overlay,
    url: (path) => `/themes/${theme.id}/${path}`,
    on: (type, fn) => {
      const handler = (e) => { try { fn(e.detail); } catch (err) { fail(err); } };
      bus.addEventListener(type, handler, { signal: controller.signal });
      if (lastDetail.has(type)) {
        queueMicrotask(() => {
          if (!controller.signal.aborted) handler({ detail: lastDetail.get(type) });
        });
      }
      return () => bus.removeEventListener(type, handler);
    },
    reducedMotion: motion.matches,
    phone: matchMedia("(max-width: 720px)").matches,
    signal: controller.signal,
  };
  try {
    // The hash in the URL makes the browser load the new code after the script was edited.
    const module = await import(`/themes/${theme.id}/${theme.script}?v=${theme.script_sha256}`);
    if (active.controller !== controller) return; // another theme was applied meanwhile
    if (typeof module.default !== "function") throw new Error("script.js must export default function setup(t)");
    const nodesBefore = new Set(topNodes());
    const classesBefore = classesOf();
    let cleanup;
    try {
      cleanup = await module.default(api);
    } finally {
      // Taken also when setup failed half way: fail() -> stopScript() then removes what it managed to add.
      const { added, newClasses } = addedSince(nodesBefore, classesBefore);
      if (active.controller === controller) { active.added = added; active.classes = newClasses; }
      else { for (const n of added) n.remove(); for (const [t, c] of newClasses) t.classList.remove(c); }
    }
    if (active.controller !== controller) { // switched away while setup was running
      if (typeof cleanup === "function") cleanup();
      return;
    }
    active.cleanup = cleanup;
  } catch (error) {
    fail(error);
  }
}

// --- notices that must work even when the rest of the page does not -------------------------------------
// Plain DOM with inline styles: they do not depend on the app's own components or on any theme.

const BAR = "display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;"
  + "background:var(--panel,#1f1f24);color:var(--fg,#eee);border-bottom:2px solid var(--warning,#e0a030);"
  + "font:14px/1.4 system-ui,sans-serif;";
const BTN = "padding:6px 12px;min-height:36px;border-radius:6px;border:1px solid currentColor;"
  + "background:transparent;color:inherit;font:inherit;cursor:pointer;";

function el(tag, style, text, children = []) {
  const node = document.createElement(tag);
  if (style) node.style.cssText = style;
  if (text) node.textContent = text;
  for (const child of children) node.appendChild(child);
  return node;
}

function button(label, onClick) {
  const node = el("button", BTN, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

async function post(path, body) {
  const reply = await fetch(path, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  if (!reply.ok) throw new Error("HTTP " + reply.status);
}

function safeBar() {
  const bar = el("div", BAR);
  bar.setAttribute("role", "status");
  bar.dataset.slot = "safe-mode-bar";
  const how = [];
  if (LOOK.safe_persistent) how.push("Включён навсегда: команда `python -m tavern safe-mode on`.");
  if (LOOK.safe_run) how.push("Включён только на этот запуск (`--safe`): перезапустите без него.");
  if (SAFE_IN_ADDRESS) how.push("Включён адресом /?safe.");
  bar.appendChild(el("strong", "", "Безопасный режим: темы, свои стили и скрипты не загружаются."));
  bar.appendChild(el("span", "opacity:.85", how.join(" ")));
  if (LOOK.safe_persistent || SAFE_IN_ADDRESS) {
    bar.appendChild(button("Выйти из безопасного режима", async () => {
      try { if (LOOK.safe_persistent) await post("/api/look/safe", { on: false }); } catch (e) { /* try anyway */ }
      location.href = "/";
    }));
  } else {
    bar.appendChild(el("span", "opacity:.85", "Выход: закройте программу и запустите без `--safe`."));
  }
  return bar;
}

let parkedNode = null; // the notice on screen, so syncLookNotice can take it away or change its words
let noticeBox = null;

function parkedBar() {
  const bar = el("div", BAR);
  bar.setAttribute("role", "alert");
  bar.dataset.slot = "look-parked-bar";
  const text = el("span", "", parkedText(LOOK.parked));
  text.dataset.part = "text";
  bar.appendChild(text);
  const actions = el("span", "display:flex;flex-wrap:wrap;gap:8px");
  const warning = el("div", "flex-basis:100%;display:none;opacity:.9",
    "Внимание: прежнее оформление может сломать меню. Если так случится, откройте безопасный режим "
    + "(адрес /?safe или команда `python -m tavern safe-mode on`) и отключите оформление в «Настройки → Оформление».");
  const confirm = button("Всё равно вернуть", async () => {
    try { await post("/api/look/restore"); location.reload(); } catch (e) { confirm.textContent = "Не вышло, попробуйте ещё раз"; }
  });
  confirm.style.display = "none";
  const ask = button("Вернуть прежний вид", () => {
    warning.style.display = "block";
    confirm.style.display = "";
    ask.style.display = "none";
  });
  const ok = button("Понятно", async () => {
    try { await post("/api/look/dismiss"); } catch (e) { /* the notice may come back once more */ }
    dropParked();
  });
  actions.append(ask, confirm, ok);
  bar.append(actions, warning);
  parkedNode = bar;
  return bar;
}

function dropParked() {
  if (parkedNode) parkedNode.remove();
  parkedNode = null;
  if (noticeBox && !noticeBox.children.length) {
    noticeBox.remove();
    noticeBox = null;
    document.documentElement.style.setProperty("--notice-h", "0px");
  }
}

// The page's theme choice changed (picked in «Оформление», by the assistant, an undo): ask the server again,
// so the notice does not keep saying «отключено» about a look that is on now, nor «стандартный вид» about another.
export async function syncLookNotice() {
  if (!parkedNode) return;
  const fresh = await fetchLook();
  if (!fresh || !("parked" in fresh)) return; // no answer: leave the notice as it is
  LOOK.parked = fresh.parked;
  if (!parkedShown(fresh)) { dropParked(); return; }
  const text = parkedNode.querySelector('[data-part="text"]');
  if (text) text.textContent = parkedText(fresh.parked);
}

function mountNotices() {
  if (!document.body) return;
  const bars = [];
  if (SAFE_MODE) bars.push(safeBar());
  if (parkedShown(LOOK)) bars.push(parkedBar());
  if (!bars.length) return;
  const hideOld = document.createElement("style");
  hideOld.textContent = ".safe-banner{display:none!important}"; // this bar replaces the page's own safe-mode line
  document.head.appendChild(hideOld);
  const box = el("div", "position:fixed;top:0;left:0;right:0;z-index:100000");
  box.dataset.slot = "look-notices";
  noticeBox = box;
  for (const bar of bars) box.appendChild(bar);
  document.body.appendChild(box);
  // The bar must not cover the menu: the page is pushed down by its height (styles.css uses --notice-h).
  const sync = () => document.documentElement.style.setProperty("--notice-h", box.offsetHeight + "px");
  sync();
  if (window.ResizeObserver) new ResizeObserver(sync).observe(box);
}
mountNotices();
