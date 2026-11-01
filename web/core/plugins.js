// Web parts of plugins. Every active plugin with "web" in its plugin.json is imported once and its
//   export default function setup(t) { ...; return () => { /* undo */ }; }
// gets `t` (see .claude/skills/create-plugin/SKILL.md):
//   t.id, t.name, t.settings (current values), t.signal (aborted when the plugin is switched off)
//   t.call(action, payload) -> the server part's api.action result
//   t.command("/roll 2d6") runs a chat command in the open chat; t.chatId() the open chat
//   t.on(type, fn): "chat" {chatId,...}, "generation" {running}, "message" {role, text}, "send" {text},
//     "trace" {kind, chatId, ...} (progress its reply writer reports with job.run.trace)
//   t.onNotice(name, fn): what the server part sends with api.notify(name, **data)
//   t.messageButton({icon, title, onClick(message)}), t.messageFooter(message => text | null),
//   t.composerButton({icon, title, onClick()}): places in the chat; t.refresh() redraws them
//   t.messageTree(message => tree | null): a folded tree under the message;
//   t.panel({icon, title, render: () => tree | null}) -> {open(), close()}: a side panel with a
//     button in the top bar. tree = {title, open, items: [{title, text, state, children}]},
//     state "wait" | "done" | "error" | ""; everything is plain text, never HTML
//   t.toast(text), t.reloadChat(), t.url(path) -> this plugin's file
// A plugin's style.css is added while it is on. Errors are caught and reported with the plugin's
// name; a failing setup leaves that plugin's web part off. Safe mode (/?safe) loads none of them.
import { useEffect, useState } from "../vendor/preact-htm.mjs";
import { api } from "./api.js";
import { SAFE_MODE, on as onEvent } from "./theme.js";

const ui = { messageButtons: [], messageFooters: [], composerButtons: [], messageTrees: [], panels: [] };
const listeners = new Set();
const loaded = new Map(); // id -> {hash, controller, cleanup}
const current = new Map(); // id -> plugin summary from /api/plugins (fresh settings)
const noticeHandlers = new Map(); // plugin id -> [{name, fn}]
let env = { toast: () => {}, command: async () => {}, chatId: () => null, reloadChat: () => {} };

function changed() {
  for (const fn of listeners) fn((n) => n + 1);
}

// Components that draw plugin places re-render when a plugin adds, removes or refreshes them.
export function usePluginUi() {
  const [, set] = useState(0);
  useEffect(() => { listeners.add(set); return () => listeners.delete(set); }, []);
  return ui;
}

export function setPluginEnv(next) { env = { ...env, ...next }; }

// A notice from /api/events of type "plugin": hand it to that plugin's web part.
export function pluginNotice(event) {
  for (const h of noticeHandlers.get(event.plugin) || []) if (h.name === event.name) h.fn(event.data || {});
}

function unload(id) {
  const entry = loaded.get(id);
  loaded.delete(id);
  noticeHandlers.delete(id);
  if (entry) {
    entry.controller.abort();
    try { if (typeof entry.cleanup === "function") entry.cleanup(); } catch (e) { console.error(e); }
  }
  for (const key of Object.keys(ui)) ui[key] = ui[key].filter((item) => item.owner !== id);
  const link = document.getElementById("plugin-css-" + id);
  if (link) link.remove();
  changed();
}

async function load(p) {
  const controller = new AbortController();
  const entry = { hash: p.hash, controller, cleanup: null };
  loaded.set(p.id, entry);
  const fail = (error) => {
    console.error(`Plugin ${p.id}`, error);
    env.toast(`Дополнение «${p.name}»: ${(error && error.message) || error}`);
  };
  const guard = (fn) => (...args) => {
    try {
      const result = fn(...args);
      if (result && typeof result.catch === "function") return result.catch(fail);
      return result;
    } catch (e) { fail(e); return null; }
  };
  if (p.style) {
    const link = document.createElement("link");
    link.id = "plugin-css-" + p.id;
    link.rel = "stylesheet";
    link.href = `/plugins/${p.id}/${p.style}?v=${p.hash}`;
    document.head.appendChild(link);
  }
  if (!p.web) return;
  const add = (place, item) => { ui[place] = [...ui[place], { ...item, owner: p.id }]; changed(); };
  const t = {
    id: p.id,
    name: p.name,
    get settings() { return (current.get(p.id) || p).values || {}; },
    signal: controller.signal,
    call: async (name, payload = {}) => (await api.post(`/plugins/${p.id}/actions/${name}`, payload)).result,
    command: (text) => env.command(text),
    chatId: () => env.chatId(),
    toast: (text) => env.toast(text),
    refresh: changed,
    reloadChat: () => env.reloadChat(),
    url: (path) => `/plugins/${p.id}/${path}`,
    on: (type, fn) => onEvent(type, guard(fn), controller.signal),
    onNotice: (name, fn) => {
      noticeHandlers.set(p.id, [...(noticeHandlers.get(p.id) || []), { name, fn: guard(fn) }]);
    },
    messageButton: ({ icon, title, onClick }) => add("messageButtons", { icon, title, onClick: guard(onClick) }),
    messageFooter: (fn) => add("messageFooters", { fn: guard(fn) }),
    composerButton: ({ icon, title, onClick }) => add("composerButtons", { icon, title, onClick: guard(onClick) }),
    messageTree: (fn) => add("messageTrees", { fn: guard(fn) }),
    panel: ({ icon, title, render }) => {
      const panel = { icon, title, render: guard(render), shown: false, key: p.id + ":" + ui.panels.length };
      add("panels", panel);
      const set = (shown) => {
        ui.panels = ui.panels.map((x) => (x.key === panel.key ? { ...x, shown } : x));
        changed();
      };
      return { open: () => set(true), close: () => set(false) };
    },
  };
  try {
    const module = await import(`/plugins/${p.id}/${p.web}?v=${p.hash}`);
    if (loaded.get(p.id) !== entry) return; // switched off meanwhile
    if (typeof module.default !== "function") throw new Error("web-часть должна экспортировать setup(t)");
    entry.cleanup = await module.default(t);
  } catch (error) {
    fail(error);
    unload(p.id);
  }
}

// Bring the loaded web parts in line with the list from /api/plugins.
export async function syncPlugins(plugins) {
  current.clear();
  for (const p of plugins) current.set(p.id, p);
  if (SAFE_MODE) return;
  const active = new Map(plugins.filter((p) => p.active && (p.web || p.style)).map((p) => [p.id, p]));
  for (const [id, entry] of [...loaded]) {
    if (!active.has(id) || active.get(id).hash !== entry.hash) unload(id);
  }
  for (const p of active.values()) if (!loaded.has(p.id)) await load(p);
  changed();
}

// Footer lines plugins add under a message (plain text).
export function messageExtras(message) {
  const lines = [];
  for (const f of ui.messageFooters) {
    const text = f.fn(message);
    if (text) lines.push({ owner: f.owner, text: String(text) });
  }
  return lines;
}

// Trees plugins show under a message (folded).
export function messageTrees(message) {
  const out = [];
  for (const f of ui.messageTrees) {
    const tree = f.fn(message);
    if (tree) out.push({ owner: f.owner, tree });
  }
  return out;
}

export function togglePanel(key, shown) {
  ui.panels = ui.panels.map((x) => (x.key === key ? { ...x, shown } : x));
  changed();
}
