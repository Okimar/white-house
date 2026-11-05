// What a window shows until it has its data: «Загрузка…», or what went wrong with a way to try again.
// A failed first load must never leave «Загрузка…» on the screen for ever.
//   const { data, error, reload } = useLoad(() => api.get("/thing"), [id]);
//   if (!data) return html`<${Pending} error=${error} onRetry=${reload} />`;
import { html, useCallback, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { Icon } from "./Icon.js";

export function useLoad(load, deps = []) {
  const [state, setState] = useState({ data: null, error: "", loading: true });
  const latest = useRef(0);
  const reload = useCallback(() => {
    const ticket = ++latest.current;
    setState((s) => ({ ...s, error: "", loading: true }));
    return Promise.resolve().then(load).then((data) => {
      if (ticket === latest.current) setState({ data, error: "", loading: false });
      return data;
    }, (e) => {
      if (ticket === latest.current) setState((s) => ({ ...s, error: e.message, loading: false }));
      return null;
    });
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { ...state, reload };
}

export function Pending({ error, onRetry, text = "Загрузка…" }) {
  if (!error) return html`<p class="muted state-note" role="status">${text}</p>`;
  return html`<div class="state-note state-error" role="alert">
    <${Icon} name="triangle-alert" />
    <span class="grow-text">${error}</span>
    ${onRetry && html`<button type="button" onClick=${onRetry}>Повторить</button>`}
  </div>`;
}

// Nothing here yet: what this place is for and, when there is one, the next step.
export function EmptyNote({ title, hint, children }) {
  return html`<div class="empty-note">
    ${title && html`<p class="empty-title">${title}</p>`}
    ${hint && html`<p class="muted">${hint}</p>`}
    ${children}
  </div>`;
}
