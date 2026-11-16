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
  if (!href) {
    link.disabled = true;
    link.removeAttribute("href");
  } else if (link.getAttribute("href") !== href) {
    link.disabled = false;
    link.setAttribute("href", href);
  }
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
export async function applyTheme(theme, opts = {}) {
  const { runScript = false, onScriptError } = typeof opts === "object" && opts !== null ? opts : {};
  if (typeof theme === "string") {
    // If called with a string id directly (e.g. from debug/console or test scripts)
    const hasCss = theme !== "default" && theme !== "light" && theme !== "dark";
    theme = { id: theme, base: theme === "dark" ? "dark" : theme === "light" ? "light" : "auto", has_css: hasCss, builtin: true };
  }
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
    const rawMsg = error && (typeof error === "string" ? error : error.message) || "";
    const shortMsg = rawMsg ? String(rawMsg).split("\n")[0].slice(0, 150) : "Непредвиденный сбой";
    if (typeof onScriptError === "function") {
      onScriptError(`Скрипт эффектов темы «${theme.name}» завершился с ошибкой и был отключён: ${shortMsg}. Сама тема продолжает работать.`);
    }
  };
  const scene = document.querySelector(".scene");
  const overlay = document.querySelector(".overlay-layer");
  const fromTheme = (stack, file) => String(stack || file || "").includes(`/themes/${theme.id}/`);
  const onError = (e) => { if (fromTheme(e.error && e.error.stack, e.filename)) fail(e.error || e.message); };
  const onRejection = (e) => { if (fromTheme(e.reason && e.reason.stack)) fail(e.reason); };
  window.addEventListener("error", onError, { signal: controller.signal });
  window.addEventListener("unhandledrejection", onRejection, { signal: controller.signal });

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
    get reducedMotion() { return matchMedia("(prefers-reduced-motion: reduce)").matches; },
    get phone() { return matchMedia("(max-width: 720px)").matches; },
    get coarse() { return matchMedia("(pointer: coarse)").matches; },
    get dark() { return theme.base === "dark" || (theme.base !== "light" && matchMedia("(prefers-color-scheme: dark)").matches); },
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

const BAR = "display:flex;flex-wrap:wrap;gap:10px;align-items:center;"
  + "padding:10px max(14px,env(safe-area-inset-right)) 10px max(14px,env(safe-area-inset-left));"
  + "background:color-mix(in srgb,var(--warning,#e5a855) 12%,var(--panel,#1b1917));"
  + "color:var(--fg,#f2ece2);border-bottom:1px solid color-mix(in srgb,var(--warning,#e5a855) 30%,var(--line,#2e2b26));"
  + "font:500 var(--text-sm,14px)/1.45 var(--font,system-ui,sans-serif);"
  + "overflow-wrap:anywhere;word-break:break-word;text-wrap:pretty;";
const BTN_BASE = "padding:6px 14px;min-height:var(--tap,44px);border-radius:var(--radius-sm,8px);"
  + "font:500 var(--text-sm,14px)/1.3 var(--font,system-ui,sans-serif);cursor:pointer;"
  + "display:inline-flex;align-items:center;justify-content:center;touch-action:manipulation;"
  + "transition:transform 80ms var(--ease-out,cubic-bezier(.23,1,.32,1)),background-color 120ms var(--ease-out,cubic-bezier(.23,1,.32,1)),border-color 120ms var(--ease-out,cubic-bezier(.23,1,.32,1)),box-shadow 120ms var(--ease-out,cubic-bezier(.23,1,.32,1));";
const BTN = BTN_BASE
  + "border:1px solid color-mix(in srgb,var(--fg,#f2ece2) 20%,transparent);"
  + "background:color-mix(in srgb,var(--panel,#1b1917) 80%,transparent);color:var(--fg,#f2ece2);";
const BTN_PRIMARY = BTN_BASE
  + "border:1px solid var(--accent,#96570f);background:var(--accent,#96570f);color:var(--accent-fg,#fff);font-weight:600;";

