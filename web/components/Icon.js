// One set of line icons for the whole interface. <Icon name="x" /> takes the colour of its text;
// <IconButton icon="x" label="Закрыть" /> is a quiet square button whose label is its accessible name.
import { h, html } from "../vendor/preact-htm.mjs";
import { ICONS } from "../vendor/lucide-icons.mjs";

export function Icon({ name, size = 16 }) {
  const nodes = ICONS[name];
  if (!nodes) return null;
  return h("svg", {
    class: "icon", width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    "stroke-width": 1.5, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
  }, nodes.map(([tag, attrs], i) => h(tag, { ...attrs, key: i })));
}

// A button with an icon only. `label` is the name for screen readers and the tooltip with a mouse;
// anything else (onClick, disabled, data-slot, class) goes to the button.
export function IconButton({ icon, label, class: extra = "", title, children, ...rest }) {
  return html`<button type="button" class=${"icon-btn " + extra} aria-label=${label} title=${title || label} ...${rest}>
    <${Icon} name=${icon} />${children}</button>`;
}
