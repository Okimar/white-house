// Message input. Enter sends, Shift+Enter inserts a newline. While a reply is being written,
// the send button turns into Stop. Your text is always a user message; the role menu decides who
// the model's next messages arrive from (assistant, system, or user for writing in your place).
// 📎 (or pasting a picture) adds pictures to the message: they go to the picture library and the
// model sees them with the message. Plugins add buttons here (core/plugins.js); "/command" text
// is offered to plugins first by the app shell (main.js).
import { html, useState, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { ROLES } from "./roles.js";
import { usePluginUi } from "../core/plugins.js";
import { Icon, IconButton } from "./Icon.js";

const MAX_IMAGES = 8;

export function Composer({ running, disabled, role, onRole, onSend, onStop, onError }) {
  const plugins = usePluginUi(); // buttons plugins put beside the message box
  const [text, setText] = useState("");
  const [images, setImages] = useState([]); // picture names in the library
  const [uploading, setUploading] = useState(false);
  const box = useRef(null);
  const picker = useRef(null);

  const submit = async () => {
    const value = text.trim();
    if ((!value && !images.length) || running || disabled || uploading) return;
    const pictures = images;
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
      <textarea ref=${box} rows="2" value=${text} placeholder="Напишите сообщение" data-slot="composer-input"
        aria-label="Сообщение" disabled=${disabled} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${onKeyDown} onPaste=${onPaste}></textarea>
      <div class="composer-tools">
        <${IconButton} icon=${uploading ? "loader-circle" : "paperclip"} class="attach" data-slot="attach-button"
          label="Прикрепить картинку" disabled=${disabled || uploading || images.length >= MAX_IMAGES}
          onClick=${() => picker.current && picker.current.click()} />
        ${plugins.composerButtons.map((b, i) => html`<button key=${b.owner + i} type="button" class="attach icon-btn"
          data-slot="composer-plugin-button" data-plugin=${b.owner} title=${b.title} aria-label=${b.title}
          disabled=${disabled || running} onClick=${() => b.onClick()}>${b.icon}</button>`)}
        <input ref=${picker} type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple hidden
          onChange=${(e) => { attach(e.target.files); e.target.value = ""; }} />
        <label class="role-pick" title="От чьего имени модель пишет следующее сообщение">
          <span class="muted role-label">Ответ от</span>
          <select value=${role} aria-label="От чьего имени модель пишет следующее сообщение"
            onChange=${(e) => onRole(e.target.value)}>
            ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
          </select>
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
