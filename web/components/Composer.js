// Message input. Enter sends, Shift+Enter inserts a newline. While a reply is being written,
// the send button turns into Stop.
import { html, useState, useRef } from "../vendor/preact-htm.mjs";

export function Composer({ running, disabled, onSend, onStop }) {
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
    <form class="composer" onSubmit=${(e) => { e.preventDefault(); submit(); }}>
      <textarea ref=${box} rows="2" value=${text} placeholder="Напишите сообщение…"
        aria-label="Сообщение" disabled=${disabled} onInput=${(e) => setText(e.target.value)}
        onKeyDown=${onKeyDown}></textarea>
      ${running
        ? html`<button type="button" class="danger" onClick=${onStop}>Стоп</button>`
        : html`<button type="submit" class="primary" disabled=${disabled || !text.trim()}>Отправить</button>`}
    </form>
  `;
}
