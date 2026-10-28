// Message input. Enter sends, Shift+Enter inserts a newline. While a reply is being written,
// the send button turns into Stop. Your text is always a user message; the role menu decides who
// the model's next messages arrive from (assistant, system, or user for writing in your place).
import { html, useState, useRef } from "../vendor/preact-htm.mjs";
import { ROLES } from "./roles.js";

export function Composer({ running, disabled, role, onRole, onSend, onStop }) {
  const [text, setText] = useState("");
  const box = useRef(null);

  const submit = async () => {
    const value = text.trim();
    if (!value || running || disabled) return;
    setText("");
    await onSend(value);
    if (box.current) box.current.focus();
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
      <label class="role-pick" title="От кого придут следующие сообщения модели">
        <span class="muted role-label">Ответ от</span>
        <select value=${role} aria-label="От кого придут следующие сообщения модели"
          onChange=${(e) => onRole(e.target.value)}>
          ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.icon} ${r.label}</option>`)}
        </select>
      </label>
      <textarea ref=${box} rows="2" value=${text} placeholder="Напишите сообщение…" data-slot="composer-input"
        aria-label="Сообщение" disabled=${disabled} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${onKeyDown}></textarea>
      ${running
        ? html`<button type="button" class="danger" data-slot="stop-button" onClick=${onStop}>Стоп</button>`
        : html`<button type="submit" class="primary" data-slot="send-button" disabled=${disabled || !text.trim()}>Отправить</button>`}
    </form>
  `;
}
