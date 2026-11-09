// The bubble map drawn on one <canvas> (default; ?treedebug=dom brings back the DOM map in CharacterTree.js).
// Why: the DOM map (hundreds of absolutely placed bubbles moved by one CSS transform) blanked out whole rectangles
// on a weak mobile GPU, and inserting culled bubbles lagged. Here nothing is a DOM node: every frame the visible part
// of the scene is painted with the camera transform, only when something changed (camera, data, selection, drag, theme).
// Colours and the font come from the theme's CSS tokens (see .claude/skills/style-bubble-map/SKILL.md): read once
// with getComputedStyle and again whenever the theme or the colours change.
// This module has no component and no state of its own: buildModel() turns the tree into plain arrays, hitTest()
// answers "what is under this point", createPainter() paints. CharacterTree.js owns the camera, pointers and API calls.
//
// Speed (the owner's tablet: 768x1280, DPR 2, weak CPU and GPU; 150-2000 bubbles, hundreds of lines):
// - nothing is measured, laid out or allocated per bubble per frame: text layouts and Path2D shapes are made once per
//   bubble (and font), warmed in idle time, so a zoom that brings hundreds of titles in pays only for drawing them;
// - plain bubbles are drawn grouped by look, so colours, widths, dashes and fonts are set a handful of times per frame;
//   each shape stays a separate rounded rect / circle, which the GPU draws analytically and batches (one path with
//   hundreds of rounded rects would fall back to a slow general path renderer on a weak GPU);
// - every text is drawn only when it is at least TEXT_PX on screen, and each mark has its own limit, so zooming in
//   switches on titles, counts, numbers in separate steps instead of all at once;
// - lines and link arcs are one path per colour band, culled by their real extent;
// - the space underneath an opened bubble never moves: it is painted once into its own canvas and copied;
// - the backing store is at most 2x the CSS size.

export const W = 168; // bubble size (the same numbers as CharacterTree.js)
export const H = 46;
const PAD = 18; // space around a nested group
const FAR = 0.45; // below this zoom: no ports (the hit tests in CharacterTree.js use the same limit)
const TEXT_PX = 6; // a text smaller than this on screen (CSS px) is not drawn: unreadable, and text is the costly part
const TINY = 0.2; // below this zoom bubbles are plain coloured boxes
const GRID = 240; // hit-test and culling cell, world px
const MAX_DPR = 2;
const HAS_RR = typeof Path2D !== "undefined" && typeof Path2D.prototype.roundRect === "function";
const BAND_ORDER = ["off", "c100", "c75", "c50", "c25", "c0"];

const CHANCE_BANDS = [[100, "c100"], [75, "c75"], [50, "c50"], [25, "c25"], [0, "c0"]];
const band = (b) => (b.mode !== "search" ? "c100" : CHANCE_BANDS.find(([min]) => (b.chance ?? 100) >= min)[1]);

// What to draw in one space: `scope` null = the whole map (the card is the root), or a bubble opened with
// «Открыть внутри». `d` carries the tree: pos, kids, links, inactive, orderOf, vectorized, character, descendants().
export function buildModel(d, scope) {
  const { pos, kids, links, inactive, orderOf, vectorized, character, descendants } = d;
  const rootId = scope || "root";
  const items = [];
  const edges = [];
  const groups = [];
  const rootBlock = scope ? d.byId.get(scope) : null;
  const root = { id: rootId, root: true, space: !!scope, mode: rootBlock ? rootBlock.mode : "root",
    title: scope ? rootBlock.title : character.name, avatar: !scope && !!character.avatar, p: pos.get(rootId) };
  items.push(root);
  const edge = (a, b, bandKey) => edges.push({ a, b, band: bandKey, pa: pos.get(a), pb: pos.get(b) });
  const kidsOf = (id) => (kids.get(id) || []).filter((c) => pos.has(c.id));
  for (const b of kids.get(scope) || []) {
    if (pos.has(b.id) && !b.detached) edge(rootId, b.id, inactive.has(b.id) ? "off" : band(b));
  }
  const depthOf = (id) => 1 + Math.max(0, ...(kids.get(id) || []).map((c) => depthOf(c.id)));
  const visit = (parent) => {
    for (const b of kids.get(parent) || []) {
      if (!pos.has(b.id)) continue;
      const children = b.layout?.collapsed && !scope ? [] : kidsOf(b.id);
      const kidsN = (kids.get(b.id) || []).length;
      const off = inactive.has(b.id);
      items.push({ id: b.id, b, mode: b.mode, title: b.title, inactive: off,
        orderN: orderOf.has(b.id) ? orderOf.get(b.id).n : 0, chance: b.chance ?? 100, vec: vectorized.has(b.id), kidsN,
        collapsed: !!b.layout?.collapsed,
        depth: b.layout?.collapsed && !scope && kidsN ? Math.min(3, depthOf(b.id)) : 0,
        p: pos.get(b.id), look: b.mode + (off ? "-free" : "") });
      if (children.length && b.layout?.display === "nested") {
        groups.push({ id: b.id, mode: b.mode, members: [b.id, ...descendants(kids, b.id)].filter((id) => pos.has(id)) });
      } else {
        for (const c of children) edge(b.id, c.id, inactive.has(c.id) ? "off" : band(c));
      }
      if (children.length) visit(b.id);
    }
  };
  visit(scope);
  const inside = new Set(scope ? descendants(kids, scope) : pos.keys());
  const refs = links.filter(([a, b]) => inside.has(a) && inside.has(b) && pos.has(a) && pos.has(b));
  // A grid of cells for hit tests and culling: bubbles never overlap, so a point sees a handful of them at most.
  const grid = new Map();
  const byId = new Map();
  for (const it of items) {
    byId.set(it.id, it);
    const p = it.p;
    if (!p) continue;
    for (let gx = Math.floor((p.x - 14) / GRID); gx <= Math.floor((p.x + W + 14) / GRID); gx += 1) {
      for (let gy = Math.floor((p.y - 14) / GRID); gy <= Math.floor((p.y + H + 14) / GRID); gy += 1) {
        const key = gx * 100003 + gy;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(it);
      }
    }
  }
  return { pos, items, edges, groups, refs, grid, byId, rootId, scope };
}

