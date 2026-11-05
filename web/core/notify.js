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

export function notify(text, kind = "info") {
  clearTimeout(timer);
  listener({ text, kind });
  timer = setTimeout(() => listener(null), LASTS[kind] || LASTS.info);
}
