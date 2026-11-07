// Groups (characters, lorebooks, chats): a free-text name that gathers items; each kind has its own groups.
// An item is in one group or none («Без группы»); deleting a group never deletes its items.
//   <${GroupedItems} kind="character" items=${list} idOf labelOf activeId renderRow onPress onError />
// shows the items as a list with foldable groups or as bubbles (the look of the character bubble map:
// the same .tree-map / .bubble / .tree-group classes, the same sizes and culling constants, exported by
// CharacterTree.js). The choice of view and the folded groups are remembered per kind on this device.
// `renderRow(item, tools)` returns the <li> of the list view; `onPress(item, element, tools)` is a tap
// on a bubble; `tools.move()` opens «В группу…» for that item (put it into a menu).
// Server: services/groups.py, routes /api/groups. Bubble view: only the bubbles in view are in the page.
import { html, useState, useEffect, useMemo, useRef, useCallback } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { W, H, GAP, MARGIN_PX, MOVE_REDRAW, FAR_ZOOM } from "./CharacterTree.js";
import { Icon, IconButton } from "./Icon.js";
import { MenuButton, openMenu } from "./Menu.js";
import { ask, askText } from "./Dialogs.js";
import { backdrop, useEscape } from "../core/keys.js";

const NONE = "";
const NONE_NAME = "Без группы";
const PAD = 18; // space around the bubbles inside a group frame
const HEAD = 40; // the group's name above its bubbles
const SHELF_W = 1700; // groups stand side by side up to this width, then start a new row
const MIN_ZOOM = 0.12;
const MAX_ZOOM = 2.5;

const read = (key, fallback) => { try { return localStorage.getItem(key) ?? fallback; } catch (e) { return fallback; } };
const write = (key, value) => { try { localStorage.setItem(key, value); } catch (e) { /* storage off */ } };

// --- server -------------------------------------------------------------------------------------

export function useGroups(kind, onError = () => {}) {
  const [data, setData] = useState({ groups: [], members: {} });
  const reload = useCallback(() => api.get(`/groups?kind=${kind}`)
    .then((d) => setData({ groups: d.groups, members: d.members }))
    .catch((e) => onError(e.message)), [kind]);
  useEffect(() => {
    reload();
    window.addEventListener("tavern:groups", reload); // the assistant changed groups
    return () => window.removeEventListener("tavern:groups", reload);
  }, [reload]);
  const guarded = async (job) => { try { return await job(); } catch (e) { onError(e.message); return null; } };
  return {
    groups: data.groups,
    members: data.members,
    reload,
    create: (name) => guarded(() => api.post("/groups", { kind, name })).then(async (g) => { await reload(); return g; }),
    rename: (id, name) => guarded(() => api.put(`/groups/${id}`, { name })).then(reload),
    remove: (id) => guarded(() => api.del(`/groups/${id}`)).then(reload),
    assign: (ids, groupId) => guarded(() => api.post("/groups/assign", { kind, item_ids: ids, group_id: groupId }))
      .then((d) => { if (d) setData({ groups: d.groups, members: d.members }); }),
  };
}

// --- layout of the bubble view ------------------------------------------------------------------

// sections: [{id, name, items: [item]}] -> frames and the bubbles with their places in the world.
export function layoutGroups(sections, idOf) {
  const frames = [];
  const bubbles = [];
  let x = 0; let y = 0; let rowBottom = 0; let width = 0;
  for (const s of sections) {
    const n = s.items.length;
    const cols = Math.max(1, Math.min(10, Math.ceil(Math.sqrt(n * 1.6))));
    const rows = Math.ceil(n / cols);
    const w = cols * W + (cols - 1) * GAP + 2 * PAD;
    const h = HEAD + (rows ? rows * H + (rows - 1) * GAP : 0) + PAD;
    if (x > 0 && x + w > SHELF_W) { x = 0; y = rowBottom + 2 * GAP; rowBottom = y; }
    frames.push({ id: s.id, name: s.name, count: n, x, y, w, h });
    s.items.forEach((item, i) => {
      bubbles.push({ id: idOf(item), item, x: x + PAD + (i % cols) * (W + GAP), y: y + HEAD + Math.floor(i / cols) * (H + GAP) });
    });
    x += w + 2 * GAP;
    rowBottom = Math.max(rowBottom, y + h);
    width = Math.max(width, x);
  }
  return { frames, bubbles, width, height: rowBottom };
}