function el(tag, style, text, children = []) {
  const node = document.createElement(tag);
  if (style) node.style.cssText = style;
  if (text) node.textContent = text;
  for (const child of children) node.appendChild(child);
  return node;
}

function button(label, onClick, primary = false) {
  const node = el("button", primary ? BTN_PRIMARY : BTN, label);
  node.type = "button";
  node.addEventListener("click", onClick);
  node.addEventListener("pointerdown", () => {
    if (!node.disabled && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      node.style.transform = "scale(0.97)";
    }
  });
  node.addEventListener("pointerup", () => { node.style.transform = ""; });
  node.addEventListener("pointercancel", () => { node.style.transform = ""; });
  node.addEventListener("mouseenter", () => {
    if (!node.disabled) {
      node.style.background = primary
        ? "var(--accent-hover, #7e480a)"
        : "color-mix(in srgb,var(--fg,#f2ece2) 12%,var(--panel,#1b1917))";
    }
  });
  node.addEventListener("mouseleave", () => {
    node.style.background = primary
      ? "var(--accent,#96570f)"
      : "color-mix(in srgb,var(--panel,#1b1917) 80%,transparent)";
  });
  node.addEventListener("focus", () => {
    node.style.outline = "var(--focus-ring, 2px solid var(--accent))";
    node.style.outlineOffset = "2px";
  });
  node.addEventListener("blur", () => {
    node.style.outline = "";
    node.style.outlineOffset = "";
  });
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
  bar.setAttribute("aria-live", "polite");
  bar.dataset.slot = "safe-mode-bar";
  const how = [];
  if (LOOK.safe_persistent) how.push("Включён постоянный режим (`python -m tavern safe-mode on`).");
  if (LOOK.safe_run) how.push("Включён на текущий запуск (`--safe`): перезапустите программу без этого флага.");
  if (SAFE_IN_ADDRESS) how.push("Включён адресом с параметром `?safe`.");
  const badge = el("span", "display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;font-size:var(--text-xs,12px);font-weight:700;letter-spacing:0.02em;background:color-mix(in srgb,var(--warning,#e5a855) 25%,transparent);color:var(--fg,#f2ece2);border:1px solid color-mix(in srgb,var(--warning,#e5a855) 40%,transparent);margin-right:4px;", "Безопасный режим");
  const head = el("div", "display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-weight:600;", "", [badge, el("span", "", "Сторонние темы, стили и эффекты отключены.")]);
  bar.appendChild(head);
  if (how.length) bar.appendChild(el("span", "opacity:.85;font-size:var(--text-xs,12px);", how.join(" ")));
  if (LOOK.safe_persistent || SAFE_IN_ADDRESS) {
    bar.appendChild(button("Выйти из безопасного режима", async (e) => {
      const btn = e && e.currentTarget ? e.currentTarget : null;
      if (btn) { btn.disabled = true; btn.textContent = "Выход…"; }
      try { if (LOOK.safe_persistent) await post("/api/look/safe", { on: false }); } catch (err) { /* try anyway */ }
      location.href = "/";
    }, true));
  } else {
    bar.appendChild(el("span", "opacity:.85;font-size:var(--text-xs,12px);", "Для выхода закройте программу и запустите её снова без флага `--safe`."));
  }
  return bar;
}

let parkedNode = null; // the notice on screen, so syncLookNotice can take it away or change its words
let noticeBox = null;

