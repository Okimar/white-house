// Plugin places that need a real window and a status chip (t.window, t.status in core/plugins.js).
// A window is a sheet like the app's own: ✕ always visible at the top right (sticky header), Esc and Back close it,
// full height and width under 720px. The plugin draws the inside with the page's parts (`win.ui`).
import { html } from "../vendor/preact-htm.mjs";
import { setWindow } from "../core/plugins.js";
import { IconButton } from "./Icon.js";
import { backdrop, useEscape } from "../core/keys.js";

function Body({ win, close }) {
  return win.render({ ...win.ui, close });
}

function PluginWindow({ win }) {
  const close = () => setWindow(win.key, false);
  useEscape(close);
  return html`<div class="overlay plugin-overlay" ...${backdrop(close)}>
    <div class="dialog plugin-window" data-slot="plugin-window" data-plugin=${win.owner} role="dialog" aria-modal="true"
      aria-label=${win.title}>
      <div class="side-head"><strong>${win.icon} ${win.title}</strong><span class="spacer"></span>
        <${IconButton} icon="x" label="Закрыть" onClick=${close} /></div>
      <${Body} win=${win} close=${close} />
    </div>
  </div>`;
}

export function PluginWindows({ windows }) {
  return windows.filter((w) => w.shown).map((w) => html`<${PluginWindow} key=${w.key} win=${w} />`);
}

// Top-bar buttons of windows that want one.
export function WindowButtons({ windows }) {
  return windows.filter((w) => w.button).map((w) => html`<button key=${w.key} type="button" data-slot="plugin-window-button"
    data-plugin=${w.owner} aria-pressed=${w.shown} title=${w.title} aria-label=${w.title}
    onClick=${() => setWindow(w.key, !w.shown)}>${w.icon}</button>`);
}

// Status chips in the top bar (progress of a plugin's work).
export function StatusChips({ chips }) {
  return chips.map((c, i) => html`<button key=${c.owner + i} type="button" class=${"plugin-chip " + (c.state || "")}
    data-slot="plugin-status" data-plugin=${c.owner} data-state=${c.state || ""} title=${c.title || c.text}
    onClick=${c.onClick || null}>${c.text}</button>`);
}
