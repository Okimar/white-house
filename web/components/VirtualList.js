// Virtualized list with variable item heights, built for chat:
//  - only items near the viewport are in the DOM (long chats stay smooth);
//  - heights are measured with ResizeObserver, unmeasured items use an estimate;
//  - stays pinned to the bottom while new text streams in, unless the user scrolled up;
//  - when older items are prepended, the visible content does not jump.
import { html, useState, useRef, useLayoutEffect, useEffect, useCallback } from "../vendor/preact-htm.mjs";

const ESTIMATE = 90;
const OVERSCAN_PX = 600;
const STICK_PX = 48;
const LOAD_MORE_PX = 400;

export function VirtualList({ items, renderItem, onReachTop, resetKey }) {
  const box = useRef(null);
  const heights = useRef(new Map()); // item id -> measured height
  const stuck = useRef(true); // pinned to the bottom
  const anchor = useRef(null); // { id, offset } of the first item before an update
  const [, redraw] = useState(0);
  const [viewport, setViewport] = useState({ top: 0, height: 600 });

  const heightOf = (id) => heights.current.get(id) ?? ESTIMATE;
  const offsets = [];
  let total = 0;
  for (const item of items) {
    offsets.push(total);
    total += heightOf(item.id);
  }

  // Latest layout, readable from event handlers that outlive a single render.
  const layout = useRef(null);
  layout.current = { items, offsets, heightOf };
  const captureAnchor = (top) => {
    const { items: list, offsets: offs, heightOf: h } = layout.current;
    const first = list.findIndex((item, i) => offs[i] + h(item.id) > top);
    return first >= 0 ? { id: list[first].id, offset: offs[first] - top } : null;
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
  }, [resetKey]);

  // The anchor (first visible item and its offset from the viewport top) is taken at the END of
  // each commit, so when items are prepended the effect below still sees the previous layout.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    if (stuck.current) {
      el.scrollTop = el.scrollHeight;
    } else if (anchor.current) {
      const idx = items.findIndex((i) => i.id === anchor.current.id);
      if (idx > 0 && Math.abs(offsets[idx] - anchor.current.offset - el.scrollTop) > 1) {
        el.scrollTop = offsets[idx] - anchor.current.offset;
      }
    }
    anchor.current = captureAnchor(el.scrollTop);
  });

  const onScroll = useCallback(() => {
    const el = box.current;
    stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
    // The user moved the view: that is the new reference point, not a layout shift to undo.
    anchor.current = captureAnchor(el.scrollTop);
    setViewport({ top: el.scrollTop, height: el.clientHeight });
    if (el.scrollTop < LOAD_MORE_PX && onReachTop) onReachTop();
  }, [onReachTop]);

  useEffect(() => {
    const el = box.current;
    setViewport({ top: el.scrollTop, height: el.clientHeight });
    const resize = new ResizeObserver(() => setViewport({ top: el.scrollTop, height: el.clientHeight }));
    resize.observe(el);
    return () => resize.disconnect();
  }, []);

  const from = Math.max(0, items.findIndex((_, i) => offsets[i] + heightOf(items[i].id) > viewport.top - OVERSCAN_PX));
  let to = from;
  while (to < items.length && offsets[to] < viewport.top + viewport.height + OVERSCAN_PX) to++;
  const start = items.length ? offsets[from] : 0;
  const end = to < items.length ? total - offsets[to] : 0;

  const measure = (el) => {
    if (el && observer.current) observer.current.observe(el);
  };

  return html`
    <div class="vlist" ref=${box} onScroll=${onScroll}>
      <div style=${{ height: start + "px" }}></div>
      ${items.slice(from, to).map((item) => html`
        <div class="vitem" key=${item.id} data-id=${item.id} ref=${measure}>${renderItem(item)}</div>
      `)}
      <div style=${{ height: end + "px" }}></div>
    </div>
  `;
}