// What is under a world point: {bid, port} (port "in"/"out" only where ports are shown), or {} for empty space.
// `exclude` (a Set of ids) is skipped: the bubbles being dragged. `portR` is the grab radius in world px.
export function hitTest(model, wx, wy, { portR = 10, ports = () => false, exclude = null } = {}) {
  if (!model) return {};
  const cell = model.grid.get(Math.floor(wx / GRID) * 100003 + Math.floor(wy / GRID));
  if (!cell) return {};
  for (const it of cell) {
    if (exclude && exclude.has(it.id)) continue;
    if (it.root && !it.space) continue;
    if (!ports(it)) continue;
    const p = model.pos.get(it.id);
    const cy = p.y + H / 2;
    if (!it.space && Math.hypot(wx - p.x, wy - cy) <= portR) return { bid: it.id, port: "in" };
    if (Math.hypot(wx - (p.x + W), wy - cy) <= portR) return { bid: it.id, port: "out" };
  }
  for (const it of cell) {
    if (exclude && exclude.has(it.id)) continue;
    const p = model.pos.get(it.id);
    if (wx >= p.x && wx <= p.x + W && wy >= p.y && wy <= p.y + H) return { bid: it.id };
  }
  return {};
}

// Same visible pixels as the bubble ring drawn by the old DOM map: rounded rects and circles. Kept as single shapes
// (see the speed notes at the top).
const rrInto = (path, x, y, w, h, r) => {
  if (path.roundRect) path.roundRect(x, y, w, h, r);
  else path.rect(x, y, w, h);
};
const rr = (ctx, x, y, w, h, r) => { ctx.beginPath(); rrInto(ctx, x, y, w, h, r); };
const rrPath = (x, y, w, h, r) => { const p = new Path2D(); rrInto(p, x, y, w, h, r); return p; };
const dotPath = (x, y, r) => {
  if (HAS_RR) return rrPath(x - r, y - r, 2 * r, 2 * r, r);
  const p = new Path2D(); p.arc(x, y, r, 0, Math.PI * 2); return p;
};
const dot = (ctx, x, y, r) => { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); };
// A parent line: from the right edge of `a` to the left edge of `b`, an S-curve.
const bez = (path, a, b) => {
  const x1 = a.x + W; const y1 = a.y + H / 2; const x2 = b.x; const y2 = b.y + H / 2; const mx = (x1 + x2) / 2;
  path.moveTo(x1, y1); path.bezierCurveTo(mx, y1, mx, y2, x2, y2);
};

// Colours and the font, as the theme sets them. A probe element resolves `var(...)` and `color-mix(...)` to real colours.
const TOKENS = {
  always: "--mode-always", search: "--mode-search", off: "--mode-off", line: "--line", muted: "--muted", accent: "--accent",
  c75: "--chance-75", c50: "--chance-50", c25: "--chance-25", c0: "--chance-0", dialog: "--dialog-bg", fg: "--fg",
};