function parkedBar() {
  const bar = el("div", BAR);
  bar.setAttribute("role", "alert");
  bar.setAttribute("aria-live", "assertive");
  bar.dataset.slot = "look-parked-bar";
  const badge = el("span", "display:inline-flex;align-items:center;padding:2px 8px;border-radius:999px;font-size:var(--text-xs,12px);font-weight:700;letter-spacing:0.02em;background:color-mix(in srgb,var(--warning,#e5a855) 25%,transparent);color:var(--fg,#f2ece2);border:1px solid color-mix(in srgb,var(--warning,#e5a855) 40%,transparent);margin-right:4px;", "Обновление");
  const text = el("span", "", parkedText(LOOK.parked));
  text.dataset.part = "text";
  const head = el("div", "display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex:1;min-width:200px;", "", [badge, text]);
  bar.appendChild(head);
  const actions = el("span", "display:flex;flex-wrap:wrap;gap:8px;align-items:center;");
  const warning = el("div", "flex-basis:100%;display:none;opacity:.9;padding:6px 10px;border-radius:6px;background:color-mix(in srgb,var(--fg,#f2ece2) 6%,transparent);font-size:var(--text-xs,12px);line-height:1.4;",
    "Прежнее оформление создавалось под старый интерфейс и может нарушить отображение меню. "
    + "Если это произойдёт, откройте безопасный режим (адрес с ?safe или команда `python -m tavern safe-mode on`) "
    + "и смените тему в «Настройки → Оформление».");
  const confirm = button("Всё равно вернуть", async () => {
    confirm.disabled = true;
    confirm.textContent = "Восстановление…";
    try {
      await post("/api/look/restore");
      location.reload();
    } catch (err) {
      confirm.disabled = false;
      confirm.textContent = "Не удалось восстановить. Попробуйте ещё раз";
    }
  }, true);
  confirm.style.display = "none";
  const ask = button("Вернуть прежний вид", () => {
    warning.style.display = "block";
    confirm.style.display = "";
    ask.style.display = "none";
  });
  const ok = button("Понятно", async () => {
    ok.disabled = true;
    ok.textContent = "Закрытие…";
    try { await post("/api/look/dismiss"); } catch (e) { /* the notice may come back once more */ }
    dropParked();
  });
  actions.append(ask, confirm, ok);
  bar.append(actions, warning);
  parkedNode = bar;
  return bar;
}

function dropParked() {
  if (!parkedNode) return;
  const node = parkedNode;
  parkedNode = null;
  const prefersReducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (prefersReducedMotion || !node.isConnected) {
    node.remove();
    const remainingHeight = (noticeBox && noticeBox.isConnected && noticeBox.children.length) ? noticeBox.offsetHeight : 0;
    if (!remainingHeight && noticeBox) {
      noticeBox.remove();
      noticeBox = null;
    }
    document.documentElement.style.setProperty("--notice-h", remainingHeight + "px");
    return;
  }
  node.style.overflow = "hidden";
  node.style.maxHeight = node.offsetHeight + "px";
  node.style.transition = "max-height 220ms ease, opacity 180ms ease, padding 220ms ease, margin 220ms ease";
  requestAnimationFrame(() => {
    node.style.maxHeight = "0px";
    node.style.opacity = "0";
    node.style.paddingTop = "0px";
    node.style.paddingBottom = "0px";
    node.style.marginTop = "0px";
    node.style.marginBottom = "0px";
  });
  setTimeout(() => {
    if (node.isConnected) node.remove();
    const remainingHeight = (noticeBox && noticeBox.isConnected && noticeBox.children.length) ? noticeBox.offsetHeight : 0;
    if (!remainingHeight && noticeBox) {
      noticeBox.remove();
      noticeBox = null;
    }
    document.documentElement.style.setProperty("--notice-h", remainingHeight + "px");
  }, 240);
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
  const box = el("aside", "position:fixed;top:0;left:0;right:0;z-index:15");
  box.setAttribute("aria-label", "Системные уведомления оформления");
  box.dataset.slot = "look-notices";
  noticeBox = box;
  for (const bar of bars) box.appendChild(bar);
  document.body.appendChild(box);
  // The bar must not cover the menu: the page is pushed down by its height (styles.css uses --notice-h).
  const sync = () => {
    const h = (box && box.isConnected) ? box.offsetHeight : 0;
    document.documentElement.style.setProperty("--notice-h", h + "px");
  };
  sync();
  if (window.ResizeObserver) new ResizeObserver(sync).observe(box);
}
mountNotices();
