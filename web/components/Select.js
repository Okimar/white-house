// Our own drop-down in place of the browser's <select>: the native list is dark and plain on Android and no
// theme can restyle it. Drop-in: the same props and the same <option> / <optgroup> children.
//   <${Select} value=${x} onChange=${(e) => set(e.target.value)} aria-label="…"> <option value="a">A</option> … <//>
// onChange gets { target: { value }, currentTarget: { value } } (a string, as with a native select).
// The list lives at the end of <body> (like Menu.js), opens under the field or above it, and is a
// listbox with options. Long lists (more than FILTER_FROM options) get a filter field.
import { html, render, useEffect, useLayoutEffect, useMemo, useRef, useState } from "../vendor/preact-htm.mjs";
import { Icon } from "./Icon.js";
import { holdTouch, keepRowInList, placeList, visibleArea } from "../core/popup.js";

export const FILTER_FROM = 10;

let host = null;
let closeCurrent = null;

// <option> / <optgroup> children -> flat rows: { value, label, text, disabled } and { group }.
function plain(node) {
  if (node === null || node === undefined || node === false || node === true) return "";
  if (Array.isArray(node)) return node.map(plain).join("");
  if (typeof node === "object") return plain(node.props && node.props.children);
  return String(node);
}

function collect(children, out, group) {
  for (const node of [].concat(children)) {
    if (Array.isArray(node)) { collect(node, out, group); continue; }
    if (!node || typeof node !== "object") continue;
    const props = node.props || {};
    if (node.type === "optgroup") {
      out.push({ group: props.label || "" });
      collect(props.children, out, props.label || "");
    } else if (node.type === "option") {
      const text = plain(props.children);
      out.push({ value: String(props.value ?? text), label: props.children, text, disabled: Boolean(props.disabled), inGroup: Boolean(group) });
    } else if (node.props && node.props.children !== undefined) {
      collect(node.props.children, out, group); // fragments and wrappers
    }
  }
  return out;
}

function Panel({ anchor, rows, value, onPick, onClose }) {
  const panel = useRef(null);
  const list = useRef(null);
  const [query, setQuery] = useState("");
  const options = useMemo(() => rows.filter((r) => r.group === undefined), [rows]);
  const filtering = options.length > FILTER_FROM;
  const q = query.trim().toLowerCase();
  const shown = useMemo(() => {
    if (!q) return rows;
    let currentGroup = null;
    const groupMatches = new Set();
    for (const r of rows) {
      if (r.group !== undefined) currentGroup = r.group;
      else if (currentGroup && r.text.toLowerCase().includes(q)) groupMatches.add(currentGroup);
    }
    return rows.filter((r) => {
      if (r.group !== undefined) return groupMatches.has(r.group);
      return r.text.toLowerCase().includes(q);
    });
  }, [rows, q]);
  const enabled = useMemo(() => shown.filter((r) => r.group === undefined && !r.disabled), [shown]);
  const selectedAt = useMemo(() => enabled.findIndex((r) => r.value === value), [enabled, value]);
  const [active, setActive] = useState(selectedAt);
  const state = useRef({});
  state.current = { enabled, active, onPick, onClose };

  useEffect(() => { setActive(q ? 0 : Math.max(0, selectedAt)); }, [q, selectedAt]);

  const side = useRef(null); // "down" / "up": chosen once, kept while it has room
  const touching = useRef(false); // a finger is on the list: nothing moves under it
  const place = () => {
    const el = panel.current;
    if (!el || !anchor.isConnected || touching.current) return;
    const box = anchor.getBoundingClientRect();
    const margin = 8;
    el.style.maxHeight = "none";
    el.style.minWidth = `${Math.min(box.width, window.innerWidth - 2 * margin)}px`;
    const at = placeList({ box, natural: el.offsetHeight, width: el.offsetWidth, view: visibleArea(),
      pageWidth: window.innerWidth, side: side.current, margin, least: 120 });
    side.current = at.side;
    el.dataset.side = at.side;
    el.style.maxHeight = `${at.maxHeight}px`;
    el.style.left = `${at.left}px`;
    el.style.top = `${at.top}px`;
  };
  useLayoutEffect(place); // after every render: options may change while it is open

  useEffect(() => {
    if (list.current) keepRowInList(list.current, list.current.querySelector(".active") || list.current.querySelector('[aria-selected="true"]'));
  }, [active, q]);

  // A layout effect, not a plain one: the list is rendered from inside another component's effect, and a plain
  // effect of a list made there can be left waiting, so no listener (outside tap, scroll, keys) was ever set.
  useLayoutEffect(() => {
    const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    if (filtering && !coarse) panel.current.querySelector("input").focus({ preventScroll: true });
    else panel.current.focus({ preventScroll: true });
    const outside = (e) => { if (!panel.current.contains(e.target) && !anchor.contains(e.target)) state.current.onClose(); };
    const key = (e) => {
      const s = state.current;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        s.onClose();
        anchor.focus();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (s.enabled.length) setActive((a) => (Math.max(a, 0) + (e.key === "ArrowDown" ? 1 : -1) + s.enabled.length) % s.enabled.length);
      } else if (e.key === "PageDown" || e.key === "PageUp") {
        e.preventDefault();
        const delta = e.key === "PageDown" ? 5 : -5;
        if (s.enabled.length) setActive((a) => Math.max(0, Math.min(s.enabled.length - 1, a + delta)));
      } else if (e.key === "Home" || e.key === "End") {
        if (document.activeElement && document.activeElement.tagName === "INPUT") return;
        e.preventDefault();
        setActive(e.key === "Home" ? 0 : s.enabled.length - 1);
      } else if (e.key === "Enter") {
        const row = s.enabled[s.active];
        if (row) { e.preventDefault(); s.onPick(row.value); anchor.focus(); }
      } else if (e.key === "Tab") {
        s.onClose();
      }
    };
    // The browser scrolls the focused filter field into view while the keyboard opens: not the user's scroll.
    let moved = 0;
    const resized = () => { moved = Date.now(); place(); };
    const scrolled = (e) => {
      if (panel.current.contains(e.target) || touching.current) return;
      if (Date.now() - moved < 500) place();
      else state.current.onClose();
    };
    const down = (e) => { if (panel.current.contains(e.target)) touching.current = true; };
    const up = () => { touching.current = false; };
    const unhold = holdTouch(panel.current, list.current);
    const vv = window.visualViewport;
    document.addEventListener("pointerdown", down, true);
    document.addEventListener("pointerup", up, true);
    document.addEventListener("pointercancel", up, true);
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("resize", resized);
    document.addEventListener("scroll", scrolled, true);
    if (vv) { vv.addEventListener("resize", resized); vv.addEventListener("scroll", resized); }
    return () => {
      unhold();
      document.removeEventListener("pointerdown", down, true);
      document.removeEventListener("pointerup", up, true);
      document.removeEventListener("pointercancel", up, true);
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("resize", resized);
      document.removeEventListener("scroll", scrolled, true);
      if (vv) { vv.removeEventListener("resize", resized); vv.removeEventListener("scroll", resized); }
    };
  }, []);

  let at = -1;
  const listboxId = "sel-listbox-" + (anchor.id || Math.random().toString(36).slice(2, 7));
  return html`<div ref=${panel} class="select-list" data-slot="select-list" tabindex="-1">
    ${filtering && html`<div class="select-filter-wrap">
      <${Icon} name="search" />
      <input type="search" class="select-filter" data-slot="select-filter" placeholder="Поиск по списку…"
        role="combobox" aria-autocomplete="list" aria-expanded="true" aria-controls=${listboxId}
        aria-activedescendant=${active >= 0 ? listboxId + "-opt-" + active : undefined}
        aria-label="Найти в списке" value=${query} onInput=${(e) => setQuery(e.target.value)} />
    </div>`}
    <div ref=${list} id=${listboxId} class="select-options" role="listbox" aria-label=${anchor.getAttribute("aria-label") || undefined}>
      ${shown.map((r, i) => {
        if (r.group !== undefined) return html`<div key=${"g" + i} class="select-group" role="presentation">${r.group}</div>`;
        if (!r.disabled) at += 1;
        const mine = at;
        const chosen = r.value === value;
        return html`<div key=${"o" + i} id=${listboxId + "-opt-" + mine} role="option"
          class=${"select-option" + (chosen ? " selected" : "") + (!r.disabled && mine === active ? " active" : "")}
          data-slot="select-option" aria-selected=${chosen ? "true" : "false"} aria-disabled=${r.disabled ? "true" : undefined}
          onPointerMove=${(e) => { if (e.pointerType === "mouse" && !r.disabled && active !== mine) setActive(mine); }}
          onClick=${() => { if (!r.disabled) onPick(r.value); }}>
          <span class="select-check">${chosen && html`<${Icon} name="check" />`}</span>
          <span class="select-label">${r.label}</span></div>`;
      })}
      ${!enabled.length && html`<div class="select-empty muted" role="presentation">
        <${Icon} name="search-x" />
        <span>${q ? "Ничего не найдено" : "В списке пока пусто"}</span>
      </div>`}
    </div>
  </div>`;
}

