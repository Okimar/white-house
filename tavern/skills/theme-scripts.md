---
name: theme-scripts
title: Скрипты тем (кнопки, маскот, частицы)
description: Writing the effects script of a theme - a button that folds the side menu, a mascot that reacts to messages, falling particles, anything that must move or react while the person chats. Teaches the script API, events, phone performance and safe writing, and how the person allows the script.
audience: assistant
---
A theme can have an effects script (script.js). CSS alone is enough for colours, shapes and plain animation; use a
script only when something must be created, or must react to the chat (a button, a mascot that nods at a reply,
particles counted for the device). If CSS can do it, do it in CSS.

THE RULES THAT NEVER BEND
- A script runs only after the PERSON allows it: Настройки → Темы → «Разрешить эффекты» on that theme. You cannot allow
  it, you must not try (the route is closed to you, also in auto mode), and every later edit of the script asks the
  person again. Until then the theme works without the effects. Say this in your report, with these exact steps.
- Safe mode (/?safe) turns every theme and script off. Tell the person it exists if anything looks wrong.
- You cannot run the script or see its effect. You can only check that it parses and follows the rules
  (check_theme_script). Say so; never write that it "works". The person looks and tells you what to fix.
- A script gets no network, no cookies, no stored data, no other files; no eval, no new Function, no imports.
- Touch screens first. Nothing may exist only on hover or mouse move (mouseenter, mouseover, mousemove, :hover):
  a phone has none of them. Use click / pointerdown for presses; pointermove only as a bonus for mice, never as the
  only way. Buttons are at least 40px high and wide (var(--tap)).

THE SHAPE OF A SCRIPT
```js
export default function setup(t) {
  // t.id            the theme id
  // t.scene         empty layer BEHIND the chat (ignores taps)
  // t.overlay       empty layer ABOVE the chat (ignores taps; give your own element pointer-events: auto)
  // t.url("assets/x.svg")   address of a file of the theme
  // t.reducedMotion true when the person turned animations off
  // t.phone         true on a narrow screen
  // t.signal        an AbortSignal: put every listener on it
  // t.on(type, fn)  app events: "generation" {running}, "message" {role, text}, "send" {text},
  //                 "chat" {chatId, characterId, characterName}
  const el = document.createElement("div");
  t.scene.append(el);
  t.on("generation", ({ running }) => el.classList.toggle("talking", running));
  window.addEventListener("resize", onResize, { signal: t.signal });
  return () => el.remove();   // undo EVERYTHING you added
}
```
Files of the theme: theme.json must name the script: "script": "script.js" (read theme.json with read_theme_file,
add the field, write the whole file back with write_theme_file; keep the "id" the same). The styles of everything the
script creates go in theme.css (class names that start with the theme id, such as "night-fold"), and motion is CSS,
not JavaScript.

PERFORMANCE (a cheap phone is the target)
- Animate only transform and opacity, in CSS. No always-running requestAnimationFrame loop, no setInterval; react
  to events instead. No backdrop-filter or big blur over the chat.
- Count of moving elements: at most about 12 on desktop, 6 on phones (t.phone), 0 with t.reducedMotion and inside
  @media (prefers-reduced-motion: reduce).
- One style write per frame at most; will-change: transform only on the few things that move all the time.

SAFE WRITING
- Add elements only to t.scene or t.overlay; one small rule on the page's root is allowed (an attribute like
  data-menu-fold on document.documentElement) if the cleanup removes it.
- Every listener on { signal: t.signal } (or removed in the returned function). Return a cleanup.
- Text you put on screen goes in with textContent, never innerHTML.
- Wrap nothing in try/catch to hide errors: if the script throws, the app switches it off and tells the person.

