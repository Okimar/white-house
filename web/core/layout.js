// Where the menu is (on top or at the side) and whether it and the side panels are folded.
// Kept on this device only (localStorage, every access guarded): a phone and a desktop want different
// things, and the page must work with storage off (then the choice lasts until the page is closed).
import { useState, useEffect } from "../vendor/preact-htm.mjs";

const KEYS = { mode: "tavern.menuMode", folded: "tavern.menuFolded", panels: "tavern.foldedPanels" };
const memory = {};
const listeners = new Set();

function read(key) {
  try { return localStorage.getItem(key); } catch (e) { return memory[key] ?? null; }
}
function write(key, value) {
  memory[key] = value;
  try { localStorage.setItem(key, value); } catch (e) { /* storage off */ }
  listeners.forEach((f) => f());
}

export const menuMode = () => (read(KEYS.mode) === "side" ? "side" : "top"); // top is the default
export const setMenuMode = (mode) => write(KEYS.mode, mode === "side" ? "side" : "top");
export const menuFolded = () => read(KEYS.folded) === "1";
export const setMenuFolded = (on) => write(KEYS.folded, on ? "1" : "0");

function foldedPanels() {
  try { const v = JSON.parse(read(KEYS.panels) || "{}"); return v && typeof v === "object" ? v : {}; } catch (e) { return {}; }
}
export const panelFolded = (key) => !!foldedPanels()[key];
export function setPanelFolded(key, on) {
  const all = foldedPanels();
  if (on) all[key] = 1; else delete all[key];
  write(KEYS.panels, JSON.stringify(all));
}

// Re-renders the component whenever any of the choices above change.
export function useLayout() {
  const [, bump] = useState(0);
  useEffect(() => {
    const f = () => bump((n) => n + 1);
    listeners.add(f);
    return () => listeners.delete(f);
  }, []);
  return { mode: menuMode(), folded: menuFolded() };
}
