// Сакура effects: stars and falling petals behind the chat, and a mascot assembled from picture
// layers. All motion is CSS (theme.css); this script only builds the elements and switches
// classes on app events, so it costs nothing between events.

const WIDE = "(min-width: 1300px)"; // room for the mascot beside the chat column

function layer(className, children = []) {
  const div = document.createElement("div");
  div.className = className;
  div.append(...children);
  return div;
}

export default function setup(t) {
  const part = (name) => {
    const img = document.createElement("img");
    img.src = t.url(`assets/mascot/${name}.svg`);
    img.className = name;
    img.alt = "";
    img.decoding = "async";
    img.draggable = false;
    return img;
  };

  const stars = layer("sakura-stars");
  const petals = layer("sakura-petals");
  const count = t.reducedMotion ? 0 : t.phone ? 6 : 14;
  for (let i = 0; i < count; i++) {
    const petal = document.createElement("i");
    petal.style.setProperty("--x", `${Math.random() * 100}%`);
    petal.style.setProperty("--d", `${9 + Math.random() * 9}s`);
    petal.style.setProperty("--delay", `${-Math.random() * 18}s`);
    petal.style.setProperty("--s", String(0.6 + Math.random() * 0.8));
    petal.style.setProperty("--sway", `${Math.random() * 140 - 70}px`);
    petals.append(petal);
  }
  t.scene.append(stars, petals);

  // Back to front: hair behind the body, then the body, then the head with its face parts.
  const rig = layer("sakura-mascot", [
    layer("layer bob", [layer("layer tilt", [part("hair-back")])]),
    layer("layer body", [part("body")]),
    layer("layer bob", [layer("layer tilt", [
      part("head"),
      part("blush"),
      layer("layer look", [layer("layer eyes", [part("eyes")])]),
      part("eyes-happy"),
      part("mouth"),
      part("mouth-open"),
    ])]),
  ]);
  rig.setAttribute("role", "img");
  rig.setAttribute("aria-label", "Маскот темы");

  // Beside the chat on wide screens (and clickable); faint, behind the chat on narrow ones.
  const wide = matchMedia(WIDE);
  const place = () => {
    rig.classList.toggle("is-wide", wide.matches);
    (wide.matches ? t.overlay : t.scene).append(rig);
  };
  place();
  wide.addEventListener("change", place, { signal: t.signal });

  let timer = 0;
  const react = (kind, ms = 1200) => {
    rig.classList.remove("react-happy", "react-nod", "react-listen");
    void rig.offsetWidth; // restart the CSS animation if the same reaction repeats
    rig.classList.add(`react-${kind}`);
    clearTimeout(timer);
    timer = setTimeout(() => rig.classList.remove(`react-${kind}`), ms);
  };

  const burst = () => {
    if (t.reducedMotion) return;
    for (let i = 0; i < 6; i++) {
      const angle = (Math.PI * 2 * i) / 6 + Math.random() * 0.5;
      const piece = document.createElement("i");
      piece.className = "sakura-burst";
      piece.style.setProperty("--bx", `${Math.cos(angle) * 70}px`);
      piece.style.setProperty("--by", `${Math.sin(angle) * 70}px`);
      piece.addEventListener("animationend", () => piece.remove(), { once: true });
      rig.append(piece);
    }
  };
  rig.addEventListener("click", () => { react("happy"); burst(); }, { signal: t.signal });

  // Eyes follow the pointer; at most one style update per frame.
  let frame = 0;
  let pointer = null;
  const look = () => {
    frame = 0;
    if (!pointer || !rig.isConnected) return;
    const box = rig.getBoundingClientRect();
    const dx = (pointer.x - (box.left + box.width / 2)) / (window.innerWidth / 2);
    const dy = (pointer.y - (box.top + box.height * 0.45)) / (window.innerHeight / 2);
    rig.style.setProperty("--lx", Math.max(-1, Math.min(1, dx)).toFixed(2));
    rig.style.setProperty("--ly", Math.max(-1, Math.min(1, dy)).toFixed(2));
  };
  if (!t.reducedMotion) {
    window.addEventListener("pointermove", (e) => {
      pointer = { x: e.clientX, y: e.clientY };
      if (!frame) frame = requestAnimationFrame(look);
    }, { passive: true, signal: t.signal });
  }

  t.on("generation", ({ running }) => rig.classList.toggle("is-talking", running));
  t.on("send", () => react("listen", 1500));
  t.on("message", ({ role }) => { if (role !== "user") react("nod", 700); });

  return () => {
    clearTimeout(timer);
    cancelAnimationFrame(frame);
    stars.remove();
    petals.remove();
    rig.remove();
  };
}
