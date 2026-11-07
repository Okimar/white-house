// Colour helpers for the colour editor (Оформление → Цвета): any CSS colour to #rrggbb, contrast ratio.

let ctx = null;
// Any colour the browser understands (also color-mix and var results) to "#rrggbb"; null when it is not a colour.
export function toHex(value) {
  if (!value) return null;
  try {
    if (!ctx) ctx = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    ctx.clearRect(0, 0, 1, 1);
    ctx.fillStyle = "#000000";
    ctx.fillStyle = value;
    ctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
    if (a === 0) return null; // transparent: not a colour to edit
    return "#" + [r, g, b].map((n) => n.toString(16).padStart(2, "0")).join("");
  } catch (e) { return null; }
}

const channel = (v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
export function luminance(hex) {
  const n = parseInt(hex.slice(1), 16);
  return 0.2126 * channel(n >> 16) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}
// WCAG contrast ratio, 1 to 21.
export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export const isHex = (v) => /^#[0-9a-fA-F]{6}$/.test(v);
