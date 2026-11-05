// Closing what lies on top: Esc closes only the newest open window; a click on the dimmed area closes it
// only if the press also began there (a text selection dragged out of the window must not close it).
import { useEffect, useRef } from "../vendor/preact-htm.mjs";

const stack = [];

window.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !stack.length || e.defaultPrevented) return;
  e.preventDefault();
  stack[stack.length - 1].current();
});

// Esc calls `close` while this window is the newest one.
export function useEscape(close) {
  const ref = useRef(close);
  ref.current = close;
  useEffect(() => {
    stack.push(ref);
    return () => { stack.splice(stack.indexOf(ref), 1); };
  }, []);
}

// Props for the dimmed area: <div class="overlay" ...${backdrop(close)}>
export function backdrop(close) {
  let began = false;
  return {
    onPointerDown: (e) => { began = e.target === e.currentTarget; },
    onClick: (e) => { if (began && e.target === e.currentTarget) close(); began = false; },
  };
}