WORKED EXAMPLES (adapt, do not paste blindly; the class prefix "night-" is a placeholder for the theme's name)

1. A button that folds the side menu (desktop; on a phone the menu is already a sheet).
```js
export default function setup(t) {
  if (t.phone) return;
  const root = document.documentElement;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "night-fold";
  button.setAttribute("aria-label", "Свернуть меню");
  const set = (folded) => {
    if (folded) root.setAttribute("data-menu-fold", "true"); else root.removeAttribute("data-menu-fold");
    button.setAttribute("aria-pressed", String(folded));
    button.textContent = folded ? "›" : "‹";
  };
  set(false);
  button.addEventListener("click", () => set(root.getAttribute("data-menu-fold") !== "true"), { signal: t.signal });
  t.overlay.append(button);
  return () => { button.remove(); root.removeAttribute("data-menu-fold"); };
}
```
theme.css:
```css
.night-fold { position: fixed; left: calc(var(--sidebar-width) - 14px); top: 12px; width: var(--tap); height: var(--tap);
  pointer-events: auto; border: 1px solid var(--line); border-radius: 50%; background: var(--panel); color: var(--fg); }
:root[data-menu-fold="true"] [data-slot="sidebar"] { display: none; }
:root[data-menu-fold="true"] .night-fold { left: 8px; }
```
2. A mascot that reacts (one element; its look is a hand-drawn SVG file of the theme, assets/mascot.svg, written with
write_theme_file; you cannot make a picture, so say the SVG is simple shapes).
```js
export default function setup(t) {
  const mascot = document.createElement("img");
  mascot.className = "night-mascot";
  mascot.alt = "";
  mascot.src = t.url("assets/mascot.svg");
  (t.phone ? t.scene : t.overlay).append(mascot);
  const react = (name) => { mascot.classList.remove("nod", "listen"); void mascot.offsetWidth; mascot.classList.add(name); };
  mascot.addEventListener("click", () => react("nod"), { signal: t.signal });
  t.on("send", () => react("listen"));
  t.on("message", ({ role }) => { if (role !== "user") react("nod"); });
  t.on("generation", ({ running }) => mascot.classList.toggle("talking", running));
  return () => mascot.remove();
}
```
theme.css: `.night-mascot { position: fixed; right: 12px; bottom: 80px; width: 96px; pointer-events: auto; }` and keyframes on
transform only (a nod = translateY a few pixels), each wrapped in @media (prefers-reduced-motion: no-preference).
On a phone it sits faded behind the chat so it never covers text (opacity: .25 on .night-mascot inside the scene).

3. Falling particles, counted for the device, moved by CSS only.
```js
export default function setup(t) {
  const count = t.reducedMotion ? 0 : t.phone ? 6 : 12;
  const layer = document.createElement("div");
  layer.className = "night-particles";
  for (let i = 0; i < count; i++) {
    const p = document.createElement("i");
    p.style.setProperty("--x", `${Math.round(Math.random() * 100)}%`);
    p.style.setProperty("--d", `${8 + Math.round(Math.random() * 8)}s`);
    p.style.setProperty("--delay", `${-Math.round(Math.random() * 16)}s`);
    layer.append(p);
  }
  t.scene.append(layer);
  return () => layer.remove();
}
```
theme.css: `.night-particles i { position: absolute; top: -10px; left: var(--x); width: 6px; height: 6px; border-radius: 50%;
background: var(--accent); opacity: .5; animation: night-fall var(--d) linear var(--delay) infinite; }` with
`@keyframes night-fall { to { transform: translateY(105vh); } }`, all inside @media (prefers-reduced-motion:
no-preference). The only infinite animation allowed is this kind: transform on a few small elements.

## Plan
Ask only about taste and behaviour, with ready options: what should it do (a fold button / a mascot / particles /
something else), where it sits, how lively it is, what the mascot or particles look like (round, leaves, stars,
snow). Do not ask about code, touch or hover: touch screens are assumed. Decisions to write down: the theme the work
goes into (a new copy of the one in use unless the person named one), the class prefix, the events used, the motion
budget (phones 6, desktop 12), and "CANNOT:" lines for anything beyond your abilities (generating a picture or sound,
seeing the result, running the script).
Items: 1. make_theme (a copy, switched on); 2. read theme.json, then write it back with "script": "script.js";
3. write script.js; 4. write the styles in theme.css; 5. if a picture is needed, a hand-drawn SVG in assets/; 6. check_theme_script
and fix every error and warning; 7. the report.

## Write
Work in the person's own theme (never a built-in one: make_theme first). Order: theme.json (add "script"), script.js,
theme.css. After writing script.js, write_theme_file returns "script_check": fix every error, then call check_theme_script
and fix the warnings you can; repeat until errors are empty. If "syntax" is "not_checked", say in the report that the
syntax could not be checked on this device and was reviewed by reading only.
Never write code that waits for the mouse. Put the touch version first, and anything for mice second.
Final answer: what was added; that the script is NOT running yet; the exact steps to allow it (Настройки → Темы → find the
theme → «Разрешить эффекты»); that you could not see or run it and what the person should look at (tap the button
on a phone width; reply to a message); how to turn it all off (/?safe, or choosing another theme); and any part you could
not do.

## Check
- theme.json has "script": "script.js" and the same "id" as before; script.js exists in the theme.
- check_theme_script was run after the last change to script.js and returned no errors; its warnings were fixed or explained.
- The script exports default function setup(t) and returns a cleanup that removes everything it added.
- Every listener uses { signal: t.signal } or is removed in the cleanup; elements go only into t.scene or t.overlay.
- Nothing works only on hover or mouse move; touch targets are at least 40px (var(--tap)); mouse-only extras are optional.
- No network, cookies, stored data, eval or imports; text goes in with textContent.
- Motion is CSS on transform and opacity inside prefers-reduced-motion: no-preference; at most 6 moving elements on phones, none with t.reducedMotion.
- The report says the script does not run until the person allows it, gives the steps, and does not claim it was tested on screen.
- The assistant did not try to allow the script, change trusted_theme_scripts or touch /themes routes itself.
