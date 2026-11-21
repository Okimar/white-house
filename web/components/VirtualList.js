// Virtualized list with variable item heights, built for chat:
//  - only items near the viewport are in the DOM (long chats stay smooth);
//  - heights are measured with ResizeObserver, unmeasured items use an estimate;
//  - stays pinned to the bottom while new text streams in, unless the user scrolled up;
//  - when older items are prepended, the visible content does not jump.
// An item's identity is `item.vkey || item.id`: a temporary message (core/pending.js) and the real one that replaces
// it share the `vkey`, so the row keeps its node and measured height. `stickTo` (a vkey) scrolls to the bottom when it
// appears: the user just sent that message.
import { html, useState, useRef, useLayoutEffect, useEffect, useCallback } from "../vendor/preact-htm.mjs";
import { Icon } from "./Icon.js";

const ESTIMATE = 90;
const OVERSCAN_PX = 900;
const REDRAW_PX = 300; // the rendered window moves when the view moved this far (not on every scroll event)
const STICK_PX = 48;
const LOAD_MORE_PX = 400;
const JUMP_MS = 800;

const keyOf = (item) => item.vkey || item.id;

// `status`: a short line shown over the bottom of the list (never moving the rows), e.g. «Собираю запрос…».
export function VirtualList({ items, renderItem, onReachTop, resetKey, jumpTo, stickTo = null, layoutKey = null,
  status = "" }) {
  const box = useRef(null);
  const jump = useRef(null); // { id, until }: keep this item at the top while heights above are measured
  useLayoutEffect(() => {
    if (jumpTo) jump.current = { id: jumpTo.id, until: Date.now() + JUMP_MS };
  }, [jumpTo]);
  const heights = useRef(new Map()); // item key -> measured height
  const stuck = useRef(true); // pinned to the bottom
  const anchor = useRef(null); // { id, offset } of the first item before an update
  const [, redraw] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 600 });
  const viewportRef = useRef(viewport); // the last window drawn, read by onScroll
  const setViewportBoth = (v) => { viewportRef.current = v; setViewport(v); };
  const [showJump, setShowJump] = useState(false);
  const showJumpRef = useRef(false);

  const scrollToBottom = () => {
    const el = box.current;
    if (!el) return;
    stuck.current = true;
    jump.current = null;
    showJumpRef.current = false;
    setShowJump(false);
    el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  // The user's finger (or its inertia) owns the scroll position: code must not write scrollTop then,
  // or the browser drops the touch and the list "sticks" and has to be dragged very hard.
  const touching = useRef(false);
  const userUntil = useRef(0);
  const userScrolling = () => touching.current || Date.now() < userUntil.current;
  const markUser = (ms) => { userUntil.current = Date.now() + ms; };

  // The user just sent a message: show it even when the list was scrolled up. Declared before the layout effect
  // below, which then sees `stuck` and scrolls.
  useLayoutEffect(() => {
    if (stickTo) {
      stuck.current = true;
      jump.current = null;
      showJumpRef.current = false;
      setShowJump(false);
    }
  }, [stickTo]);

  // Unmeasured items are guessed from the measured ones, so corrections stay small.
  let sum = 0;
  for (const h of heights.current.values()) sum += h;
  const estimate = heights.current.size ? Math.min(400, Math.max(40, sum / heights.current.size)) : ESTIMATE;
  const heightOf = (id) => heights.current.get(id) ?? estimate;
  const offsets = [];
  let total = 0;
  for (const item of items) {
    offsets.push(total);
    total += heightOf(keyOf(item));
  }

  // Latest layout, readable from event handlers that outlive a single render.
  const layout = useRef(null);
  layout.current = { items, offsets, heightOf };
  const captureAnchor = (top) => {
    const { items: list, offsets: offs, heightOf: h } = layout.current;
    if (!list.length) return null;
    let low = 0;
    let high = list.length - 1;
    let first = -1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      if (offs[mid] + h(keyOf(list[mid])) > top) {
        first = mid;
        high = mid - 1;
      } else {
        low = mid + 1;
      }
    }
    return first >= 0 ? { id: keyOf(list[first]), offset: offs[first] - top } : null;
  };

  const observer = useRef(null);
  if (!observer.current && typeof ResizeObserver !== "undefined") {
    observer.current = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const id = entry.target.dataset.id;
        const h = Math.ceil(entry.target.getBoundingClientRect().height);
        if (h && heights.current.get(id) !== h) {
          heights.current.set(id, h);
          changed = true;
        }
      }
      if (changed) redraw((n) => n + 1);
    });
  }
  useEffect(() => () => observer.current && observer.current.disconnect(), []);

  // New chat: forget measurements and jump to the newest message.
  useLayoutEffect(() => {
    heights.current = new Map();
    stuck.current = true;
    anchor.current = null;
    showJumpRef.current = false;
    setShowJump(false);
  }, [resetKey]);

  // The look of the rows changed (chat view «Пузыри» / «Документ»): every cached height is stale, also those of rows
  // out of the window. Measure the rows in the DOM now (the new CSS is already applied) and drop the rest to the
  // estimate; the anchor below then keeps the first visible row where it was, and the scroll does not jump.
  const seenLayout = useRef(layoutKey);
  useLayoutEffect(() => {
    if (seenLayout.current === layoutKey) return;
    seenLayout.current = layoutKey;
    const next = new Map();
    const el = box.current;
    if (el) {
      for (const node of el.querySelectorAll(".vitem")) {
        const h = Math.ceil(node.getBoundingClientRect().height);
        if (h) next.set(node.dataset.id, h);
      }
    }
    heights.current = next;
    redraw((n) => n + 1);
  }, [layoutKey]);

  // The anchor (first visible item and its offset from the viewport top) is taken at the END of
  // each commit, so when items are prepended the effect below still sees the previous layout.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    if (jump.current && Date.now() > jump.current.until) jump.current = null;
    // Never write scrollTop while the finger (or its inertia) owns the scroll: the browser drops the touch and the
    // list "sticks". onScroll keeps the anchor fresh meanwhile; the position is corrected after the finger lets go.
    if (userScrolling() && !jump.current) return;
    const jumpIndex = jump.current ? items.findIndex((i) => i.id === jump.current.id) : -1;
    if (jumpIndex >= 0) {
      stuck.current = false;
      if (Math.abs(offsets[jumpIndex] - el.scrollTop) > 1) el.scrollTop = offsets[jumpIndex];
    } else if (stuck.current) {
      el.scrollTop = el.scrollHeight;
    } else if (anchor.current) {
      const idx = items.findIndex((i) => keyOf(i) === anchor.current.id);
      if (idx > 0 && Math.abs(offsets[idx] - anchor.current.offset - el.scrollTop) > 1) {
        el.scrollTop = offsets[idx] - anchor.current.offset;
      }
    }
    anchor.current = captureAnchor(el.scrollTop);
  });

  const onScroll = useCallback(() => {
    const el = box.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (!jump.current) stuck.current = distance < STICK_PX;
    const needJump = distance > 180;
    if (needJump !== showJumpRef.current) {
      showJumpRef.current = needJump;
      setShowJump(needJump);
    }
    // The user moved the view: that is the new reference point, not a layout shift to undo.
    anchor.current = captureAnchor(el.scrollTop);
    // Redraw only when the rendered window must move: the overscan covers the steps in between. Redrawing every
    // message on every scroll event made a fast fling up stutter (owner's tablet, 2026-11-22).
    const seen = viewportRef.current;
    if (Math.abs(el.scrollTop - seen.top) > REDRAW_PX || el.clientHeight !== seen.height || el.scrollTop < REDRAW_PX
        || distance < REDRAW_PX) {
      setViewportBoth({ top: el.scrollTop, height: el.clientHeight });
    }
    if (el.scrollTop < LOAD_MORE_PX && onReachTop) onReachTop();
  }, [onReachTop]);

  useEffect(() => {
    const el = box.current;
    setViewportBoth({ top: el.scrollTop, height: el.clientHeight });
    const resize = new ResizeObserver(() => setViewportBoth({ top: el.scrollTop, height: el.clientHeight }));
    resize.observe(el);
    return () => resize.disconnect();
  }, []);

  // Binary search for the first item intersecting viewport - OVERSCAN_PX (O(log N) instead of O(N) scan)
  const targetTop = viewport.top - OVERSCAN_PX;
  let low = 0;
  let high = items.length - 1;
  let from = 0;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (offsets[mid] + heightOf(keyOf(items[mid])) > targetTop) {
      from = mid;
      high = mid - 1;
    } else {
      low = mid + 1;
    }
  }
  let to = from;
  while (to < items.length && offsets[to] < viewport.top + viewport.height + OVERSCAN_PX) to++;
  const start = items.length ? offsets[from] : 0;
  const end = to < items.length ? total - offsets[to] : 0;

  const measure = (el) => {
    if (el && observer.current) observer.current.observe(el);
  };

  return html`
    <div class="vlist-wrap" data-slot="virtual-list-wrap">
      <div class="vlist" ref=${box} onScroll=${onScroll}
        role="log" aria-label="История сообщений" aria-relevant="additions text" tabindex="0"
        onTouchStart=${() => { touching.current = true; /* a touch alone does not unstick: only scrolling up does (onScroll) */ }}
        onTouchEnd=${() => { touching.current = false; markUser(700); }}
        onTouchCancel=${() => { touching.current = false; markUser(700); }}
        onWheel=${(e) => { markUser(200); if (e.deltaY < 0) stuck.current = false; }}>
        <div style=${{ height: start + "px" }}></div>
        ${items.slice(from, to).map((item) => html`
          <div class="vitem" key=${keyOf(item)} data-id=${keyOf(item)} ref=${measure}>${renderItem(item)}</div>
        `)}
        <div style=${{ height: end + "px" }}></div>
      </div>
      ${status && html`<div class="vlist-status" data-slot="vlist-status" role="status"><span class="spinner" aria-hidden="true"></span>${status}</div>`}
      ${showJump && html`<button type="button" class="vlist-jump" data-slot="vlist-jump"
        aria-label="Перейти к последним сообщениям" title="Перейти к последним сообщениям" onClick=${scrollToBottom}>
        <${Icon} name="chevron-down" />
      </button>`}
    </div>
  `;
}
