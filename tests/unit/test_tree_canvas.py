"""The canvas bubble map (web/components/TreeCanvas.js) run in Node against a recording fake canvas: hit tests, and
the speed rules that a browser on a fast PC cannot show (skipped where Node is missing, like Termux): no text
measuring or font switching per bubble per frame, no canvas resize while drawing, the space underneath painted once,
link arcs culled by their whole curve."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

HARNESS = r"""
const counts = {};
const bump = (k) => { counts[k] = (counts[k] || 0) + 1; };
class Path2D { constructor() { this.ops = 0; } roundRect() { this.ops++; } rect() { this.ops++; } arc() { this.ops++; }
  moveTo() { this.ops++; } bezierCurveTo() { this.ops++; } quadraticCurveTo() { this.ops++; } }
function makeCtx(name) {
  let font = "";
  const ctx = { name, globalAlpha: 1, fillStyle: "", strokeStyle: "", lineWidth: 1, lineJoin: "", lineCap: "",
    textBaseline: "", textAlign: "",
    get font() { return font; }, set font(v) { bump(name + ".font"); font = v; },
    measureText(t) { bump(name + ".measureText"); return { width: t.length * 8 }; } };
  for (const m of ["setTransform", "clearRect", "beginPath", "roundRect", "rect", "arc", "moveTo", "bezierCurveTo",
    "quadraticCurveTo", "fill", "stroke", "fillText", "fillRect", "strokeRect", "setLineDash", "save", "restore", "clip",
    "drawImage"]) ctx[m] = (...a) => { bump(name + "." + m); if (ctx.log) ctx.log.push(m); };
  return ctx;
}
let made = 0; // canvases the painter makes: the first measures text, the next holds the space underneath
function makeCanvas(name = made++ === 0 ? "measure" : "under") {
  let w = 0;
  const cv = { get width() { return w; }, set width(v) { bump(name + ".resize"); w = v; }, height: 0,
    getContext() { return cv.ctx || (cv.ctx = makeCtx(name)); } };
  return cv;
}
const rafQ = [];
globalThis.Path2D = Path2D;
globalThis.window = { devicePixelRatio: 3 };
globalThis.requestAnimationFrame = (f) => { rafQ.push(f); return rafQ.length; };
globalThis.cancelAnimationFrame = () => {};
globalThis.requestIdleCallback = (f) => { setTimeout(() => f({ timeRemaining: () => 50 }), 0); return 1; };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} };
globalThis.matchMedia = () => ({ addEventListener() {}, removeEventListener() {} });
globalThis.getComputedStyle = (el) => ({ color: "rgb(1, 2, 3)", fontFamily: "sans-serif", fontSize: "16px" });
globalThis.document = {
  createElement: (tag) => (tag === "canvas" ? makeCanvas() : { setAttribute() {}, style: {}, remove() {} }),
  documentElement: {}, head: {}, addEventListener() {}, removeEventListener() {},
};
const { buildModel, hitTest, createPainter, W, H } = await import("./components/TreeCanvas.js");

// A tree: the card, 40 branches of 20 leaves (801 bubbles), one nested group, a free branch, links.
const blocks = []; const pos = new Map([["root", { x: 0, y: 0 }]]); const kids = new Map(); const byId = new Map();
const add = (b, parent) => { blocks.push(b); byId.set(b.id, b); if (!kids.has(parent)) kids.set(parent, []); kids.get(parent).push(b); };
for (let i = 0; i < 40; i += 1) {
  const id = "b" + i;
  add({ id, title: "Ветка номер " + i + " с длинным названием", mode: i % 3 ? "search" : "always", chance: i % 4 ? 100 : 50,
    detached: i === 39, layout: i === 5 ? { display: "nested" } : {} }, null);
  pos.set(id, { x: 230, y: i * 400 });
  for (let j = 0; j < 20; j += 1) {
    const cid = id + "-" + j;
    add({ id: cid, title: "Лист " + j, mode: j % 5 ? "always" : "off", layout: {} }, id);
    pos.set(cid, { x: 460 + (j % 4) * 230, y: i * 400 + Math.floor(j / 4) * 66 });
  }
}
const descendants = (k, id) => (k.get(id) || []).flatMap((c) => [c.id, ...descendants(k, c.id)]);
const inactive = new Set(["b39", ...descendants(kids, "b39")]);
const orderOf = new Map(blocks.map((b, i) => [b.id, { n: (i % 20) + 1 }]));
const links = [["b0-0", "b0-19"], ["b2-0", "b3-19"]];
const d = { pos, kids, links, inactive, orderOf, vectorized: new Set(["b1"]), character: { name: "Кот", avatar: false },
  byId, descendants };
const model = buildModel(d, null);

const out = {};
const box = { width: 768, height: 1280 };
const host = { appendChild() {}, getBoundingClientRect: () => box };
const canvas = makeCanvas("c1");
const live = { model, cam: { x: 0, y: 0, s: 1 }, coarse: true };
const painter = createPainter(canvas, host, () => live);
const ctx = canvas.getContext("2d");
const frame = () => { const before = { ...counts }; rafQ.splice(0).forEach((f) => f()); const diff = {};
  for (const k of Object.keys(counts)) if (counts[k] !== (before[k] || 0)) diff[k] = counts[k] - (before[k] || 0); return diff; };
frame();
out.size = [canvas.width, canvas.height];
await new Promise((r) => setTimeout(r, 50)); // idle warm-up of the text layouts

