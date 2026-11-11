// Shared parts of the lists that open from a field (components/Select.js, components/Suggest.js): where the list
// goes, how it scrolls under a finger. Pure placement is exported for tests (tests/unit/test_web_logic.py).
//
// Why this exists (owner, Android tablet, keyboard open): the list jumped away from the finger and nothing could be
// picked. Three things did it: the free room was measured against the visual viewport's height but not its offset
// (with the keyboard open Chrome pans the visual viewport, so the list was placed partly off the visible part);
// a finger sliding over rows made one "active" and scrollIntoView() of that row panned the whole page to show it;
// a drag on a list that could not scroll any further moved the page under it. Here: placement within the visible
// part, the side kept once chosen, rows brought into view by the list's own scroll only, and touch moves that
// would scroll the page stopped inside the list.

const KEEP = 132; // three rows: the side already chosen stays while it has at least this much room

// The part of the page the user sees, in the coordinates of position: fixed (the layout viewport).
export function visibleArea() {
  const vv = typeof window !== "undefined" ? window.visualViewport : null;
  const full = typeof window !== "undefined" ? window.innerHeight : 0;
  if (!vv) return { top: 0, height: full };
  const top = Math.max(0, vv.offsetTop || 0);
  return { top, height: Math.max(0, Math.min(vv.height, full ? full - top : vv.height)) };
}

// Where a list of `natural` height goes beside a field `box` ({top, bottom, left}) inside the visible part `view`
// ({top, height}). `side` is the side chosen before ("down" / "up" / null): it is kept while it has room, so the
// list does not flip back and forth while the keyboard moves. Returns { side, top, left, maxHeight }.
export function placeList({ box, natural, width, view, pageWidth, side = null, gap = 4, margin = 8, least = 88 }) {
  const viewTop = view.top;
  const viewBottom = view.top + view.height;
  const below = viewBottom - box.bottom - gap - margin;
  const above = box.top - viewTop - gap - margin;
  const enough = Math.min(natural, KEEP);
  let down;
  if (side === "down" && below >= enough) down = true;
  else if (side === "up" && above >= enough) down = false;
  else down = natural <= below || below >= above;
  const room = Math.max(least, Math.floor(down ? below : above));
  const height = Math.min(natural, room);
  const top = down ? box.bottom + gap : Math.max(viewTop + margin, box.top - gap - height);
  const left = Math.max(margin, Math.min(box.left, pageWidth - width - margin));
  return { side: down ? "down" : "up", top: Math.round(top), left: Math.round(left), maxHeight: room };
}

// Bring a row into view by scrolling the list itself. scrollIntoView() would also scroll every scrolling parent
// and the visual viewport, which moved the whole page under the finger.
export function keepRowInList(list, row) {
  if (!list || !row) return;
  const l = list.getBoundingClientRect();
  const r = row.getBoundingClientRect();
  if (r.top < l.top) list.scrollTop -= l.top - r.top;
  else if (r.bottom > l.bottom) list.scrollTop += r.bottom - l.bottom;
}

// A finger on the list scrolls the list and never the page behind it: a move the list cannot take (it is short,
// already at that end, or the finger is on the list's frame) is cancelled. Returns a function that removes it.
export function holdTouch(panel, scroller) {
  let lastY = 0;
  const start = (e) => { if (e.touches.length === 1) lastY = e.touches[0].clientY; };
  const move = (e) => {
    if (e.touches.length !== 1 || !e.cancelable) return;
    const y = e.touches[0].clientY;
    const dy = y - lastY; // > 0: the finger goes down, the list scrolls up
    lastY = y;
    const s = scroller;
    const inside = s && s.contains(e.target);
    const can = inside && s.scrollHeight > s.clientHeight + 1
      && (dy > 0 ? s.scrollTop > 0 : dy < 0 ? s.scrollTop + s.clientHeight < s.scrollHeight - 1 : true);
    if (!can) e.preventDefault();
  };
  panel.addEventListener("touchstart", start, { passive: true });
  panel.addEventListener("touchmove", move, { passive: false });
  return () => {
    panel.removeEventListener("touchstart", start);
    panel.removeEventListener("touchmove", move);
  };
}
