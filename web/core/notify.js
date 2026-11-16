// Short messages at the edge of the screen. One at a time; kinds: "error" (red), "ok" and "info" (quiet).
// Any module may call notify(); the app shell shows the current one (subscribe).
const LASTS = { error: 8000, ok: 3500, info: 5000 };

let listener = () => {};
let timer = null;

export function subscribe(fn) {
  listener = fn;
  return () => { listener = () => {}; };
}

export function dismiss() {
  clearTimeout(timer);
  listener(null);
}

export function notify(text, kindOrOptions = "info") {
  clearTimeout(timer);
  const opts = typeof kindOrOptions === "string" ? { kind: kindOrOptions } : (kindOrOptions || {});
  const kind = opts.kind || "info";
  const action = opts.action || null;
  listener({ text, kind, action });
  const lasts = opts.timeout || (action ? 8000 : (LASTS[kind] || LASTS.info));
  timer = setTimeout(() => listener(null), lasts);
}
