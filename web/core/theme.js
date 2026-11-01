// Applies a theme: its stylesheet, its light/dark base and, if the user allowed it, its effects
// script. A failing script is switched off and reported; the app itself keeps working.
// Safe mode (/?safe) skips all of this, so a broken theme can always be fixed from the UI.
//
// Effects script contract (see .claude/skills/make-theme/SKILL.md):
//   export default function setup(t) { ...; return () => { /* undo everything */ }; }
// where t = { id, scene, overlay, url(path), on(type, fn) -> off, reducedMotion, phone, signal }.
// Events: "chat" {chatId, characterId, characterName}, "generation" {running},
//         "message" {role, text}, "send" {text}.

export const SAFE_MODE = new URLSearchParams(location.search).has("safe");
const CACHE_KEY = "tavern.theme";
const bus = new EventTarget();
// State-like events are replayed to late subscribers: a script loads after the chat opened.
const STICKY = new Set(["chat", "generation"]);
const lastDetail = new Map();
let active = { key: null, cleanup: null, controller: null };

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

function setLook(id, base, hasCss) {
  const root = document.documentElement;
  root.dataset.theme = id;
  if (base === "light" || base === "dark") root.dataset.base = base; else delete root.dataset.base;
  const link = stylesheet();
  const href = hasCss ? `/themes/${id}/theme.css` : "";
  if (!href) link.removeAttribute("href");
  else if (link.getAttribute("href") !== href) link.setAttribute("href", href);
}

// Before the first API answer: reuse the last theme's look so the page does not flash.
export function applyCachedLook() {
  if (SAFE_MODE) return;
  try {
    const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    if (cached && /^[a-z0-9][a-z0-9_-]*$/.test(cached.id)) setLook(cached.id, cached.base, cached.hasCss);
  } catch (e) { /* storage off or junk: start with the base look */ }
}

function stopScript() {
  if (active.controller) active.controller.abort();
  const cleanup = active.cleanup;
  active.cleanup = null;
  active.controller = null;
  if (typeof cleanup === "function") {
    try { cleanup(); } catch (e) { console.error("Theme cleanup failed", e); }
  }
  for (const layer of document.querySelectorAll(".scene, .overlay-layer")) layer.replaceChildren();
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
    localStorage.setItem(CACHE_KEY, JSON.stringify({ id: theme.id, base: theme.base, hasCss: theme.has_css }));
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
    const cleanup = await module.default(api);
    if (active.controller !== controller) { // switched away while setup was running
      if (typeof cleanup === "function") cleanup();
      return;
    }
    active.cleanup = cleanup;
  } catch (error) {
    fail(error);
  }
}