// --- the bubble view ----------------------------------------------------------------------------

// The camera works like the character map's: pan and zoom move one layer without a re-render; which
// bubbles are in the page is chosen again only after the camera has gone a good way, or settled.
function GroupMap({ sections, idOf, labelOf, activeId, big = false, onPress, onGroupMenu }) {
  const layout = useMemo(() => layoutGroups(sections, idOf), [sections]);
  const [camera, setCamera] = useState({ x: PAD, y: PAD, s: 1 });
  const [size, setSize] = useState({ w: 320, h: 360 });
  const viewport = useRef(null);
  const world = useRef(null);
  const cam = useRef(camera);
  const drawn = useRef(camera);
  const frame = useRef(0);
  const settle = useRef(0);
  const gesture = useRef(null);
  const fitted = useRef(false);

  const commit = () => {
    clearTimeout(settle.current);
    cancelAnimationFrame(frame.current);
    frame.current = 0;
    drawn.current = { ...cam.current };
    setCamera(drawn.current);
  };
  const apply = (next) => {
    cam.current = next;
    if (world.current) world.current.style.transform = `translate(${next.x}px, ${next.y}px) scale(${next.s})`;
    const d = drawn.current;
    const far = Math.abs(next.x - d.x) > MOVE_REDRAW || Math.abs(next.y - d.y) > MOVE_REDRAW
      || Math.abs(Math.log(next.s / d.s)) > 0.12;
    clearTimeout(settle.current);
    if (far) { if (!frame.current) frame.current = requestAnimationFrame(commit); }
    else settle.current = setTimeout(commit, 120);
  };
  useEffect(() => () => { clearTimeout(settle.current); cancelAnimationFrame(frame.current); }, []);
  const zoomAt = (px, py, factor) => {
    const c = cam.current;
    const s = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, c.s * factor));
    apply({ s, x: px - ((px - c.x) * s) / c.s, y: py - ((py - c.y) * s) / c.s });
  };

  // Follow the size of the box; the first time, fit the width.
  useEffect(() => {
    const el = viewport.current;
    if (!el) return undefined;
    const measure = (w, h) => {
      setSize({ w, h });
      if (!fitted.current && w > 0 && layout.width > 0) {
        fitted.current = true;
        const s = Math.min(1, Math.max(MIN_ZOOM, (w - 2 * PAD) / (layout.width - 2 * GAP)));
        const start = { x: PAD, y: PAD, s };
        cam.current = start; drawn.current = start;
        setCamera(start);
      }
    };
    const box = el.getBoundingClientRect();
    measure(box.width, box.height);
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width, entry.contentRect.height));
    observer.observe(el);
    return () => observer.disconnect();
  }, [layout.width > 0]);

  useEffect(() => {
    const el = viewport.current;
    if (!el) return undefined;
    const wheel = (e) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      if (e.ctrlKey || Math.abs(e.deltaY) >= Math.abs(e.deltaX)) zoomAt(e.clientX - box.left, e.clientY - box.top, Math.exp(-e.deltaY * 0.0015));
      else apply({ ...cam.current, x: cam.current.x - e.deltaX });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, []);

  const onPointerDown = (e) => {
    if (e.button > 0) return;
    viewport.current.setPointerCapture(e.pointerId);
    const g = gesture.current || { pointers: new Map() };
    g.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (g.pointers.size === 1) {
      g.iid = e.target.closest("[data-iid]")?.dataset.iid ?? null;
      g.gid = e.target.closest("[data-gid]")?.dataset.gid ?? null;
      g.mode = "pan";
      g.start = { x: e.clientX, y: e.clientY, cam: { ...cam.current } };
    } else if (g.pointers.size === 2) {
      const [a, b] = [...g.pointers.values()];
      g.mode = "pinch";
      g.pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, s: cam.current.s };
    }
    gesture.current = g;
  };
  const onPointerMove = (e) => {
    const g = gesture.current;
    if (!g || !g.pointers.has(e.pointerId)) return;
    g.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (g.mode === "pinch" && g.pointers.size === 2) {
      const box = viewport.current.getBoundingClientRect();
      const [a, b] = [...g.pointers.values()];
      const factor = (g.pinch.s * Math.hypot(a.x - b.x, a.y - b.y)) / g.pinch.dist / cam.current.s;
      zoomAt((a.x + b.x) / 2 - box.left, (a.y + b.y) / 2 - box.top, factor);
    } else if (g.mode === "pan") {
      apply({ ...cam.current, x: g.start.cam.x + e.clientX - g.start.x, y: g.start.cam.y + e.clientY - g.start.y });
    }
  };
  const onPointerUp = (e) => {
    const g = gesture.current;
    if (!g) return;
    g.pointers.delete(e.pointerId);
    if (g.pointers.size > 0) { if (g.mode === "pinch") g.mode = "done"; return; }
    gesture.current = null;
    const c0 = cam.current; const d0 = drawn.current;
    if (c0.x !== d0.x || c0.y !== d0.y || c0.s !== d0.s) commit();
    const still = Math.hypot(e.clientX - g.start.x, e.clientY - g.start.y) < 6;
    if (g.mode !== "pan" || !still || e.type === "pointercancel") return;
    if (g.iid !== null) {
      const hit = layout.bubbles.find((b) => b.id === g.iid);
      const el = viewport.current.querySelector(`[data-iid="${CSS.escape(g.iid)}"]`);
      if (hit && el) onPress(hit.item, el);
    } else if (g.gid) {
      const el = viewport.current.querySelector(`[data-gid="${CSS.escape(g.gid)}"]`);
      if (el) onGroupMenu(g.gid, el);
    }
  };

  // Only what is near the screen goes into the page.
  const margin = MARGIN_PX / camera.s;
  const x0 = -camera.x / camera.s - margin; const x1 = (size.w - camera.x) / camera.s + margin;
  const y0 = -camera.y / camera.s - margin; const y1 = (size.h - camera.y) / camera.s + margin;
  const frames = layout.frames.filter((f) => f.x < x1 && f.x + f.w > x0 && f.y < y1 && f.y + f.h > y0);
  const bubbles = layout.bubbles.filter((b) => b.x < x1 && b.x + W > x0 && b.y < y1 && b.y + H > y0);

  return html`<div class=${"tree-map group-map" + (big ? " big" : "") + (camera.s < FAR_ZOOM ? " far" : "")}
    ref=${viewport} data-slot="group-map" onPointerDown=${onPointerDown} onPointerMove=${onPointerMove}
    onPointerUp=${onPointerUp} onPointerCancel=${onPointerUp}>
    <div class="tree-world" ref=${world}
      style=${`transform: translate(${camera.x}px, ${camera.y}px) scale(${camera.s})`}>
      ${frames.map((f) => html`<div key=${"f" + f.id} class="tree-group group-frame"
        style=${`left:${f.x}px;top:${f.y}px;width:${f.w}px;height:${f.h}px`}>
        <button type="button" class="group-label" data-gid=${f.id || null} title=${f.name}
          aria-haspopup=${f.id ? "menu" : null}><span class="group-label-name">${f.name}</span>
          <span class="muted">${f.count}</span></button></div>`)}
      ${bubbles.map((b) => html`<div key=${"i" + b.id} data-iid=${b.id}
        class=${"bubble group-item" + (activeId === b.id ? " selected" : "")}
        style=${`transform: translate(${b.x}px, ${b.y}px)`} title=${labelOf(b.item)}>
        <span class="bubble-title">${labelOf(b.item)}</span></div>`)}
    </div>
    ${layout.bubbles.length === 0 && html`<p class="tree-empty muted">Здесь пока пусто</p>`}
  </div>`;
}

