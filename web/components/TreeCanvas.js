// The bubble map drawn on one <canvas> (default; ?treedebug=dom brings back the DOM map in CharacterTree.js).
// Why: the DOM map (hundreds of absolutely placed bubbles moved by one CSS transform) blanked out whole rectangles
// on a weak mobile GPU, and inserting culled bubbles lagged. Here nothing is a DOM node: every frame the visible part
// of the scene is painted with the camera transform, only when something changed (camera, data, selection, drag, theme).
// Colours and the font come from the theme's CSS tokens (see .claude/skills/style-bubble-map/SKILL.md): read once
// with getComputedStyle and again whenever the theme or the colours change.
// This module has no component and no state of its own: buildModel() turns the tree into plain arrays, hitTest()
// answers "what is under this point", createPainter() paints. CharacterTree.js owns the camera, pointers and API calls.

export const W = 168; // bubble size (the same numbers as CharacterTree.js)
export const H = 46;
const PAD = 18; // space around a nested group
const FAR = 0.45; // below this zoom: no ports, numbers or marks
const NO_TEXT = 0.28; // below this zoom titles are unreadable and skipped
const TINY = 0.2; // below this zoom bubbles are plain coloured boxes, batched by colour
const GRID = 240; // hit-test cell, world px

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
    title: scope ? rootBlock.title : character.name, avatar: !scope && !!character.avatar };
  items.push(root);
  const kidsOf = (id) => (kids.get(id) || []).filter((c) => pos.has(c.id));
  for (const b of kids.get(scope) || []) {
    if (pos.has(b.id) && !b.detached) edges.push({ a: rootId, b: b.id, band: inactive.has(b.id) ? "off" : band(b) });
  }
  const depthOf = (id) => 1 + Math.max(0, ...(kids.get(id) || []).map((c) => depthOf(c.id)));
  const visit = (parent) => {
    for (const b of kids.get(parent) || []) {
      if (!pos.has(b.id)) continue;
      const children = b.layout?.collapsed && !scope ? [] : kidsOf(b.id);
      const kidsN = (kids.get(b.id) || []).length;
      items.push({ id: b.id, b, mode: b.mode, title: b.title, inactive: inactive.has(b.id),
        orderN: orderOf.has(b.id) ? orderOf.get(b.id).n : 0, chance: b.chance ?? 100, vec: vectorized.has(b.id), kidsN,
        collapsed: !!b.layout?.collapsed,
        depth: b.layout?.collapsed && !scope && kidsN ? Math.min(3, depthOf(b.id)) : 0 });
      if (children.length && b.layout?.display === "nested") {
        groups.push({ id: b.id, mode: b.mode, members: [b.id, ...descendants(kids, b.id)].filter((id) => pos.has(id)) });
      } else {
        for (const c of children) edges.push({ a: b.id, b: c.id, band: inactive.has(c.id) ? "off" : band(c) });
      }
      if (children.length) visit(b.id);
    }
  };
  visit(scope);
  const inside = new Set(scope ? descendants(kids, scope) : pos.keys());
  const refs = links.filter(([a, b]) => inside.has(a) && inside.has(b) && pos.has(a) && pos.has(b));
  // A grid of cells for hit tests: bubbles never overlap, so a point sees a handful of them at most.
  const grid = new Map();
  for (const it of items) {
    const p = pos.get(it.id);
    if (!p) continue;
    for (let gx = Math.floor((p.x - 14) / GRID); gx <= Math.floor((p.x + W + 14) / GRID); gx += 1) {
      for (let gy = Math.floor((p.y - 14) / GRID); gy <= Math.floor((p.y + H + 14) / GRID); gy += 1) {
        const key = gx * 100003 + gy;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(it);
      }
    }
  }
  return { pos, items, edges, groups, refs, grid, rootId, scope };
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

const rr = (ctx, x, y, w, h, r) => {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, w, h, r);
  else { ctx.rect(x, y, w, h); }
};