// prefix: a quiet word before the chosen value on the button («Профиль: Работа»), not repeated in the list.
export function Select({ value, onChange, disabled, children, class: cls, className, prefix, ...rest }) {
  const button = useRef(null);
  const [open, setOpen] = useState(false);
  const current = value === undefined || value === null ? "" : String(value);
  const rows = collect(children, [], "");
  const options = rows.filter((r) => r.group === undefined);
  const chosen = options.find((o) => o.value === current);

  const close = useRef(() => setOpen(false)).current; // stable: the shared host compares it
  const pick = (v) => {
    setOpen(false);
    if (v !== current && onChange) onChange({ target: { value: v }, currentTarget: { value: v } });
  };

  // One list at a time, rendered into a shared host at the end of <body>; redone after every render so a
  // changed option list shows at once.
  useEffect(() => {
    if (!host) {
      host = document.createElement("div");
      host.id = "select-host";
      document.body.appendChild(host);
    }
    if (open && !disabled) {
      if (closeCurrent && closeCurrent !== close) closeCurrent();
      closeCurrent = close;
      render(html`<${Panel} anchor=${button.current} rows=${rows} value=${current} onPick=${pick} onClose=${close} />`, host);
    } else if (closeCurrent === close) {
      closeCurrent = null;
      render(null, host);
    }
  });
  useEffect(() => () => {
    if (closeCurrent === close) { closeCurrent = null; render(null, host); }
  }, []);
  return html`<button ref=${button} type="button" class=${["select-btn", cls, className].filter(Boolean).join(" ")} data-slot="select"
    aria-haspopup="listbox" aria-expanded=${open ? "true" : "false"} disabled=${disabled} ...${rest}
    onKeyDown=${(e) => { if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !open) { e.preventDefault(); setOpen(true); } }}
    onClick=${() => setOpen(!open)}>
    ${prefix && html`<span class="select-prefix" data-slot="select-prefix">${prefix}</span>`}
    <span class="select-value">${chosen ? chosen.label : (current ? current : (options.length ? "—" : ""))}</span>
    <${Icon} name="chevron-down" />
  </button>`;
}