// --- «В группу…» --------------------------------------------------------------------------------

function GroupPicker({ title, groups, current, onPick, onNew, onClose }) {
  useEscape(onClose);
  return html`<div class="overlay" ...${backdrop(onClose)}>
    <div class="dialog stack group-picker" data-slot="dialog" role="dialog" aria-modal="true" aria-label="В группу">
      <strong class="dialog-name" title=${title}>В группу: ${title}</strong>
      <ul class="list">
        <li><button class="item" aria-pressed=${current === NONE} onClick=${() => onPick(null)}>${NONE_NAME}</button></li>
        ${groups.map((g) => html`<li key=${g.id}><button class="item" title=${g.name} aria-pressed=${current === g.id}
          onClick=${() => onPick(g.id)}>${g.name}</button></li>`)}
        <li><button class="item" onClick=${onNew}>Новая группа…</button></li>
      </ul>
      <div class="row"><span class="spacer"></span><button onClick=${onClose}>Закрыть</button></div>
    </div></div>`;
}

// --- the section body ---------------------------------------------------------------------------

export function GroupedItems({ kind, items, idOf, labelOf, activeId = null, renderRow, onPress, onError }) {
  const g = useGroups(kind, onError);
  const [view, setView] = useState(() => (read(`tavern.groupView.${kind}`, "list") === "bubbles" ? "bubbles" : "list"));
  const [closed, setClosed] = useState(() => {
    try { return new Set(JSON.parse(read(`tavern.groupClosed.${kind}`, "[]"))); } catch (e) { return new Set(); }
  });
  const [moving, setMoving] = useState(null); // the item whose «В группу…» is open
  const [big, setBig] = useState(false);
  useEffect(() => write(`tavern.groupView.${kind}`, view), [view]);
  const keepClosed = (next) => { setClosed(next); write(`tavern.groupClosed.${kind}`, JSON.stringify([...next])); };

  // Items by group; a group an item names but that is gone counts as «Без группы».
  const sections = useMemo(() => {
    const known = new Map(g.groups.map((x) => [x.id, { id: x.id, name: x.name, items: [] }]));
    const loose = { id: NONE, name: NONE_NAME, items: [] };
    for (const item of items) (known.get(g.members[idOf(item)]) || loose).items.push(item);
    return [...known.values(), loose];
  }, [items, g.groups, g.members]);
  const grouped = g.groups.length > 0;
  const shown = useMemo(() => (grouped ? sections : sections.filter((s) => s.id === NONE)), [sections, grouped]);

  const newGroup = async () => {
    const name = ((await askText("Название группы", { ok: "Создать", hint: "Любой текст, например «Мир РПГ»" })) || "").trim();
    return name ? g.create(name) : null;
  };
  const groupMenu = (id) => {
    const group = g.groups.find((x) => x.id === id);
    if (!group) return [];
    return [
      { icon: "pencil", label: "Переименовать", onClick: async () => {
        const name = ((await askText("Название группы", { value: group.name })) || "").trim();
        if (name && name !== group.name) g.rename(id, name);
      } },
      { icon: "trash-2", label: "Удалить группу", danger: true, onClick: async () => {
        if (await ask(`Удалить группу «${group.name}»?`, { ok: "Удалить", danger: true, hint: `Всё, что в ней, останется и перейдёт в «${NONE_NAME}».` })) g.remove(id);
      } },
    ];
  };
  const tools = (item) => ({ move: () => setMoving(item) });
  const pick = async (groupId) => {
    const item = moving;
    setMoving(null);
    await g.assign([idOf(item)], groupId);
  };
  const pickNew = async () => {
    const item = moving;
    const made = await newGroup();
    setMoving(null);
    if (made) await g.assign([idOf(item)], made.id);
  };

  if (items.length === 0 && !grouped) return null;
  const toggleAll = () => keepClosed(closed.size ? new Set() : new Set(g.groups.map((x) => x.id).concat(NONE)));
  const mapProps = { sections: shown, idOf, labelOf, activeId,
    onPress: (item, el) => onPress(item, el, tools(item)),
    onGroupMenu: (id, el) => openMenu(el, groupMenu(id)) };

  return html`<div class="groups" data-slot="groups" data-kind=${kind}>
    <div class="group-bar">
      <span class="muted group-bar-note">${grouped ? `Групп: ${g.groups.length}` : "Групп нет"}</span>
      <span class="spacer"></span>
      <${IconButton} icon=${view === "list" ? "waypoints" : "menu"} data-slot="group-view"
        label=${view === "list" ? "Показать пузырями" : "Показать списком"} onClick=${() => setView(view === "list" ? "bubbles" : "list")} />
      ${view === "bubbles" && html`<${IconButton} icon="maximize-2" label="Открыть крупно" onClick=${() => setBig(true)} />`}
      <${MenuButton} label="Группы" icon="library" data-slot="group-menu" items=${() => [
        { icon: "plus", label: "Новая группа", onClick: newGroup },
        ...(view === "list" && grouped ? [{ icon: "chevron-down", label: closed.size ? "Развернуть все" : "Свернуть все", onClick: toggleAll }] : []),
      ]} />
    </div>
    ${view === "bubbles" && html`<${GroupMap} ...${mapProps} />`}
    ${view === "list" && !grouped && html`<ul class="list">${items.map((item) => renderRow(item, tools(item)))}</ul>`}
    ${view === "list" && grouped && shown.map((s) => {
      const open = !closed.has(s.id);
      return html`<div key=${s.id || "none"} class="group" data-slot="group">
        <div class="group-head">
          <button class="group-fold" aria-expanded=${open} title=${s.name} onClick=${() => {
            const next = new Set(closed);
            if (open) next.add(s.id); else next.delete(s.id);
            keepClosed(next);
          }}><${Icon} name=${open ? "chevron-down" : "chevron-right"} /><span class="group-name">${s.name}</span>
            <span class="muted count">${s.items.length}</span></button>
          ${s.id !== NONE && html`<${MenuButton} label=${"Действия с группой " + s.name} items=${() => groupMenu(s.id)} />`}
        </div>
        ${open && s.items.length > 0 && html`<ul class="list">${s.items.map((item) => renderRow(item, tools(item)))}</ul>`}
        ${open && s.items.length === 0 && html`<p class="muted group-empty">Пусто: перенесите сюда через «В группу…»</p>`}
      </div>`;
    })}
    ${moving && html`<${GroupPicker} title=${labelOf(moving)} groups=${g.groups} current=${g.members[idOf(moving)] || NONE}
      onPick=${pick} onNew=${pickNew} onClose=${() => setMoving(null)} />`}
    ${big && html`<${BigMap} onClose=${() => setBig(false)}><${GroupMap} ...${mapProps} big=${true} /><//>`}
  </div>`;
}

function BigMap({ onClose, children }) {
  useEscape(onClose);
  return html`<div class="overlay" ...${backdrop(onClose)}>
    <div class="dialog stack tree-dialog group-big" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Группы пузырями">
      <div class="side-head"><strong class="dialog-name">Группы</strong><span class="spacer"></span>
        <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
      ${children}
    </div></div>`;
}
