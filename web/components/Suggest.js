// A text field with our own list of suggestions, in place of <input list> + <datalist> (the native popup is
// plain, unthemed and sticks to a corner of the screen on Android). Free text stays allowed.
//   <${Suggest} value=${x} options=${["a", { id: "b", name: "Bee" }]} onInput=${…} onChange=${…} aria-label="…" />
// onInput / onChange get { target: { value } } as with a native input; picking a row calls both (the value is set,
// then the same save as after leaving the field). The list lives at the end of <body> like Select's, opens under
// the field or above it, fits the visible height (keyboard open), and closes on a tap outside, on scrolling the
// page, on Esc / Tab. The field keeps focus: rows swallow pointerdown so blur does not eat the tap.
import { html, render, useEffect, useLayoutEffect, useRef, useState } from "../vendor/preact-htm.mjs";

const MAX_ROWS = 200;
let host = null;
let closeCurrent = null;
let seq = 0;

function Panel({ anchor, rows, active, listId, onActive, onPick, onClose }) {
  const panel = useRef(null);
  const state = useRef({});
  state.current = { onClose };
  const born = useRef(Date.now());
  const moved = useRef(Date.now());

  const place = () => {
    const el = panel.current;
    if (!el || !anchor.isConnected) return;
    const box = anchor.getBoundingClientRect();
    const gap = 4;
    const margin = 8;
    const vv = window.visualViewport;
    const vh = vv ? vv.height : window.innerHeight;
    el.style.maxHeight = "none";
    el.style.width = `${Math.min(Math.max(box.width, 260), window.innerWidth - 2 * margin)}px`;
    const natural = el.offsetHeight;
    const below = vh - box.bottom - gap - margin;
    const above = box.top - gap - margin;
    const down = natural <= below || below >= above;
    const room = Math.max(88, down ? below : above);
    const height = Math.min(natural, room);
    el.style.maxHeight = `${room}px`;
    const left = Math.min(box.left, window.innerWidth - el.offsetWidth - margin);
    el.style.left = `${Math.max(margin, left)}px`;
    el.style.top = `${down ? box.bottom + gap : Math.max(margin, box.top - gap - height)}px`;
  };
  useLayoutEffect(place);

  useEffect(() => {
    const row = panel.current && panel.current.querySelector(".active");
    if (row) row.scrollIntoView({ block: "nearest" });
  }, [active]);

  useLayoutEffect(() => {
    const outside = (e) => { if (!panel.current.contains(e.target) && !anchor.contains(e.target)) state.current.onClose(); };
    // The browser scrolls a focused field into view while the keyboard opens: that is not the user's scroll.
    const scrolled = (e) => {
      if (panel.current.contains(e.target)) return;
      if (Date.now() - Math.max(born.current, moved.current) < 500) place();
      else state.current.onClose();
    };
    const resized = () => { moved.current = Date.now(); place(); };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("scroll", scrolled, true);
    window.addEventListener("resize", resized);
    if (window.visualViewport) window.visualViewport.addEventListener("resize", resized);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("scroll", scrolled, true);
      window.removeEventListener("resize", resized);
      if (window.visualViewport) window.visualViewport.removeEventListener("resize", resized);
    };
  }, []);

  return html`<div ref=${panel} class="select-list suggest-list" data-slot="suggest-list"
    onPointerDown=${(e) => e.preventDefault()}>
    <div class="select-options" role="listbox" id=${listId} aria-label=${anchor.getAttribute("aria-label") || undefined}>
      ${rows.map((r, i) => html`<div key=${r.id} id=${listId + "-" + i} role="option" aria-selected=${i === active ? "true" : "false"}
        class=${"select-option suggest-option" + (i === active ? " active" : "")} data-slot="select-option"
        onPointerMove=${() => { if (active !== i) onActive(i); }}
        onClick=${() => onPick(r.id)}>
        <span class="suggest-id">${r.id}</span>
        ${r.name && html`<span class="suggest-name muted">${r.name}</span>`}</div>`)}
    </div>
  </div>`;
}

export function Suggest({ value, options, onInput, onChange, disabled, ...rest }) {
  const input = useRef(null);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(value === undefined || value === null ? "" : String(value));
  const [dirty, setDirty] = useState(false); // filter only after typing: on focus show everything
  const [active, setActive] = useState(-1);
  const listId = useRef("suggest-" + (seq += 1)).current;

  // The field is not controlled: a controlled value from a lagging render ate fast keystrokes. Outside changes
  // of `value` (another connection opened, a reset) are copied in here instead.
  useLayoutEffect(() => {
    const v = value === undefined || value === null ? "" : String(value);
    if (input.current && input.current.value !== v) input.current.value = v;
    setText(v);
  }, [value]);

  const all = (options || []).map((o) => (typeof o === "string" ? { id: o, name: "" } : { id: String(o.id), name: o.name && o.name !== o.id ? String(o.name) : "" }));
  const q = dirty ? text.trim().toLowerCase() : "";
  const rows = all.filter((r) => !q || r.id.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)).slice(0, MAX_ROWS);
  const visible = open && !disabled && rows.length > 0 && !(rows.length === 1 && rows[0].id === text);

  const close = useRef(() => setOpen(false)).current; // stable: the shared host compares it
  const pick = (v) => {
    setOpen(false);
    setDirty(false);
    setText(v);
    if (input.current) input.current.value = v;
    if (onInput) onInput({ target: { value: v }, currentTarget: { value: v } });
    if (onChange) onChange({ target: { value: v }, currentTarget: { value: v } });
  };

  // Layout effects, not plain ones: Preact runs a component's pending plain effects at the start of its next
  // render; a nested render() from there leaves the hooks' current component pointing at the list and a fast
  // second keystroke broke useState.
  useLayoutEffect(() => {
    if (!host) {
      host = document.createElement("div");
      host.id = "suggest-host";
      document.body.appendChild(host);
    }
    if (visible) {
      if (closeCurrent && closeCurrent !== close) closeCurrent();
      closeCurrent = close;
      render(html`<${Panel} anchor=${input.current} rows=${rows} active=${active} listId=${listId}
        onActive=${setActive} onPick=${pick} onClose=${close} />`, host);
    } else if (closeCurrent === close) {
      closeCurrent = null;
      render(null, host);
    }
  });
  useLayoutEffect(() => () => {
    if (closeCurrent === close) { closeCurrent = null; render(null, host); }
  }, []);

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!rows.length) return;
      e.preventDefault();
      if (!visible) { setOpen(true); return; }
      setActive((a) => (a < 0 ? (e.key === "ArrowDown" ? 0 : rows.length - 1) : (a + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length));
    } else if (e.key === "Escape") {
      if (visible) { e.preventDefault(); e.stopPropagation(); setOpen(false); }
    } else if (e.key === "Enter") {
      if (visible && active >= 0 && rows[active]) { e.preventDefault(); pick(rows[active].id); }
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return html`<input ref=${input} type="text" autocomplete="off" autocapitalize="off" spellcheck="false" ...${rest}
    disabled=${disabled} role="combobox" aria-autocomplete="list" aria-expanded=${visible ? "true" : "false"}
    aria-controls=${visible ? listId : undefined} aria-activedescendant=${visible && active >= 0 ? listId + "-" + active : undefined}
    onFocus=${() => { setActive(-1); setOpen(true); }}
    onInput=${(e) => { setText(e.target.value); setDirty(true); setActive(-1); setOpen(true); if (onInput) onInput(e); }}
    onChange=${onChange}
    onBlur=${() => setOpen(false)}
    onKeyDown=${onKeyDown} />`;
}
