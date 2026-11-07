// Closing what lies on top: Esc closes only the newest open window; a click on the dimmed area closes it
// only if the press also began there (a text selection dragged out of the window must not close it).
import { useEffect, useRef } from "../vendor/preact-htm.mjs";

const stack = [];

window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !stack.length || e.defaultPrevented) return;
  e.preventDefault();
  stack[stack.length - 1].current();
});

// Esc and the browser's Back button call `close` while this window is the newest one. Each open window
// owns one history entry (`wdepth`): Back drops the entry and closes the newest window, a window closed
// by its own button takes its entry away again.
export function useEscape(close) {
  const ref = useRef(close);
  ref.current = close;
  useEffect(() => {
    stack.push(ref);
    const depth = stack.length;
    ref.depth = depth;
    try { history.pushState({ ...(history.state || {}), wdepth: depth }, ""); } catch (e) { /* no history */ }
    return () => {
      const at = stack.indexOf(ref);
      if (at < 0) return; // already removed by Back
      stack.splice(at, 1);
      try { if (history.state && history.state.wdepth === depth) history.back(); } catch (e) { /* no history */ }
    };
  }, []);
}

window.addEventListener("popstate", (e) => {
  const now = (e.state && e.state.wdepth) || 0;
  while (stack.length && stack[stack.length - 1].depth > now) {
    const top = stack.pop();
    top.current();
  }
});

// Props for the dimmed area: <div class="overlay" ...${backdrop(close)}>
export function backdrop(close) {
  let began = false;
  return {
    onPointerDown: (e) => { began = e.target === e.currentTarget; },
    onClick: (e) => { if (began && e.target === e.currentTarget) close(); began = false; },
  };
}
