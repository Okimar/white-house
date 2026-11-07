// What was picked inside a Settings tab (the open connection, preset, skill, template), kept on this device so
// Settings reopens where it was closed. A sibling of `tavern.settingsView` (Settings.js rewrites that whole key).
// Only a convenience: every access is guarded, and without storage the choice lasts until the page closes.
const KEY = "tavern.settingsChoice";
const memory = {};

function all() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? { ...memory, ...v } : { ...memory };
  } catch (e) { return { ...memory }; }
}

export function getChoice(name) {
  const v = all()[name];
  return typeof v === "string" && v ? v : null;
}

export function setChoice(name, id) {
  if (id) memory[name] = id; else delete memory[name];
  const next = all();
  if (id) next[name] = id; else delete next[name];
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch (e) { /* storage off */ }
}
