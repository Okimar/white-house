// How many of a message's notes the column beside it shows before «Все заметки» (setting notes_shown,
// 1..20, default 2). One value for the whole page: the line under the notes and Settings > Основное edit
// the same number, and every message follows at once.
import { useEffect, useState } from "../vendor/preact-htm.mjs";
import { api } from "./api.js";

export const DEFAULT_SHOWN = 2;
export const MAX_SHOWN = 20;

let value = DEFAULT_SHOWN;
let loading = null;
const listeners = new Set();

function publish(n) {
  value = n;
  listeners.forEach((fn) => fn(n));
}

function clean(n) {
  return Number.isInteger(n) && n >= 1 && n <= MAX_SHOWN ? n : DEFAULT_SHOWN;
}

export function syncNotesShown(n) {
  if (clean(n) !== value) publish(clean(n));
}

export function setNotesShown(n) {
  const next = Math.max(1, Math.min(MAX_SHOWN, Math.round(Number(n)) || DEFAULT_SHOWN));
  const before = value;
  publish(next);
  return api.put("/settings", { notes_shown: next }).catch((e) => { publish(before); throw e; });
}

export function useNotesShown() {
  const [n, setN] = useState(value);
  useEffect(() => {
    listeners.add(setN);
    setN(value);
    loading ||= api.get("/settings").then((s) => syncNotesShown(s.notes_shown)).catch(() => {});
    return () => listeners.delete(setN);
  }, []);
  return n;
}
