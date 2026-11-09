// Screenshots for the assistant (ADR 0020). The server sends the notice "assistant_capture" {id, what, long_side,
// max_bytes}; the page draws itself with the vendored modern-screenshot, downsizes the picture and POSTs it to
// /api/assistant/screenshots/{id}. "screen" is the page as it is; "phone" and "tablet" load this same page in a hidden
// same-origin frame of that size (/?capture=1, the one address the server lets be framed) and draw the frame.
// Password fields are left out of every picture. The person sees a note each time.
import { api } from "./api.js";
import { notify } from "./notify.js";

const SIZES = { phone: [375, 667], tablet: [768, 1280] };
const FRAME_LOAD_MS = 12000;
const SETTLE_MS = 1500; // after the frame's app drew its first screen: fonts, theme, effects

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Never draw what the person typed into a password field (API keys, app passwords).
const keepNode = (node) => !(node && node.tagName === "INPUT" && String(node.type).toLowerCase() === "password");

async function openFrame(width, height) {
  const frame = document.createElement("iframe");
  frame.src = "/?capture=1";
  frame.title = "";
  frame.tabIndex = -1;
  frame.setAttribute("aria-hidden", "true");
  Object.assign(frame.style, {
    position: "fixed", left: "-10000px", top: "0", width: width + "px", height: height + "px",
    border: "0", opacity: "0", pointerEvents: "none",
  });
  document.body.append(frame);
  await new Promise((resolve, reject) => {
    frame.onload = resolve;
    setTimeout(() => reject(new Error("страница в рамке не загрузилась")), FRAME_LOAD_MS);
  });
  const until = Date.now() + FRAME_LOAD_MS;
  while (Date.now() < until) { // the app draws into #root after its modules loaded
    const root = frame.contentDocument && frame.contentDocument.getElementById("root");
    if (root && root.children.length) break;
    await wait(100);
  }
  await wait(SETTLE_MS);
  return frame;
}

function shrink(canvas, longSide) {
  const scale = Math.min(1, longSide / Math.max(canvas.width, canvas.height));
  if (scale === 1) return canvas;
  const out = document.createElement("canvas");
  out.width = Math.max(1, Math.round(canvas.width * scale));
  out.height = Math.max(1, Math.round(canvas.height * scale));
  out.getContext("2d").drawImage(canvas, 0, 0, out.width, out.height);
  return out;
}

const toBlob = (canvas, type, quality) => new Promise((resolve) => canvas.toBlob(resolve, type, quality));

// PNG when it fits, otherwise JPEG with less and less quality.
async function encode(canvas, maxBytes) {
  let blob = await toBlob(canvas, "image/png");
  for (const quality of [0.85, 0.7, 0.5]) {
    if (blob && blob.size <= maxBytes) return blob;
    blob = await toBlob(canvas, "image/jpeg", quality);
  }
  if (!blob || blob.size > maxBytes) throw new Error("снимок получился слишком большим");
  return blob;
}

async function draw(what, longSide) {
  const lib = await import("../vendor/modern-screenshot.mjs");
  let frame = null;
  let doc = document;
  let width = window.innerWidth;
  let height = window.innerHeight;
  try {
    if (SIZES[what]) {
      [width, height] = SIZES[what];
      frame = await openFrame(width, height);
      doc = frame.contentDocument;
    }
    const root = doc.documentElement;
    const background = doc.defaultView.getComputedStyle(doc.body).backgroundColor || "#000";
    return await lib.domToCanvas(root, { width, height, backgroundColor: background, filter: keepNode, scale: 1 });
  } finally {
    if (frame) frame.remove();
  }
}

export async function handleCapture(event) {
  if (window.top !== window) return; // the hidden frame is not a page to ask
  const { id, what = "screen", long_side: longSide = 1280, max_bytes: maxBytes = 2 * 1024 * 1024 } = event;
  try {
    const canvas = shrink(await draw(what, longSide), longSide);
    const blob = await encode(canvas, maxBytes);
    await api.postBinary(`/assistant/screenshots/${encodeURIComponent(id)}`, blob, blob.type);
    notify("🪄 Помощник сделал снимок экрана", "info");
  } catch (e) {
    const why = (e && e.message) || "неизвестная ошибка";
    try { await api.postBinary(`/assistant/screenshots/${encodeURIComponent(id)}?failed=${encodeURIComponent(why)}`, "", "text/plain"); } catch (e2) { /* the tool times out by itself */ }
  }
}
