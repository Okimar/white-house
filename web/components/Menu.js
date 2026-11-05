// A drop-down list of actions. It lives at the end of <body> so that a scrolling list or a dialog cannot
// cut it off, and it opens toward the free side of the screen.
//   openMenu(event.currentTarget, [{ icon, label, onClick, disabled, danger, href, download }, ...])
//   <${MenuButton} label="Действия" items=${() => [...]} />
import { html, render, useEffect, useLayoutEffect, useRef } from "../vendor/preact-htm.mjs";
import { Icon, IconButton } from "./Icon.js";

let host = null;
let closeCurrent = null;

export function closeMenu() {
  if (closeCurrent) closeCurrent();
}

function Panel({ anchor, items, onClose }) {
  const panel = useRef(null);

  useLayoutEffect(() => {
    const box = anchor.getBoundingClientRect();
    const el = panel.current;
    const gap = 4;
    const margin = 8;
    const left = Math.min(box.right - el.offsetWidth, window.innerWidth - el.offsetWidth - margin);
    const below = box.bottom + gap + el.offsetHeight <= window.innerHeight - margin;
    const top = below ? box.bottom + gap : Math.max(margin, box.top - gap - el.offsetHeight);
    el.style.left = `${Math.max(margin, left)}px`;
    el.style.top = `${top}px`;
    el.focus({ preventScroll: true }); // arrows then walk the items; no ring on an item after a tap
  }, []);

  useEffect(() => {
    const outside = (e) => { if (!panel.current.contains(e.target) && !anchor.contains(e.target)) onClose(); };
    const key = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose();
        anchor.focus();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const all = [...panel.current.querySelectorAll("button:not(:disabled), a")];
        const at = all.indexOf(document.activeElement);
        const next = all[(at + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length];
        if (next) next.focus();
      }
    };
    const leave = (e) => { if (!panel.current.contains(e.target)) onClose(); };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("resize", onClose);
    document.addEventListener("scroll", leave, true);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("resize", onClose);
      document.removeEventListener("scroll", leave, true);
    };
  }, []);

  const body = (item) => html`
    ${item.icon && (item.emoji ? html`<span class="menu-emoji">${item.icon}</span>` : html`<${Icon} name=${item.icon} />`)}
    <span class="menu-label">${item.label}</span>`;
  return html`<div ref=${panel} class="menu" data-slot="menu" role="menu" tabindex="-1">
    ${items.filter(Boolean).map((item, i) => (item.href
      ? html`<a key=${i} role="menuitem" class=${item.danger ? "danger-text" : ""} href=${item.href}
          download=${item.download} onClick=${onClose}>${body(item)}</a>`
      : html`<button key=${i} type="button" role="menuitem" disabled=${item.disabled}
          class=${item.danger ? "danger-text" : ""}
          onClick=${() => { onClose(); item.onClick(); }}>${body(item)}</button>`))}
  </div>`;
}

export function openMenu(anchor, items) {
  closeMenu();
  if (!host) {
    host = document.createElement("div");
    host.id = "menu-host";
    document.body.appendChild(host);
  }
  const close = () => {
    if (closeCurrent !== close) return;
    closeCurrent = null;
    anchor.removeAttribute("aria-expanded");
    render(null, host);
  };
  closeCurrent = close;
  anchor.setAttribute("aria-expanded", "true");
  render(html`<${Panel} anchor=${anchor} items=${items} onClose=${close} />`, host);
}

// The usual "…" button. A second press closes the list.
export function MenuButton({ label = "Действия", icon = "ellipsis", items, ...rest }) {
  return html`<${IconButton} icon=${icon} label=${label} aria-haspopup="menu" ...${rest}
    onClick=${(e) => {
      const button = e.currentTarget;
      if (button.getAttribute("aria-expanded") === "true") closeMenu();
      else openMenu(button, typeof items === "function" ? items() : items);
    }} />`;
}
