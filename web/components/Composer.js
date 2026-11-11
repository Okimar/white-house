// Message input. Enter sends, Shift+Enter inserts a newline. While a reply is being written,
// the send button turns into Stop. Your text is always a user message; the role menu decides who
// the model's next messages arrive from (assistant, system, or user for writing in your place).
// 📎 (or pasting a picture) adds pictures to the message: they go to the picture library and the
// model sees them with the message. Plugins add buttons here (core/plugins.js); "/command" text
// is offered to plugins first by the app shell (main.js).
// Plugin helpers may mark ranges of the typed text (spelling): a layer over the textarea draws wavy underlines
// (a textarea cannot style its own text), and a tap that puts the caret in a marked range opens a small menu of
// that mark's options (MarkMenu) above the word, inside the part of the page the keyboard leaves visible.
import { Select } from "./Select.js";
import { html, useState, useRef, useEffect, useLayoutEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { ROLES } from "./roles.js";
import { usePluginUi, setComposerBridge, resolve, pluginSummary } from "../core/plugins.js";
import { applyEdits, markAt, segments, spellingHint } from "../core/textMarks.js";
import { holdTouch, placeList, visibleArea } from "../core/popup.js";
import { Icon, IconButton } from "./Icon.js";

const MAX_IMAGES = 8;
const HINT_KEY = "tavern.spellingHintSeen";
// What the layer copies from the textarea so its lines wrap exactly as the textarea's do.
const MIRRORED = ["fontFamily", "fontSize", "fontWeight", "fontStyle", "fontVariant", "fontStretch", "lineHeight",
  "letterSpacing", "wordSpacing", "textTransform", "textIndent", "tabSize", "textAlign", "direction", "whiteSpace",
  "overflowWrap", "wordBreak", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft"];

const hintSeen = () => { try { return localStorage.getItem(HINT_KEY) === "1"; } catch { return false; } };

// The small menu of one mark: its options (corrections, «Пропустить», «В словарь») as 44px rows. Focus stays in
// the textarea (mousedown is held), so the keyboard stays open; the menu sits above the word when there is room.
function MarkMenu({ mark, layer, onClose }) {
  const panel = useRef(null);
  const side = useRef("up");
  const close = useRef(onClose);
  close.current = onClose;
  const place = () => {
    const el = panel.current;
    const word = layer && layer.querySelector(`[data-mark="${mark.index}"]`);
    if (!el || !word) return;
    const rects = word.getClientRects();
    const r = rects.length ? rects[0] : word.getBoundingClientRect();
    el.style.maxHeight = "none";
    const at = placeList({ box: { top: r.top, bottom: r.bottom, left: r.left }, natural: el.offsetHeight,
      width: el.offsetWidth, view: visibleArea(), pageWidth: window.innerWidth, side: side.current, least: 96 });
    side.current = at.side;
    el.style.maxHeight = `${at.maxHeight}px`;
    el.style.left = `${at.left}px`;
    el.style.top = `${at.top}px`;
  };
  const placeRef = useRef(place);
  placeRef.current = place;
  useLayoutEffect(place);
  useLayoutEffect(() => {
    const again = () => placeRef.current();
    const outside = (e) => { if (panel.current && !panel.current.contains(e.target)) close.current(); };
    const key = (e) => { if (e.key === "Escape") { e.preventDefault(); close.current(); } };
    const unhold = holdTouch(panel.current, panel.current);
    const vv = window.visualViewport;
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", key, true);
    window.addEventListener("resize", again);
    if (vv) { vv.addEventListener("resize", again); vv.addEventListener("scroll", again); }
    return () => {
      unhold();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", key, true);
      window.removeEventListener("resize", again);
      if (vv) { vv.removeEventListener("resize", again); vv.removeEventListener("scroll", again); }
    };
  }, []);
  const label = mark.title || "Исправление";
  return html`<div ref=${panel} class="mark-menu" data-slot="mark-menu" data-plugin=${mark.owner} role="group"
    aria-label=${`${label}: ${mark.word || ""}`} onMouseDown=${(e) => e.preventDefault()}>
    <div class="mark-menu-title muted">${label}</div>
    ${(mark.options || []).map((o, j) => html`<button key=${j} type="button"
      class=${"mark-menu-option" + (o.quiet ? " quiet" : "")}
      onClick=${() => { onClose(); o.onClick(); }}>${o.label}</button>`)}
  </div>`;
}

export function Composer({ running, disabled, role, onRole, onSend, onStop, onError }) {
  const plugins = usePluginUi(); // buttons plugins put beside the message box
  const [text, setText] = useState("");
  const [images, setImages] = useState([]); // picture names in the library
  const [uploading, setUploading] = useState(false);
  const box = useRef(null);
  const picker = useRef(null);
  const textRef = useRef("");
  const wantCaret = useRef(null); // where the caret goes after a plugin edited the text
  const [openChip, setOpenChip] = useState(null);
  const layer = useRef(null); // the marks layer over the textarea
  const layerText = useRef(null);
  const [openMark, setOpenMark] = useState(null); // {owner, start, end} of the mark whose menu is open
  const [hintOff, setHintOff] = useState(hintSeen);
  textRef.current = text;

  // What plugin helpers show: computed once per render (view() is plain and cheap). Marks get their owner and index.
  const views = plugins.composerHelpers.map((h) => ({ owner: h.owner, view: h.view() }));
  const marks = [];
  for (const x of views) {
    for (const m of (x.view && x.view.marks) || []) marks.push({ ...m, owner: x.owner, index: marks.length });
  }
  const shownMark = openMark && marks.find((m) => m.owner === openMark.owner && m.start === openMark.start
    && m.end === openMark.end);
  const spelling = pluginSummary("spelling");
  const hint = spellingHint(spelling, hintOff);
  useEffect(() => { // shown in this visit only: the next page load does not show it again
    if (hint) { try { localStorage.setItem(HINT_KEY, "1"); } catch { /* shown again next time */ } }
  }, [hint]);

  // The layer takes the textarea's place, box and font, so its text wraps line for line; its text is clear and only
  // the underlines show. Runs after every render (the strip above may have moved the textarea) and on resize.
  const syncLayer = () => {
    const el = layer.current;
    const inner = layerText.current;
    const area = box.current;
    if (!el || !inner || !area || !marks.length) return;
    const css = getComputedStyle(area);
    el.style.left = `${area.offsetLeft + area.clientLeft}px`;
    el.style.top = `${area.offsetTop + area.clientTop}px`;
    el.style.width = `${area.clientWidth}px`;
    el.style.height = `${area.clientHeight}px`;
    for (const k of MIRRORED) inner.style[k] = css[k];
    inner.style.width = `${area.clientWidth}px`;
    inner.style.transform = `translate(${-area.scrollLeft}px, ${-area.scrollTop}px)`;
  };
  const sync = useRef(syncLayer);
  sync.current = syncLayer;
  useLayoutEffect(() => { sync.current(); });
  useEffect(() => {
    if (!box.current || typeof ResizeObserver === "undefined") return undefined;
    const watch = new ResizeObserver(() => sync.current());
    watch.observe(box.current);
    return () => watch.disconnect();
  }, []);

  // A tap (or click) that leaves the caret in a mark opens its menu; anywhere else closes it.
  const onTap = () => {
    const area = box.current;
    if (!area || area.selectionStart !== area.selectionEnd) { setOpenMark(null); return; }
    const i = markAt(marks, area.selectionStart);
    setOpenMark(i >= 0 ? { owner: marks[i].owner, start: marks[i].start, end: marks[i].end } : null);
  };

  // Plugin helpers (spelling and the like) read and edit the typed text through this bridge.
  // Telling them about a change is fire-and-forget: sending never waits for them.
  useEffect(() => {
    setComposerBridge({
      text: () => textRef.current,
      caret: () => (box.current ? box.current.selectionStart : textRef.current.length),
      replace: (edits, caret) => {
        const old = textRef.current;
        const done = applyEdits(old, edits, box.current ? box.current.selectionStart : old.length);
        if (!done) return false;
        wantCaret.current = typeof caret === "number" ? caret : done.caret;
        setText(done.text);
        return true;
      },
    });
    return () => setComposerBridge(null);
  }, []);
  useEffect(() => {
    if (wantCaret.current !== null && box.current) {
      const at = Math.min(wantCaret.current, text.length);
      if (document.activeElement !== box.current) box.current.focus({ preventScroll: true });
      box.current.setSelectionRange(at, at);
    }
    wantCaret.current = null;
    setOpenMark(null); // the text moved under the menu
    const caret = box.current ? box.current.selectionStart : text.length;
    for (const h of plugins.composerHelpers) h.onText(text, caret);
  }, [text]);

  const submit = async () => {
    const value = text.trim();
    if ((!value && !images.length) || running || disabled || uploading) return;
    const pictures = images;
    setHintOff(true);
    setText("");
    setImages([]);
    const sent = await onSend(value, pictures);
    // Not sent (network, busy, spending limit, a failed command): nothing typed may be lost.
    if (sent === false) {
      setText((current) => current || value);
      setImages((current) => (current.length ? current : pictures));
    }
    if (box.current) box.current.focus();
  };

  const attach = async (files) => {
    const list = [...files].filter((f) => f.type.startsWith("image/")).slice(0, MAX_IMAGES - images.length);
    if (!list.length) return;
    setUploading(true);
    try {
      for (const file of list) {
        const saved = await api.postBinary(`/assets?name=${encodeURIComponent(file.name || "picture")}`, file,
          file.type || "application/octet-stream");
        setImages((current) => [...current, saved.name]);
      }
    } catch (err) {
      if (onError) onError(err.message);
    } finally {
      setUploading(false);
    }
  };

  const onPaste = (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.some((f) => f.type.startsWith("image/"))) {
      e.preventDefault();
      attach(files);
    }
  };

  const onKeyDown = (e) => {
    // isComposing: do not send while an IME (phone keyboards, CJK) is still confirming a word
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return html`
    <form class="composer" data-slot="composer" onSubmit=${(e) => { e.preventDefault(); submit(); }}>
      ${images.length > 0 && html`<div class="composer-images" data-slot="composer-images">
        ${images.map((name) => html`<span key=${name} class="composer-image">
          <img src=${"/assets/" + encodeURIComponent(name)} alt="прикреплённая картинка" />
          <${IconButton} icon="x" label="Убрать картинку" onClick=${() => setImages(images.filter((n) => n !== name))} />
        </span>`)}
      </div>`}
      ${hint && html`<div class="composer-hint muted" data-slot="composer-hint" role="note">
        <span>Проверка орфографии выключена. Включается в Настройках → «Дополнения» → «${spelling.name}»: ошибки
          подчёркиваются прямо здесь.</span>
        <button type="button" onClick=${() => setHintOff(true)}>Понятно</button>
      </div>`}
      ${views.filter((x) => x.view && (x.view.note || (x.view.items || []).length || x.view.undo)).map((x) => html`
        <div key=${x.owner} class="composer-helper" data-slot="composer-helper" data-plugin=${x.owner}>
          ${x.view.note && html`<span class="muted composer-helper-note">${x.view.note}</span>`}
          ${(x.view.items || []).map((item, i) => html`<span key=${i} class="composer-helper-item">
            <button type="button" class="composer-helper-chip" title=${item.title || ""}
              onClick=${() => setOpenChip(openChip === x.owner + i ? null : x.owner + i)}>${item.label}</button>
            ${openChip === x.owner + i && (item.options || []).map((o, j) => html`<button key=${j} type="button"
              class="composer-helper-option" onClick=${() => { setOpenChip(null); o.onClick(); }}>${o.label}</button>`)}
          </span>`)}
          ${x.view.undo && html`<button type="button" class="composer-helper-undo"
            onClick=${() => x.view.undo.onClick()}>${x.view.undo.label}</button>`}
        </div>`)}
      <textarea ref=${box} rows="2" value=${text} placeholder="Напишите сообщение" data-slot="composer-input"
        aria-label="Сообщение" disabled=${disabled} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${onKeyDown} onPaste=${onPaste} onClick=${onTap} onScroll=${() => sync.current()}></textarea>
      <div ref=${layer} class="composer-marks" data-slot="composer-marks" aria-hidden="true"
        hidden=${!marks.length}><div ref=${layerText} class="composer-marks-text">${marks.length > 0
          && segments(text, marks).map((s, i) => (s.mark < 0 ? s.text : html`<span key=${i} class="composer-mark"
            data-mark=${s.mark} data-kind=${marks[s.mark].kind || "spelling"}>${s.text}</span>`))}</div></div>
      ${shownMark && html`<${MarkMenu} key=${shownMark.owner + ":" + shownMark.start} mark=${shownMark}
        layer=${layer.current} onClose=${() => setOpenMark(null)} />`}
      <div class="composer-tools">
        <${IconButton} icon=${uploading ? "loader-circle" : "paperclip"} class="attach" data-slot="attach-button"
          label="Прикрепить картинку" disabled=${disabled || uploading || images.length >= MAX_IMAGES}
          onClick=${() => picker.current && picker.current.click()} />
        ${plugins.composerButtons.filter((b) => resolve(b.show) !== false).map((b, i) => html`<button key=${b.owner + i}
          type="button" class="attach icon-btn" data-slot="composer-plugin-button" data-plugin=${b.owner}
          title=${resolve(b.title)} aria-label=${resolve(b.title)}
          disabled=${disabled || running} onClick=${() => b.onClick()}>${resolve(b.icon)}</button>`)}
        <input ref=${picker} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
          onChange=${(e) => { attach(e.target.files); e.target.value = ""; }} />
        <label class="role-pick" title="От чьего имени модель пишет следующее сообщение">
          <span class="muted role-label">Ответ от</span>
          <${Select} value=${role} aria-label="От чьего имени модель пишет следующее сообщение"
            onChange=${(e) => onRole(e.target.value)}>
            ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
          </${Select}>
        </label>
        <span class="spacer"></span>
        ${running
          ? html`<button type="button" class="danger" data-slot="stop-button" onClick=${onStop}>
              <${Icon} name="square" /> Стоп</button>`
          : html`<button type="submit" class="primary" data-slot="send-button"
              disabled=${disabled || uploading || (!text.trim() && !images.length)}>
              <${Icon} name="send-horizontal" /> Отправить</button>`}
      </div>
    </form>
  `;
}
