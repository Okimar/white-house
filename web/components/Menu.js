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
    if (!el) return;
    const gap = 4;
    const margin = 8;
    const winH = window.innerHeight;
    const winW = window.innerWidth;
    const spaceBelow = winH - box.bottom - gap - margin;
    const spaceAbove = box.top - gap - margin;
    const fitsBelow = el.offsetHeight <= spaceBelow;
    const below = fitsBelow || spaceBelow >= spaceAbove;
    const maxH = Math.max(88, Math.floor(below ? spaceBelow : spaceAbove));
    el.style.maxHeight = `${maxH}px`;
    const h = Math.min(el.offsetHeight, maxH);
    const top = below ? box.bottom + gap : Math.max(margin, box.top - gap - h);
    const alignLeft = box.left + el.offsetWidth <= winW - margin && box.left < winW / 2;
    const idealLeft = alignLeft ? box.left : box.right - el.offsetWidth;
    const left = Math.min(Math.max(margin, idealLeft), Math.max(margin, winW - el.offsetWidth - margin));
    el.style.left = `${left}px`;
    el.style.top = `${Math.max(margin, top)}px`;
    el.dataset.side = below ? "down" : "up";
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
      } else if (e.key === "Tab") {
        onClose();
      } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const all = [...panel.current.querySelectorAll("button:not(:disabled), a")];
        if (!all.length) return;
        const at = all.indexOf(document.activeElement);
        const next = at < 0
          ? (e.key === "ArrowDown" ? all[0] : all[all.length - 1])
          : all[(at + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length];
        if (next) next.focus();
      } else if (e.key === "Home" || e.key === "PageUp") {
        e.preventDefault();
        const all = [...panel.current.querySelectorAll("button:not(:disabled), a")];
        if (all.length) all[0].focus();
      } else if (e.key === "End" || e.key === "PageDown") {
        e.preventDefault();
        const all = [...panel.current.querySelectorAll("button:not(:disabled), a")];
        if (all.length) all[all.length - 1].focus();
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
  return html`<div ref=${panel} class="menu" data-slot="menu" role="menu" aria-orientation="vertical"
    aria-label=${anchor.getAttribute("aria-label") || "Меню"} tabindex="-1">
    ${items.filter(Boolean).map((item, i) => (item.href
      ? html`<a key=${i} role="menuitem" class=${item.danger ? "danger-text" : ""} href=${item.href}
          download=${item.download} title=${item.title || item.label} onClick=${onClose}>${body(item)}</a>`
      : html`<button key=${i} type="button" role="menuitem" disabled=${item.disabled}
          aria-disabled=${item.disabled ? "true" : undefined}
          class=${item.danger ? "danger-text" : ""} title=${item.title || item.label}
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
