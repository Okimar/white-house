// Web parts of plugins. Every active plugin with "web" in its plugin.json is imported once and its
//   export default function setup(t) { ...; return () => { /* undo */ }; }
// gets `t` (see .claude/skills/create-plugin/SKILL.md):
//   t.id, t.name, t.settings (current values), t.signal (aborted when the plugin is switched off)
//   t.call(action, payload) -> the server part's api.action result
//   t.command("/roll 2d6") runs a chat command in the open chat; t.chatId() the open chat
//   t.on(type, fn): "chat" {chatId,...}, "generation" {running}, "message" {role, text}, "send" {text},
//     "trace" {kind, chatId, ...} (progress its reply writer reports with job.run.trace),
//     "assistant_message" {id, text} (the assistant finished a reply)
//   t.onNotice(name, fn): what the server part sends with api.notify(name, **data)
//   t.messageButton({icon, title, onClick(message)}), t.messageFooter(message => text | null),
//   t.composerButton({icon, title, onClick(), show}): places in the chat; t.refresh() redraws them;
//     t.assistantButton(...) the same beside the assistant's box; icon, title and show may be functions
//     (called on every redraw); show() === false hides the button
//   t.insertText(text, {target: "chat" | "assistant", send}): puts text into that box (send: and sends it)
//   t.reloadSettings(): reads this plugin's settings again (after the server part changed them)
//   t.messageTree(message => tree | null): a folded tree under the message;
//   t.panel({icon, title, render: () => tree | null}) -> {open(), close()}: a side panel with a
//     button in the top bar. tree = {title, open, items: [{title, text, state, children}]},
//     state "wait" | "done" | "error" | ""; everything is plain text, never HTML
//   t.composerHelper({onText(text, caret), view()}): a strip above the message box. onText runs on
//     every change of the typed text (it never delays sending); view() returns null or
//     {note, items: [{label, title, options: [{label, onClick}]}], undo: {label, onClick}} (plain text);
//     t.composer.text() / .caret() / .replace([{start, end, text}], caret?) read and edit the box
//     (the caret stays where it was unless `caret` is given); call t.refresh() to redraw the strip
//   t.window({icon, title, render(ui), button}) -> {open(), close()}: a window of the plugin (a full-height sheet on a
//     phone). `render(ui)` runs as a component: it may call ui.useState / ui.useEffect and returns ui.html`...`;
//     ui = {html, useState, useEffect, useRef, Select, Icon, IconButton, ask, askText, toast, close}, the page's own
//     parts, so the plugin brings none. `button: false` leaves out the top-bar button (open it from your own place)
//   t.messageDivider(message => {label, title, state, onClick} | null): a thin line ABOVE a message (plain text)
//   t.messageMenu({label, icon, onClick(message), show(message)}): an item in a message's «⋯» menu
//   t.status(() => {text, title, onClick, state} | null): a small chip in the top bar (progress); state "wait" |
//     "done" | "error" | ""; call t.refresh() to redraw it. t.on("job", fn): progress of this app's background jobs
//     {owner, key, running, paused, done, total, note, error}
//   t.toast(text), t.reloadChat(), t.url(path) -> this plugin's file
// A plugin's style.css is added while it is on. Errors are caught and reported with the plugin's
// name; a failing setup leaves that plugin's web part off. Safe mode (/?safe) loads none of them.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { api } from "./api.js";
import { Select } from "../components/Select.js";
import { Icon, IconButton } from "../components/Icon.js";
import { ask, askText } from "../components/Dialogs.js";
import { notify } from "./notify.js";
import { SAFE_MODE, on as onEvent } from "./theme.js";

const ui = { messageButtons: [], messageFooters: [], composerButtons: [], composerHelpers: [], assistantButtons: [], messageTrees: [], panels: [],
  windows: [], messageDividers: [], messageMenus: [], statuses: [] };
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

// The message box registers itself here so plugins can read and edit what is typed.
let composerBridge = null;
export function setComposerBridge(bridge) { composerBridge = bridge; }

// A message box takes text a plugin hands over (t.insertText): the chat composer and the assistant's box.
export function useTextInput(target, fn) {
  useEffect(() => {
    inputs.set(target, fn);
    return () => { if (inputs.get(target) === fn) inputs.delete(target); };
  });
}

// Props of a plugin button that may be a function (icon, title, show).
export const resolve = (value) => (typeof value === "function" ? value() : value);

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
    insertText: (text, { target = "chat", send = false } = {}) => {
      const fn = inputs.get(target);
      if (!fn) throw new Error(target === "assistant" ? "Окно помощника закрыто" : "Поле ввода сейчас недоступно");
      fn(String(text), Boolean(send));
    },
    reloadSettings: async () => { current.set(p.id, await api.get(`/plugins/${p.id}`)); },
    url: (path) => `/plugins/${p.id}/${path}`,
    on: (type, fn) => onEvent(type, guard(fn), controller.signal),
    onNotice: (name, fn) => {
      noticeHandlers.set(p.id, [...(noticeHandlers.get(p.id) || []), { name, fn: guard(fn) }]);
    },
    messageButton: ({ icon, title, onClick }) => add("messageButtons", { icon, title, onClick: guard(onClick) }),
    messageFooter: (fn) => add("messageFooters", { fn: guard(fn) }),
    composerButton: ({ icon, title, onClick, show }) =>
      add("composerButtons", { icon, title, show, onClick: guard(onClick) }),
    assistantButton: ({ icon, title, onClick, show }) =>
      add("assistantButtons", { icon, title, show, onClick: guard(onClick) }),
    composerHelper: ({ onText, view }) => add("composerHelpers", { onText: guard(onText || (() => {})), view: guard(view) }),
    composer: {
      text: () => (composerBridge ? composerBridge.text() : ""),
      caret: () => (composerBridge ? composerBridge.caret() : 0),
      replace: (edits, caret) => (composerBridge ? composerBridge.replace(edits, caret) : false),
    },
    messageTree: (fn) => add("messageTrees", { fn: guard(fn) }),
    messageDivider: (fn) => add("messageDividers", { fn: guard(fn) }),
    messageMenu: ({ label, icon, onClick, show }) =>
      add("messageMenus", { label, icon, show: show ? guard(show) : null, onClick: guard(onClick) }),
    status: (fn) => add("statuses", { fn: guard(fn) }),
    window: ({ icon, title, render, button = true }) => {
      const win = { icon, title, button, render: guard(render), shown: false, key: p.id + ":w" + ui.windows.length,
        ui: { html, useState, useEffect, useRef, Select, Icon, IconButton, ask, askText, toast: (text, kind) => notify(text, kind) } };
      add("windows", win);
      return { open: () => setWindow(win.key, true), close: () => setWindow(win.key, false) };
    },
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

// Windows of plugins (t.window): shown by key.
export function setWindow(key, shown) {
  ui.windows = ui.windows.map((x) => (x.key === key ? { ...x, shown } : x));
  changed();
}

// Lines above a message: [{owner, label, title, state, onClick}].
export function messageDividers(message) {
  const out = [];
  for (const d of ui.messageDividers) {
    const item = d.fn(message);
    if (item) out.push({ owner: d.owner, ...item });
  }
  return out;
}

// Items plugins add to a message's «⋯» menu.
export function messageMenuItems(message) {
  return ui.messageMenus.filter((m) => !m.show || m.show(message)).map((m) => ({ ...m }));
}

// Chips for the top bar.
export function statusChips() {
  const out = [];
  for (const s of ui.statuses) {
    const item = s.fn();
    if (item) out.push({ owner: s.owner, ...item });
  }
  return out;
}