const shot = (cam, extra = {}) => { Object.assign(live, { cam, drag: null, wire: null, selected: null, behind: null, behindCam: null }, extra);
  painter.requestDraw(); return frame(); };
for (const s of [0.1, 0.3, 0.4, 0.5, 0.7, 1, 1.6]) {
  const cam = { x: -100 * s, y: -200 * s, s };
  const f = shot(cam);
  out["zoom" + s] = { drawn: painter.stats.drawn, fillText: f["c1.fillText"] || 0, font: f["c1.font"] || 0,
    measure: f["measure.measureText"] || 0, resize: f["c1.resize"] || 0, clear: f["c1.clearRect"] || 0 };
}
// panning at a mid zoom, frame after frame
let measured = 0; let fonts = 0; let maxDrawn = 0;
for (let i = 0; i < 30; i += 1) {
  const f = shot({ x: -20, y: -i * 300, s: 0.45 });
  measured += f["measure.measureText"] || 0; fonts = Math.max(fonts, f["c1.font"] || 0);
  maxDrawn = Math.max(maxDrawn, painter.stats.drawn);
}
out.pan = { measured, fonts, maxDrawn };
// dragging a branch with its leaves onto another bubble
const ids = new Set(["b1", ...descendants(kids, "b1")]);
const f = shot({ x: 0, y: -1000, s: 0.8 }, { drag: { ids, dx: 40, dy: 300, target: "b2" }, selected: "b1" });
out.drag = { drawn: painter.stats.drawn, fill: f["c1.fill"] || 0 };
// the space underneath: painted into its own canvas once, then only copied
const inner = buildModel(d, "b0");
const first = shot({ x: 0, y: 0, s: 1 }, { model: inner, behind: model, behindCam: { x: 0, y: 0, s: 0.6 } });
const second = shot({ x: 50, y: 10, s: 1 }, { model: inner, behind: model, behindCam: { x: 0, y: 0, s: 0.6 } });
out.under = { first: first["under.clearRect"] || 0, second: second["under.clearRect"] || 0,
  copies: second["c1.drawImage"] || 0 };
live.model = model;
// hit tests
const p = pos.get("b3-4");
out.hitBody = hitTest(model, p.x + 50, p.y + 20, {}).bid;
out.hitPort = hitTest(model, p.x + W, p.y + H / 2, { ports: () => true }).port;
out.hitNone = hitTest(model, p.x + 50, p.y + 55, {}).bid || null;
out.hitExcluded = hitTest(model, p.x + 50, p.y + 20, { exclude: new Set(["b3-4"]) }).bid || null;
// a link arc whose two ends are both off screen but whose bow crosses the view is still drawn
const arcModel = { ...model, refs: [["b10-0", "b10-19"]], groups: [], edges: [] };
const q = pos.get("b10-0"); const r = pos.get("b10-19");
const midY = (q.y + r.y) / 2;
ctx.log = [];
shot({ x: -(q.x + W / 2 + 400) * 1 + 100, y: -(midY) + 300, s: 1 }, { model: arcModel });
out.arcDrawn = ctx.log.includes("quadraticCurveTo");
ctx.log = null;
painter.destroy();
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def run() -> dict:
    result = subprocess.run([NODE, "--input-type=module", "-e", HARNESS], capture_output=True, timeout=60, cwd=WEB)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def test_backing_store_is_at_most_twice_the_css_size(run: dict) -> None:
    assert run["size"] == [768 * 2, 1280 * 2]


def test_every_frame_clears_once_and_never_resizes(run: dict) -> None:
    for key, frame in run.items():
        if key.startswith("zoom"):
            assert frame["clear"] == 1 and frame["resize"] == 0, (key, frame)


def test_text_is_not_measured_or_refonted_per_bubble(run: dict) -> None:
    # after the idle warm-up no frame measures text, and fonts are set a few times per look, not per bubble
    for key, frame in run.items():
        if key.startswith("zoom"):
            assert frame["measure"] == 0, (key, frame)
            assert frame["font"] <= 40, (key, frame)
    assert run["pan"]["measured"] == 0 and run["pan"]["fonts"] <= 40 and run["pan"]["maxDrawn"] > 100


def test_small_text_is_skipped_and_marks_come_in_steps(run: dict) -> None:
    assert run["zoom0.1"]["fillText"] == 0 and run["zoom0.3"]["fillText"] == 0  # under 6 px on screen
    assert run["zoom0.4"]["fillText"] > 0  # titles only
    per = lambda z: run[z]["fillText"] / max(1, run[z]["drawn"])  # noqa: E731
    assert per("zoom0.4") <= 1.05 < per("zoom0.7")  # counts, numbers, chances join at their own sizes


def test_drag_draws_the_moved_branch(run: dict) -> None:
    assert run["drag"]["drawn"] > 20 and run["drag"]["fill"] > 0


def test_space_underneath_is_painted_once_and_copied(run: dict) -> None:
    assert run["under"]["first"] == 1 and run["under"]["second"] == 0 and run["under"]["copies"] == 1


def test_hit_tests(run: dict) -> None:
    assert run["hitBody"] == "b3-4"
    assert run["hitPort"] == "out"
    assert run["hitNone"] is None and run["hitExcluded"] is None


def test_link_arc_is_culled_by_its_whole_curve(run: dict) -> None:
    assert run["arcDrawn"] is True