// Colours and the font, as the theme sets them. A probe element resolves `var(...)` and `color-mix(...)` to real colours.
const TOKENS = {
  always: "--mode-always", search: "--mode-search", off: "--mode-off", line: "--line", muted: "--muted", accent: "--accent",
  c75: "--chance-75", c50: "--chance-50", c25: "--chance-25", c0: "--chance-0", dialog: "--dialog-bg", fg: "--fg",
};

export function createPainter(canvas, host, getLive) {
  const ctx = canvas.getContext("2d", { alpha: true });
  const probe = document.createElement("span");
  probe.setAttribute("aria-hidden", "true");
  probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;width:0;height:0;overflow:hidden";
  host.appendChild(probe);
  let colors = {};
  let font = { family: "system-ui, sans-serif", size: 16 };
  let raf = 0;
  let dead = false;
  const widths = new Map();
  const labels = new Map();
  const stats = { drawn: 0, ms: 0, frames: 0, worst: 0, times: [] };
  let cssW = 0; let cssH = 0; let dpr = 1;

  function refreshTokens() {
    const next = {};
    for (const [key, token] of Object.entries(TOKENS)) {
      probe.style.color = "";
      probe.style.color = `var(${token})`;
      next[key] = getComputedStyle(probe).color;
    }
    next.root = next.accent;
    colors = next;
    const cs = getComputedStyle(host);
    font = { family: cs.fontFamily || font.family, size: parseFloat(cs.fontSize) || 16 };
    widths.clear();
    labels.clear();
    requestDraw();
  }

  function resize() {
    dpr = window.devicePixelRatio || 1;
    const box = host.getBoundingClientRect();
    cssW = Math.max(1, box.width); cssH = Math.max(1, box.height);
    const w = Math.min(8192, Math.round(cssW * dpr)); const h = Math.min(8192, Math.round(cssH * dpr));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  }

  function requestDraw() {
    if (!raf && !dead) raf = requestAnimationFrame(draw);
  }

  const fontStr = (px, weight = 400) => `${weight} ${px}px ${font.family}`;
  const measure = (text, f) => {
    const key = f + "|" + text;
    let w = widths.get(key);
    if (w === undefined) {
      if (widths.size > 6000) widths.clear();
      ctx.font = f;
      w = ctx.measureText(text).width;
      widths.set(key, w);
    }
    return w;
  };
  const fit = (text, avail, f) => {
    if (avail <= 8) return "";
    const key = f + "|" + avail + "|" + text;
    let out = labels.get(key);
    if (out !== undefined) return out;
    if (labels.size > 6000) labels.clear();
    if (measure(text, f) <= avail) out = text;
    else {
      let lo = 0; let hi = text.length;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (measure(text.slice(0, mid) + "…", f) <= avail) lo = mid; else hi = mid - 1;
      }
      out = lo ? text.slice(0, lo) + "…" : "…";
    }
    labels.set(key, out);
    return out;
  };

  const modeColor = (m) => colors[m] || colors.line;

  function drawBubble(it, x, y, st) {
    const { s, far, text, ports, live } = st;
    const mc = it.root && !it.space ? colors.accent : modeColor(it.mode);
    const dragging = live.drag && live.drag.ids.has(it.id);
    let alpha = it.mode === "off" ? 0.55 : 1;
    if (it.inactive) alpha = 0.4;
    if (dragging) alpha *= 0.75;
    ctx.globalAlpha = alpha;
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
    if (it.inactive) ctx.setLineDash([5, 4]);
    ctx.stroke();
    if (it.inactive) ctx.setLineDash([]);
    if (text) {
      const weight = it.root ? 600 : 400;
      const f = fontStr(font.size, weight);
      ctx.font = f; ctx.textBaseline = "middle"; ctx.textAlign = "left";
      const cy = y + H / 2 + 0.5;
      let left = x + 14;
      let right = x + W - 14;
      if (it.root && !it.space) {
        const img = live.avatar;
        if (it.avatar && img && img.complete && img.naturalWidth) {
          ctx.save();
          ctx.beginPath(); ctx.arc(x + 8 + 15, cy - 0.5, 15, 0, Math.PI * 2); ctx.clip();
          ctx.drawImage(img, x + 8, cy - 15.5, 30, 30);
          ctx.restore();
          left = x + 8 + 30 + 6;
        }
      } else {
        ctx.beginPath(); ctx.arc(x + 14 + 6, y + H / 2, 6, 0, Math.PI * 2);
        ctx.fillStyle = mc; ctx.fill();
        left = x + 14 + 12 + 6;
      }
      if (!far && !it.root) {
        if (it.kidsN > 0) {
          const t = (it.collapsed ? "+" : "") + it.kidsN;
          const cf = fontStr(font.size * 0.75);
          const cw = measure(t, cf) + 14;
          rr(ctx, right - cw, cy - 9, cw, 18, 6);
          ctx.strokeStyle = colors.line; ctx.lineWidth = 1; ctx.stroke();
          ctx.font = cf; ctx.fillStyle = colors.muted; ctx.fillText(t, right - cw + 7, cy);
          right -= cw + 6;
        }
        if (it.vec) {
          const vf = fontStr(font.size * 0.9);
          const vw = measure("≈", vf);
          ctx.font = vf; ctx.fillStyle = colors.muted; ctx.fillText("≈", right - vw, cy);
          right -= vw + 6;
        }
        if (it.mode === "search" && it.chance < 100) {
          const t = it.chance + "%";
          const cf = fontStr(font.size * 0.7);
          const cw = measure(t, cf);
          ctx.font = cf; ctx.fillStyle = colors.muted; ctx.fillText(t, right - cw, cy);
          right -= cw + 6;
        }
      } else if (far && !it.root && it.kidsN > 0) {
        const t = (it.collapsed ? "+" : "") + it.kidsN;
        const cf = fontStr(font.size * 0.75);
        const cw = measure(t, cf) + 14;
        ctx.font = cf; ctx.fillStyle = colors.muted; ctx.fillText(t, right - cw + 7, cy);
        right -= cw + 6;
      }
      ctx.font = f; ctx.fillStyle = colors.fg;
      ctx.fillText(fit(it.title || "", right - left, f), left, cy);
      if (!far && it.orderN) {
        const of = fontStr(font.size * 0.65);
        const t = String(it.orderN);
        const ow = Math.max(16, measure(t, of) + 10);
        const ox = x + W - 2 - 14 - ow; const oy = y - 7;
        rr(ctx, ox, oy, ow, 16, 6);
        ctx.fillStyle = colors.dialog; ctx.fill();
        ctx.strokeStyle = mc; ctx.lineWidth = 1; ctx.stroke();
        ctx.font = of; ctx.fillStyle = colors.muted; ctx.textAlign = "center";
        ctx.fillText(t, ox + ow / 2, oy + 8.5);
        ctx.textAlign = "left";
      }
    }
    if (ports && (!it.root || it.space)) {
      const shown = st.coarse || selected || target || live.hover === it.id;
      if (shown) {
        const r = st.coarse ? 10 : 7;
        ctx.globalAlpha = st.coarse && !(selected || target || live.hover === it.id) ? 0.8 * alpha : alpha;
        const dot = (px) => {
          ctx.beginPath(); ctx.arc(px, y + H / 2, r, 0, Math.PI * 2);
          ctx.fillStyle = colors.dialog; ctx.fill();
          ctx.strokeStyle = mc; ctx.lineWidth = 2; ctx.stroke();
        };
        if (!it.space) dot(x);
        dot(x + W);
      }
    }
    ctx.globalAlpha = 1;
    void s;
  }

  const bez = (a, b) => {
    const x1 = a.x + W; const y1 = a.y + H / 2; const x2 = b.x; const y2 = b.y + H / 2; const mx = (x1 + x2) / 2;
    ctx.moveTo(x1, y1); ctx.bezierCurveTo(mx, y1, mx, y2, x2, y2);
  };

  // One space of the scene with `cam` {x, y, s}; returns how many bubbles were drawn.
  function drawSpace(model, cam, live, opts) {
    const s = cam.s;
    ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * cam.x, dpr * cam.y);
    const m = 24 + 18;
    const left = -cam.x / s - m; const top = -cam.y / s - m;
    const right = left + cssW / s + 2 * m; const bottom = top + cssH / s + 2 * m;
    const { pos } = model;
    const drag = opts.behind ? null : live.drag;
    const at = (id) => {
      const p = pos.get(id);
      return drag && drag.ids.has(id) ? { x: p.x + drag.dx, y: p.y + drag.dy } : p;
    };
    const far = s < FAR;
    const plain = far || opts.moving;
    // nested groups
    for (const g of model.groups) {
      let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
      for (const id of g.members) {
        const p = at(id);
        if (p.x < x0) x0 = p.x; if (p.y < y0) y0 = p.y; if (p.x > x1) x1 = p.x; if (p.y > y1) y1 = p.y;
      }
      const bx = x0 - PAD; const by = y0 - PAD; const bw = x1 - x0 + W + 2 * PAD; const bh = y1 - y0 + H + 2 * PAD;
      if (bx + bw < left || bx > right || by + bh < top || by > bottom) continue;
      const mc = modeColor(g.mode);
      rr(ctx, bx + 1, by + 1, bw - 2, bh - 2, 21);
      ctx.globalAlpha = 0.07; ctx.fillStyle = mc; ctx.fill();
      ctx.globalAlpha = 1; ctx.strokeStyle = mc; ctx.lineWidth = 2;
      if (!plain) ctx.setLineDash([6, 5]);
      ctx.stroke(); ctx.setLineDash([]);
    }
    // link arcs, under the lines
    if (model.refs.length) {
      ctx.beginPath();
      for (const [a, b] of model.refs) {
        const p = at(a); const q = at(b);
        if (Math.max(p.x, q.x) + W < left || Math.min(p.x, q.x) > right || Math.max(p.y, q.y) + H < top || Math.min(p.y, q.y) > bottom) continue;
        const x1 = p.x + W / 2; const y1 = p.y + H / 2; const x2 = q.x + W / 2; const y2 = q.y + H / 2;
        const dx = x2 - x1; const dy = y2 - y1; const len = Math.hypot(dx, dy) || 1;
        let nx = -dy / len; let ny = dx / len;
        if (nx < 0 || (nx === 0 && ny < 0)) { nx = -nx; ny = -ny; }
        const bow = W * 1.3 + len * 0.3;
        ctx.moveTo(x1, y1);
        ctx.quadraticCurveTo((x1 + x2) / 2 + nx * bow, (y1 + y2) / 2 + ny * bow, x2, y2);
      }
      ctx.strokeStyle = colors.accent; ctx.lineWidth = 2.5; ctx.lineCap = plain ? "butt" : "round";
      ctx.globalAlpha = plain ? 0.35 : 0.5;
      if (!plain) ctx.setLineDash([2, 6]);
      ctx.stroke(); ctx.setLineDash([]); ctx.lineCap = "butt"; ctx.globalAlpha = 1;
    }
    // parent lines, one stroke per colour band
    const byBand = {};
    for (const e of model.edges) {
      const p = at(e.a); const q = at(e.b);
      if (Math.max(p.x, q.x) + W < left || Math.min(p.x, q.x) > right || Math.max(p.y, q.y) + H < top || Math.min(p.y, q.y) > bottom) continue;
      (byBand[e.band] || (byBand[e.band] = [])).push(p, q);
    }
    for (const key of ["off", "c100", "c75", "c50", "c25", "c0"]) {
      const list = byBand[key];
      if (!list) continue;
      ctx.beginPath();
      for (let i = 0; i < list.length; i += 2) bez(list[i], list[i + 1]);
      const chance = key !== "c100" && key !== "off";
      ctx.strokeStyle = chance ? colors[key] : colors.muted;
      ctx.lineWidth = chance ? 2 : 1.5;
      ctx.globalAlpha = key === "off" ? 0.35 : chance ? 0.9 : 0.6;
      if (key === "off" && !plain) ctx.setLineDash([4, 5]);
      ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
    // bubbles
    let drawn = 0;
    const st = { s, far, text: s >= NO_TEXT && !opts.behindPlain, ports: !far && !opts.behind, coarse: opts.coarse, live };
    const late = [];
    if (s < TINY) {
      // a plain box per bubble, batched by colour
      const byColor = {};
      for (const it of model.items) {
        const p = at(it.id);
        if (p.x + W < left || p.x > right || p.y + H < top || p.y > bottom) continue;
        const key = it.root && !it.space ? "root" : it.mode;
        (byColor[key] || (byColor[key] = [])).push(p);
        drawn += 1;
      }
      for (const [key, list] of Object.entries(byColor)) {
        ctx.fillStyle = colors.dialog; ctx.strokeStyle = modeColor(key === "root" ? "accent" : key); ctx.lineWidth = 2;
        ctx.beginPath();
        for (const p of list) ctx.rect(p.x, p.y, W, H);
        ctx.fill(); ctx.stroke();
      }
      return drawn;
    }
    for (const it of model.items) {
      const p = at(it.id);
      if (p.x + W + 20 < left || p.x - 20 > right || p.y + H + 20 < top || p.y - 20 > bottom) continue;
      if (drag && drag.ids.has(it.id)) { late.push([it, p]); continue; }
      drawBubble(it, p.x, p.y, st);
      drawn += 1;
    }
    for (const [it, p] of late) { drawBubble(it, p.x, p.y, st); drawn += 1; }
    // the line being pulled from a port
    if (!opts.behind && live.wire && pos.has(live.wire.from)) {
      const w = live.wire;
      const from = pos.get(w.from);
      ctx.beginPath();
      if (w.port === "out") bez(from, { x: w.x, y: w.y - H / 2 });
      else bez({ x: w.x - W, y: w.y - H / 2 }, from);
      ctx.strokeStyle = colors.accent; ctx.lineWidth = 2; ctx.globalAlpha = 0.9; ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
    return drawn;
  }

  function draw() {
    raf = 0;
    if (dead) return;
    const t0 = performance.now();
    const live = getLive();
    if (!live || !live.model || !live.cam) return;
    if (!colors.dialog) refreshTokens();
    if (dpr !== (window.devicePixelRatio || 1) || canvas.width === 0) resize();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.lineJoin = "round";
    if (live.behind && live.behindCam) {
      const c = live.behindCam;
      const k = 0.93; // the space underneath sits a little further away, dimmed
      ctx.globalAlpha = 0.22;
      drawSpace(live.behind, { x: cssW * (1 - k) / 2 + k * c.x, y: cssH * (1 - k) / 2 + k * c.y, s: k * c.s }, live,
        { behind: true, moving: true, behindPlain: true, coarse: live.coarse });
      ctx.globalAlpha = 1;
    }
    stats.drawn = drawSpace(live.model, live.cam, live, { moving: live.moving, coarse: live.coarse });
    const ms = performance.now() - t0;
    stats.ms = ms; stats.frames += 1; if (ms > stats.worst) stats.worst = ms;
    if (stats.times.length < 2000) stats.times.push(ms);
  }

  resize();
  refreshTokens();
  const ro = new ResizeObserver(() => { resize(); requestDraw(); });
  ro.observe(host);
  // A theme switch, a colour setting, a stylesheet that finished loading: the tokens are read again.
  let tokTimer = 0;
  const later = () => { clearTimeout(tokTimer); tokTimer = setTimeout(refreshTokens, 30); };
  const onLoad = (e) => { if (e.target && e.target.tagName === "LINK") later(); };
  const mo = new MutationObserver(later);
  mo.observe(document.documentElement, { attributes: true });
  mo.observe(document.head, { childList: true, subtree: true, attributes: true, characterData: true });
  document.addEventListener("load", onLoad, true);
  let mq = null;
  try { mq = matchMedia("(prefers-color-scheme: dark)"); mq.addEventListener("change", later); } catch (e) { /* old browser */ }
  return {
    requestDraw, refreshTokens, stats, getLive,
    destroy() {
      dead = true; cancelAnimationFrame(raf); clearTimeout(tokTimer); ro.disconnect(); mo.disconnect();
      document.removeEventListener("load", onLoad, true);
      if (mq) mq.removeEventListener("change", later);
      probe.remove();
    },
  };
}
