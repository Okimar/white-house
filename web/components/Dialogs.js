// Own questions instead of the browser's confirm() and prompt(): they follow the theme, and a phone's
// browser cannot silently suppress them.
//   if (await ask("Удалить чат?", { ok: "Удалить", danger: true })) ...
//   const name = await askText("Название чата", { value: old });  // null = cancelled
// <DialogHost /> lives once in the app shell.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { backdrop, useEscape } from "../core/keys.js";

let show = null;

function open(question) {
  return new Promise((resolve) => {
    if (!show) { resolve(question.input ? null : false); return; }
    show({ ...question, resolve });
  });
}

export const ask = (text, { ok = "Да", cancel = "Отмена", danger = false, hint = "" } = {}) =>
  open({ text, ok, cancel, danger, hint });

export const askText = (text, { value = "", placeholder = "", ok = "Сохранить", cancel = "Отмена", hint = "" } = {}) =>
  open({ text, ok, cancel, hint, input: true, value, placeholder });

function Question({ question, onDone }) {
  const [value, setValue] = useState(question.value || "");
  const first = useRef(null);
  useEffect(() => {
    if (!first.current) return;
    first.current.focus();
    if (first.current.select) first.current.select();
  }, []);
  const cancel = () => onDone(question.input ? null : false);
  useEscape(cancel);
  return html`<div class="overlay ask-overlay" ...${backdrop(cancel)}>
    <form class="dialog ask" data-slot="dialog" role="alertdialog" aria-modal="true" aria-label=${question.text}
      onSubmit=${(e) => { e.preventDefault(); onDone(question.input ? value : true); }}>
      <p class="ask-text">${question.text}</p>
      ${question.hint && html`<p class="muted">${question.hint}</p>`}
      ${question.input && html`<input ref=${first} value=${value} placeholder=${question.placeholder}
        aria-label=${question.text} onInput=${(e) => setValue(e.target.value)} />`}
      <div class="row ask-actions">
        <span class="spacer"></span>
        ${question.cancel && html`<button type="button" onClick=${cancel}>${question.cancel}</button>`}
        <button ref=${question.input ? null : first} type="submit"
          class=${question.danger ? "danger" : "primary"}>${question.ok}</button>
      </div>
    </form>
  </div>`;
}

export function DialogHost() {
  const [question, setQuestion] = useState(null);
  useEffect(() => {
    show = setQuestion;
    return () => { show = null; };
  }, []);
  if (!question) return null;
  return html`<${Question} key=${question.text} question=${question} onDone=${(answer) => {
    setQuestion(null);
    question.resolve(answer);
  }} />`;
}