export function createPainter(canvas, host, getLive) {
  const main = canvas.getContext("2d", { alpha: true });
  let ctx = main; // the space underneath is painted into its own canvas with the same code
  // Text is measured on a separate tiny canvas, so measuring never changes the font of a frame being drawn.
  const mctx = document.createElement("canvas").getContext("2d");
  let mfont = "";
  const probe = document.createElement("span");
  probe.setAttribute("aria-hidden", "true");
  probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;width:0;height:0;overflow:hidden";
  host.appendChild(probe);
  let colors = {};
  let font = { family: "system-ui, sans-serif", size: 16 };
  let fonts = {};
  let gen = 0; // grows when the colours or the font change: cached text layouts and the underneath canvas are made again
  let raf = 0;
  let dead = false;
  const widths = new Map();
  const labels = new Map();
  const stats = { drawn: 0, ms: 0, frames: 0, worst: 0, times: [] };
  let cssW = 0; let cssH = 0; let dpr = 1;
  const buckets = new Map(); // look -> plain bubbles in view this frame (the arrays live between frames)
  const specials = []; const late = [];
  let stamp = 0;
  let under = null; let underKey = null;

  const fontStr = (px, weight = 400) => `${weight} ${px}px ${font.family}`;
  function refreshTokens(force = false) {
    const next = {};
    for (const [key, token] of Object.entries(TOKENS)) {
      probe.style.color = "";
      probe.style.color = `var(${token})`;
      next[key] = getComputedStyle(probe).color;
    }
    next.root = next.accent;
    const cs = getComputedStyle(host);
    const nf = { family: cs.fontFamily || font.family, size: parseFloat(cs.fontSize) || 16 };
    // Any attribute change on <html> or in <head> lands here; most change nothing the map uses, so keep the caches.
    if (!force && colors.dialog && nf.family === font.family && nf.size === font.size
      && Object.keys(next).every((k) => next[k] === colors[k])) return;
    colors = next;
    font = nf;
    fonts = { title: fontStr(font.size), bold: fontStr(font.size, 600), badge: fontStr(font.size * 0.75),
      vec: fontStr(font.size * 0.9), chance: fontStr(font.size * 0.7), num: fontStr(font.size * 0.65) };
    gen += 1;
    widths.clear();
    labels.clear();
    requestDraw();
  }

  const targetDpr = () => Math.min(MAX_DPR, window.devicePixelRatio || 1);
  function resize() {
    dpr = targetDpr();
    const box = host.getBoundingClientRect();
    cssW = Math.max(1, box.width); cssH = Math.max(1, box.height);
    const w = Math.min(8192, Math.round(cssW * dpr)); const h = Math.min(8192, Math.round(cssH * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  }

  function requestDraw() {
    if (!raf && !dead) raf = requestAnimationFrame(draw);
  }
  // Setting the canvas size clears it: the new frame is painted in the same task, so no empty frame is ever shown.
  function drawNow() {
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
    draw();
  }

  const setMeasureFont = (f) => { if (mfont !== f) { mctx.font = f; mfont = f; } };
  const measure = (text, f) => {
    const key = f + "|" + text;
    let w = widths.get(key);
    if (w === undefined) {
      if (widths.size > 8000) widths.clear();
      setMeasureFont(f);
      w = mctx.measureText(text).width;
      widths.set(key, w);
    }
    return w;
  };
  // The longest start of `text` that fits `avail` with "…"; guessed from the full width, then corrected a step or two.
  const fitNow = (text, avail, f) => {
    if (avail <= 8) return "";
    const full = measure(text, f);
    if (full <= avail) return text;
    setMeasureFont(f);
    const width = (n) => mctx.measureText(text.slice(0, n) + "…").width;
    let n = Math.max(0, Math.min(text.length - 1, Math.floor((text.length * avail) / full)));
    if (width(n) <= avail) { while (n < text.length - 1 && width(n + 1) <= avail) n += 1; }
    else { do n -= 1; while (n > 0 && width(n) > avail); }
    return n > 0 ? text.slice(0, n) + "…" : "…";
  };
  const fit = (text, avail, f) => {
    const key = f + "|" + avail + "|" + text;
    let out = labels.get(key);
    if (out === undefined) {
      if (labels.size > 2000) labels.clear();
      out = fitNow(text, avail, f);
      labels.set(key, out);
    }
    return out;
  };

  // Where the title and the marks of a (non-root) bubble go, relative to its top-left corner; made once per bubble and
  // font. The title always leaves room for every mark, even at a zoom that hides some, so it never changes as you zoom.
  function layoutOf(it) {
    let L = it.lay;
    if (L && L.gen === gen) return L;
    let right = W - 14;
    L = { gen, left: 14 + 12 + 6, title: "", badge: null, vec: 0, chance: null, num: null };
    if (it.kidsN > 0) {
      const t = (it.collapsed ? "+" : "") + it.kidsN;
      const w = measure(t, fonts.badge) + 14;
      L.badge = { t, x: right - w, w };
      right -= w + 6;
    }
    if (it.vec) {
      const w = measure("≈", fonts.vec);
      L.vec = right - w;
      right -= w + 6;
    }
    if (it.mode === "search" && it.chance < 100) {
      const t = it.chance + "%";
      const w = measure(t, fonts.chance);
      L.chance = { t, x: right - w };
      right -= w + 6;
    }
    if (it.orderN) {
      const t = String(it.orderN);
      const w = Math.max(16, measure(t, fonts.num) + 10);
      L.num = { t, x: W - 2 - 14 - w, w };
    }
    L.title = fitNow(it.title || "", right - L.left, fonts.title);
    it.lay = L;
    return L;
  }
  // The shapes of a plain bubble at its place: body, dot and ports by position; the mark boxes also by the layout.
  function shapesOf(it) {
    const p = it.p;
    let S = it.shp;
    if (!S || S.x !== p.x || S.y !== p.y) {
      const cy = p.y + H / 2;
      S = { x: p.x, y: p.y, body: rrPath(p.x + 1, p.y + 1, W - 2, H - 2, 22), dot: dotPath(p.x + 20, cy, 6),
        inPort: dotPath(p.x, cy, 10), outPort: dotPath(p.x + W, cy, 10), gen: -1, badge: null, num: null };
      it.shp = S;
    }
    return S;
  }
  function boxesOf(it) {
    const S = shapesOf(it);
    if (S.gen !== gen) {
      const L = layoutOf(it);
      const cy = S.y + H / 2 + 0.5;
      S.badge = L.badge ? rrPath(S.x + L.badge.x, cy - 9, L.badge.w, 18, 6) : null;
      S.num = L.num ? rrPath(S.x + L.num.x, S.y - 7, L.num.w, 16, 6) : null;
      S.gen = gen;
    }
    return S;
  }

  // Text layouts of every bubble are made in idle time after the tree or the font changes, so the first zoom to a
  // readable size does not measure hundreds of titles in one frame.
  let warmModel = null; let warmGen = -1; let warmAt = 0; let warming = false;
  const idle = typeof requestIdleCallback === "function"
    ? (fn) => requestIdleCallback(fn, { timeout: 1000 })
    : (fn) => setTimeout(() => fn({ timeRemaining: () => 6 }), 60);
  function warm(model) {
    if (model === warmModel && warmGen === gen) return;
    warmModel = model; warmGen = gen; warmAt = 0;
    if (!warming) { warming = true; idle(warmStep); }
  }
  function warmStep(deadline) {
    warming = false;
    if (dead || !warmModel || warmGen !== gen) return;
    const list = warmModel.items;
    while (warmAt < list.length && deadline.timeRemaining() > 1) {
      const it = list[warmAt]; warmAt += 1;
      if (!it.root) layoutOf(it);
    }
    if (warmAt < list.length) { warming = true; idle(warmStep); }
  }

  const modeColor = (m) => colors[m] || colors.line;
  const alphaOf = (it) => (it.inactive ? 0.4 : it.mode === "off" ? 0.55 : 1);

  // One bubble with everything it may carry: the card, picked, hovered, folded, drop target, being dragged.
  // These are few; the many plain ones go through drawPlain.
  function drawBubble(it, x, y, st) {
    const { live } = st;
    const mc = it.root && !it.space ? colors.accent : modeColor(it.mode);
    const dragging = live.drag && live.drag.ids.has(it.id);
    let alpha = alphaOf(it);
    if (dragging) alpha *= 0.75;
    // folded bubble: copies behind it "into the depth"
    for (let i = it.depth || 0; i >= 1; i -= 1) {
      ctx.globalAlpha = alpha * [0, 0.45, 0.25, 0.12][i];
      rr(ctx, x + 6 * i, y + 6 * i, W, H, 23);
      ctx.fillStyle = colors.dialog; ctx.fill();
      ctx.strokeStyle = mc; ctx.lineWidth = 2; ctx.stroke();
    }
    ctx.globalAlpha = alpha;
    const selected = live.selected === it.id;
    const target = (live.drag && live.drag.target === it.id) || (live.wire && live.wire.target === it.id);
    const linking = live.linking === it.id;
    if (target) { rr(ctx, x - 2, y - 2, W + 4, H + 4, 25); ctx.strokeStyle = colors.search; ctx.lineWidth = 4; ctx.stroke(); }
    else if (selected || linking) { rr(ctx, x - 1.5, y - 1.5, W + 3, H + 3, 24.5); ctx.strokeStyle = colors.accent; ctx.lineWidth = 3; ctx.stroke(); }
    const bw = it.space ? 3 : 2;
    rr(ctx, x + bw / 2, y + bw / 2, W - bw, H - bw, 23 - bw / 2);
    ctx.fillStyle = colors.dialog; ctx.fill();
    ctx.strokeStyle = mc; ctx.lineWidth = bw;
    const dashed = it.inactive && !st.plain;
    if (dashed) ctx.setLineDash([5, 4]);
    ctx.stroke();
    if (dashed) ctx.setLineDash([]);
    if (st.title) {
      const cy = y + H / 2 + 0.5;
      let left;
      let title;
      if (it.root) {
        left = x + 14;
        if (!it.space) {
          const img = live.avatar;
          if (it.avatar && img && img.complete && img.naturalWidth) {
            ctx.save();
            ctx.beginPath(); ctx.arc(x + 8 + 15, cy - 0.5, 15, 0, Math.PI * 2); ctx.clip();
            ctx.drawImage(img, x + 8, cy - 15.5, 30, 30);
            ctx.restore();
            left = x + 8 + 30 + 6;
          }
        } else {
          dot(ctx, x + 20, y + H / 2, 6); ctx.fillStyle = mc; ctx.fill();
          left = x + 14 + 12 + 6;
        }
        title = fit(it.title || "", x + W - 14 - left, fonts.bold);
      } else {
        dot(ctx, x + 20, y + H / 2, 6); ctx.fillStyle = mc; ctx.fill();
        const L = layoutOf(it);
        left = x + L.left;
        title = L.title;
        ctx.fillStyle = colors.muted;
        if (L.badge && st.badge) {
          rr(ctx, x + L.badge.x, cy - 9, L.badge.w, 18, 6);
          ctx.strokeStyle = colors.line; ctx.lineWidth = 1; ctx.stroke();
          ctx.font = fonts.badge; ctx.fillText(L.badge.t, x + L.badge.x + 7, cy);
        }
        if (it.vec && st.vec) { ctx.font = fonts.vec; ctx.fillText("≈", x + L.vec, cy); }
        if (L.chance && st.chance) { ctx.font = fonts.chance; ctx.fillText(L.chance.t, x + L.chance.x, cy); }
        if (L.num && st.num) {
          const ox = x + L.num.x; const oy = y - 7;
          rr(ctx, ox, oy, L.num.w, 16, 6);
          ctx.fillStyle = colors.dialog; ctx.fill();
          ctx.strokeStyle = mc; ctx.lineWidth = 1; ctx.stroke();
          ctx.font = fonts.num; ctx.fillStyle = colors.muted; ctx.textAlign = "center";
          ctx.fillText(L.num.t, ox + L.num.w / 2, oy + 8.5);
          ctx.textAlign = "left";
        }
      }
      ctx.font = it.root ? fonts.bold : fonts.title; ctx.fillStyle = colors.fg;
      ctx.fillText(title, left, cy);
    }
    if (st.ports && (!it.root || it.space)) {
      const hot = selected || target || live.hover === it.id;
      if (st.coarse || hot) {
        const r = st.coarse ? 10 : 7;
        ctx.globalAlpha = st.coarse && !hot ? 0.8 * alpha : alpha;
        ctx.fillStyle = colors.dialog; ctx.strokeStyle = mc; ctx.lineWidth = 2;
        if (!it.space) { dot(ctx, x, y + H / 2, r); ctx.fill(); ctx.stroke(); }
        dot(ctx, x + W, y + H / 2, r); ctx.fill(); ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;
  }

  // Plain bubbles of one look (mode + free): every style is set once for the whole list. Shapes first, then texts by
  // font: bubbles never overlap, so the order between bubbles does not show.
  function drawPlain(list, st) {
    const first = list[0];
    const mc = modeColor(first.mode);
    const alpha = alphaOf(first);
    const n = list.length;
    ctx.globalAlpha = alpha;
    ctx.fillStyle = colors.dialog;
    for (let i = 0; i < n; i += 1) ctx.fill(shapesOf(list[i]).body);
    ctx.strokeStyle = mc; ctx.lineWidth = 2;
    const dashed = first.inactive && !st.plain;
    if (dashed) ctx.setLineDash([5, 4]);
    for (let i = 0; i < n; i += 1) ctx.stroke(list[i].shp.body);
    if (dashed) ctx.setLineDash([]);
    if (st.title) {
      ctx.fillStyle = mc;
      for (let i = 0; i < n; i += 1) ctx.fill(list[i].shp.dot);
      if (st.badge) {
        ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
        for (let i = 0; i < n; i += 1) { const b = boxesOf(list[i]).badge; if (b) ctx.stroke(b); }
      }
      if (st.num) {
        ctx.fillStyle = colors.dialog;
        for (let i = 0; i < n; i += 1) { const b = boxesOf(list[i]).num; if (b) ctx.fill(b); }
        ctx.strokeStyle = mc; ctx.lineWidth = 1;
        for (let i = 0; i < n; i += 1) { const b = list[i].shp.num; if (b) ctx.stroke(b); }
      }
      ctx.font = fonts.title; ctx.fillStyle = colors.fg;
      for (let i = 0; i < n; i += 1) {
        const it = list[i]; const L = it.lay;
        ctx.fillText(L.title, it.p.x + L.left, it.p.y + H / 2 + 0.5);
      }
      ctx.fillStyle = colors.muted;
      if (st.badge) {
        ctx.font = fonts.badge;
        for (let i = 0; i < n; i += 1) {
          const it = list[i]; const b = it.lay.badge;
          if (b) ctx.fillText(b.t, it.p.x + b.x + 7, it.p.y + H / 2 + 0.5);
        }
      }
      if (st.vec) {
        ctx.font = fonts.vec;
        for (let i = 0; i < n; i += 1) { const it = list[i]; if (it.vec) ctx.fillText("≈", it.p.x + it.lay.vec, it.p.y + H / 2 + 0.5); }
      }
      if (st.chance) {
        ctx.font = fonts.chance;
        for (let i = 0; i < n; i += 1) {
          const it = list[i]; const c = it.lay.chance;
          if (c) ctx.fillText(c.t, it.p.x + c.x, it.p.y + H / 2 + 0.5);
        }
      }
      if (st.num) {
        ctx.font = fonts.num; ctx.textAlign = "center";
        for (let i = 0; i < n; i += 1) {
          const it = list[i]; const m = it.lay.num;
          if (m) ctx.fillText(m.t, it.p.x + m.x + m.w / 2, it.p.y + 1.5);
        }
        ctx.textAlign = "left";
      }
    }
    // Touch: ports on every bubble (hovered and picked ones are drawn by drawBubble).
    if (st.ports && st.coarse) {
      ctx.globalAlpha = 0.8 * alpha;
      ctx.fillStyle = colors.dialog;
      for (let i = 0; i < n; i += 1) { const S = list[i].shp; ctx.fill(S.inPort); ctx.fill(S.outPort); }
      ctx.strokeStyle = mc; ctx.lineWidth = 2;
      for (let i = 0; i < n; i += 1) { const S = list[i].shp; ctx.stroke(S.inPort); ctx.stroke(S.outPort); }
    }
    ctx.globalAlpha = 1;
  }

  // One space of the scene with `cam` {x, y, s}; returns how many bubbles were drawn.
  function drawSpace(model, cam, live, opts) {
    const s = cam.s;
    ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * cam.x, dpr * cam.y);
    ctx.lineJoin = "round"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    const m = 24 + 18;
    const left = -cam.x / s - m; const top = -cam.y / s - m;
    const right = left + cssW / s + 2 * m; const bottom = top + cssH / s + 2 * m;
    const { pos } = model;
    const drag = opts.behind ? null : live.drag;
    const ids = drag ? drag.ids : null;
    const moved = (p) => ({ x: p.x + drag.dx, y: p.y + drag.dy });
    const at = (id) => (ids && ids.has(id) ? moved(pos.get(id)) : pos.get(id));
    const plain = s < FAR || opts.moving;
    // nested groups: the frame of a group is measured once, again only while one of its bubbles is dragged
    for (const g of model.groups) {
      let box = g.box;
      let hit = false;
      if (ids) {
        if (g.dragIds !== ids) { g.dragIds = ids; g.dragHit = g.members.some((id) => ids.has(id)); }
        hit = g.dragHit;
      }
      if (!box || hit) {
        let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
        for (const id of g.members) {
          const p = hit ? at(id) : pos.get(id);
          if (p.x < x0) x0 = p.x; if (p.y < y0) y0 = p.y; if (p.x > x1) x1 = p.x; if (p.y > y1) y1 = p.y;
        }
        box = { x: x0 - PAD, y: y0 - PAD, w: x1 - x0 + W + 2 * PAD, h: y1 - y0 + H + 2 * PAD };
        if (!hit) g.box = box;
      }
      if (box.x + box.w < left || box.x > right || box.y + box.h < top || box.y > bottom) continue;
      const mc = modeColor(g.mode);
      rr(ctx, box.x + 1, box.y + 1, box.w - 2, box.h - 2, 21);
      ctx.globalAlpha = 0.07; ctx.fillStyle = mc; ctx.fill();
      ctx.globalAlpha = 1; ctx.strokeStyle = mc; ctx.lineWidth = 2;
      if (!plain) ctx.setLineDash([6, 5]);
      ctx.stroke(); ctx.setLineDash([]);
    }
    // link arcs, under the lines; culled by the whole arc (it bows out far beyond its two ends)
    if (model.refs.length && !live.nolines) {
      ctx.beginPath();
      let any = false;
      for (const [a, b] of model.refs) {
        const p = at(a); const q = at(b);
        const x1 = p.x + W / 2; const y1 = p.y + H / 2; const x2 = q.x + W / 2; const y2 = q.y + H / 2;
        const dx = x2 - x1; const dy = y2 - y1; const len = Math.hypot(dx, dy) || 1;
        const bow = W * 1.3 + len * 0.3;
        const reach = bow / 2 + 4; // a quadratic curve comes at most half way to its control point
        if (Math.max(x1, x2) + reach < left || Math.min(x1, x2) - reach > right
          || Math.max(y1, y2) + reach < top || Math.min(y1, y2) - reach > bottom) continue;
        let nx = -dy / len; let ny = dx / len;
        if (nx < 0 || (nx === 0 && ny < 0)) { nx = -nx; ny = -ny; }
        ctx.moveTo(x1, y1);
        ctx.quadraticCurveTo((x1 + x2) / 2 + nx * bow, (y1 + y2) / 2 + ny * bow, x2, y2);
        any = true;
      }
      if (any) {
        ctx.strokeStyle = colors.accent; ctx.lineWidth = 2.5; ctx.lineCap = plain ? "butt" : "round";
        ctx.globalAlpha = plain ? 0.35 : 0.5;
        if (!plain) ctx.setLineDash([2, 6]);
        ctx.stroke(); ctx.setLineDash([]); ctx.lineCap = "butt"; ctx.globalAlpha = 1;
      }
    }
    // parent lines, one path and one stroke per colour band
    if (!live.nolines) {
      const paths = {};
      for (const e of model.edges) {
        let p = e.pa; let q = e.pb;
        if (ids) { if (ids.has(e.a)) p = moved(p); if (ids.has(e.b)) q = moved(q); }
        if ((p.x > q.x ? p.x : q.x) + W < left || (p.x < q.x ? p.x : q.x) > right
          || (p.y > q.y ? p.y : q.y) + H < top || (p.y < q.y ? p.y : q.y) > bottom) continue;
        bez(paths[e.band] || (paths[e.band] = new Path2D()), p, q);
      }
      for (const key of BAND_ORDER) {
        const path = paths[key];
        if (!path) continue;
        const chance = key !== "c100" && key !== "off";
        ctx.strokeStyle = chance ? colors[key] : colors.muted;
        ctx.lineWidth = chance ? 2 : 1.5;
        ctx.globalAlpha = key === "off" ? 0.35 : chance ? 0.9 : 0.6;
        if (key === "off" && !plain) ctx.setLineDash([4, 5]);
        ctx.stroke(path); ctx.setLineDash([]); ctx.globalAlpha = 1;
      }
    }
    // bubbles in view: from the grid cells in view when that is fewer than all bubbles
    stamp += 1;
    for (const list of buckets.values()) list.length = 0;
    specials.length = 0; late.length = 0;
    const tiny = s < TINY;
    const k = s * font.size;
    const st = { plain, live, coarse: opts.coarse, ports: s >= FAR && !opts.behind,
      title: !tiny && k >= TEXT_PX && !opts.behindPlain };
    st.badge = st.title && k * 0.75 >= TEXT_PX; st.vec = st.title && k * 0.9 >= TEXT_PX;
    st.chance = st.title && k * 0.7 >= TEXT_PX; st.num = st.title && k * 0.65 >= TEXT_PX;
    const sel = live.selected; const link = live.linking; const hover = live.hover;
    const dropOn = (live.drag && live.drag.target) || (live.wire && live.wire.target) || null;
    let drawn = 0;
    const visit = (it) => {
      if (it.seen === stamp) return;
      it.seen = stamp;
      if (ids && ids.has(it.id)) return; // drawn last, where the finger has it
      const p = it.p;
      if (p.x + W + 20 < left || p.x - 20 > right || p.y + H + 20 < top || p.y - 20 > bottom) return;
      drawn += 1;
      if (it.root || it.depth > 0 || it.id === sel || it.id === link || it.id === hover || it.id === dropOn) {
        specials.push(it);
        return;
      }
      let list = buckets.get(it.look);
      if (!list) buckets.set(it.look, list = []);
      list.push(it);
      if (st.title && (!it.lay || it.lay.gen !== gen)) layoutOf(it);
    };
    const gx0 = Math.floor(left / GRID); const gx1 = Math.floor(right / GRID);
    const gy0 = Math.floor(top / GRID); const gy1 = Math.floor(bottom / GRID);
    if ((gx1 - gx0 + 1) * (gy1 - gy0 + 1) * 2 < model.items.length) {
      for (let gx = gx0; gx <= gx1; gx += 1) {
        for (let gy = gy0; gy <= gy1; gy += 1) {
          const cell = model.grid.get(gx * 100003 + gy);
          if (cell) for (let i = 0; i < cell.length; i += 1) visit(cell[i]);
        }
      }
    } else {
      for (let i = 0; i < model.items.length; i += 1) visit(model.items[i]);
    }
    if (ids) {
      for (const id of ids) {
        const it = model.byId.get(id);
        if (!it) continue;
        const p = moved(it.p);
        if (p.x + W + 20 < left || p.x - 20 > right || p.y + H + 20 < top || p.y - 20 > bottom) continue;
        late.push(it);
        drawn += 1;
      }
    }
    if (tiny) {
      // a plain box per bubble, by colour; nothing inside
      ctx.lineWidth = 2;
      const boxes = (list, color) => {
        ctx.fillStyle = colors.dialog;
        for (let i = 0; i < list.length; i += 1) { const p = list[i].p; ctx.fillRect(p.x, p.y, W, H); }
        ctx.strokeStyle = color;
        for (let i = 0; i < list.length; i += 1) { const p = list[i].p; ctx.strokeRect(p.x, p.y, W, H); }
      };
      for (const list of buckets.values()) if (list.length) boxes(list, modeColor(list[0].mode));
      for (const it of [...specials, ...late]) {
        const p = ids && ids.has(it.id) ? moved(it.p) : it.p;
        ctx.fillStyle = colors.dialog; ctx.fillRect(p.x, p.y, W, H);
        ctx.strokeStyle = it.root && !it.space ? colors.accent : modeColor(it.mode); ctx.strokeRect(p.x, p.y, W, H);
      }
      return drawn;
    }
    for (const list of buckets.values()) if (list.length) drawPlain(list, st);
    for (const it of specials) drawBubble(it, it.p.x, it.p.y, st);
    for (const it of late) { const p = moved(it.p); drawBubble(it, p.x, p.y, st); }
    // the line being pulled from a port
    if (!opts.behind && live.wire && pos.has(live.wire.from)) {
      const w = live.wire;
      const from = pos.get(w.from);
      ctx.beginPath();
      if (w.port === "out") bez(ctx, from, { x: w.x, y: w.y - H / 2 });
      else bez(ctx, { x: w.x - W, y: w.y - H / 2 }, from);
      ctx.strokeStyle = colors.accent; ctx.lineWidth = 2; ctx.globalAlpha = 0.9; ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    return drawn;
  }

  // The space underneath an opened bubble stays where it was: painted once into its own canvas (again only when the
  // tree, the theme, the size or that space's camera changes) and copied dimmed under the open one every frame.
  function drawUnder(live) {
    const c = live.behindCam;
    const k = 0.93; // the space underneath sits a little further away, dimmed
    const cam = { x: (cssW * (1 - k)) / 2 + k * c.x, y: (cssH * (1 - k)) / 2 + k * c.y, s: k * c.s };
    const w = canvas.width; const h = canvas.height;
    const u = underKey;
    if (!u || u.model !== live.behind || u.x !== cam.x || u.y !== cam.y || u.s !== cam.s || u.w !== w || u.h !== h
      || u.gen !== gen || u.nolines !== !!live.nolines) {
      if (!under) under = document.createElement("canvas");
      if (under.width !== w || under.height !== h) { under.width = w; under.height = h; }
      const uctx = under.getContext("2d");
      uctx.setTransform(1, 0, 0, 1, 0, 0);
      uctx.clearRect(0, 0, w, h);
      ctx = uctx;
      try {
        drawSpace(live.behind, cam, { nolines: live.nolines }, { behind: true, moving: true, behindPlain: true, coarse: live.coarse });
      } finally { ctx = main; }
      underKey = { model: live.behind, x: cam.x, y: cam.y, s: cam.s, w, h, gen, nolines: !!live.nolines };
    }
    main.globalAlpha = 0.22;
    main.drawImage(under, 0, 0);
    main.globalAlpha = 1;
  }

  function draw() {
    raf = 0;
    if (dead) return;
    const t0 = performance.now();
    const live = getLive();
    if (!live || !live.model || !live.cam) return;
    if (!colors.dialog) refreshTokens();
    if (dpr !== targetDpr() || canvas.width === 0) resize();
    main.setTransform(1, 0, 0, 1, 0, 0);
    main.globalAlpha = 1;
    main.clearRect(0, 0, canvas.width, canvas.height);
    if (live.behind && live.behindCam) drawUnder(live);
    else if (under) { under.width = 0; under.height = 0; under = null; underKey = null; }
    stats.drawn = drawSpace(live.model, live.cam, live, { moving: live.moving, coarse: live.coarse });
    warm(live.model);
    const ms = performance.now() - t0;
    stats.ms = ms; stats.frames += 1; if (ms > stats.worst) stats.worst = ms;
    if (stats.times.length < 2000) stats.times.push(ms);
  }

  resize();
  refreshTokens();
  const ro = new ResizeObserver(() => { resize(); drawNow(); });
  ro.observe(host);
  // A theme switch, a colour setting, a stylesheet that finished loading: the tokens are read again.
  let tokTimer = 0;
  const later = () => { clearTimeout(tokTimer); tokTimer = setTimeout(refreshTokens, 30); };
  const onLoad = (e) => { if (e.target && e.target.tagName === "LINK") later(); };
  // A web font that arrives later keeps the same font string but other widths: measure again.
  const onFonts = () => { clearTimeout(tokTimer); tokTimer = setTimeout(() => refreshTokens(true), 30); };
  const mo = new MutationObserver(later);
  mo.observe(document.documentElement, { attributes: true });
  mo.observe(document.head, { childList: true, subtree: true, attributes: true, characterData: true });
  document.addEventListener("load", onLoad, true);
  const fontSet = document.fonts && document.fonts.addEventListener ? document.fonts : null;
  if (fontSet) fontSet.addEventListener("loadingdone", onFonts);
  let mq = null;
  try { mq = matchMedia("(prefers-color-scheme: dark)"); mq.addEventListener("change", later); } catch (e) { /* old browser */ }
  return {
    requestDraw, refreshTokens, stats, getLive,
    destroy() {
      dead = true; cancelAnimationFrame(raf); clearTimeout(tokTimer); ro.disconnect(); mo.disconnect();
      document.removeEventListener("load", onLoad, true);
      if (fontSet) fontSet.removeEventListener("loadingdone", onFonts);
      if (mq) mq.removeEventListener("change", later);
      if (under) { under.width = 0; under.height = 0; under = null; }
      probe.remove();
    },
  };
}
