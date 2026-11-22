// Own questions instead of the browser's confirm() and prompt(): they follow the theme, and a phone's
// browser cannot silently suppress them.
//   if (await ask("Удалить чат?", { ok: "Удалить", danger: true })) ...
//   const name = await askText("Название чата", { value: old });  // null = cancelled
// <DialogHost /> lives once in the app shell.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { backdrop, useEscape } from "../core/keys.js";
import { api } from "../core/api.js";
import { Icon } from "./Icon.js";

let show = null;

// «Не спрашивать больше»: a delete question that is safe to skip carries a key (`ask(text, { skip: "message" })`).
// The dialog then has the checkbox; once ticked and confirmed the question is never asked again, until
// Settings > Основное turns it back on. The choice is the server setting `skip_confirms` (key -> true).
// Never for whole characters, chats, lorebooks, connections and backups: those stay asked.
export const SKIP_ASKS = [
  ["message", "Удаление сообщения (следующие остаются)"],
  ["swipe", "Удаление одного варианта ответа"],
  ["note", "Удаление заметки автора"],
  ["member", "Убрать персонажа из чата"],
  ["rule", "Удаление правила замены текста"],
  ["picture", "Удаление картинки из галереи"],
  ["profile", "Удаление профиля подключения"],
  ["skill", "Удаление инструкции помощника"],
  ["key", "Удаление ключа подключения"],
];
let skips = null; // key -> true, null until the setting is read
let skipsLoading = null;

export function syncSkips(value) { skips = value && typeof value === "object" ? { ...value } : {}; }
const loadSkips = () => skips ? Promise.resolve(skips)
  : (skipsLoading ||= api.get("/settings").then((s) => { syncSkips(s.skip_confirms); return skips; })
    .catch(() => { skipsLoading = null; return {}; }));
export function isSkipped(key) { return Boolean(skips && skips[key]); }
export async function setSkip(key, on) {
  const next = { ...(await loadSkips()) };
  if (on) next[key] = true; else delete next[key];
  const saved = await api.put("/settings", { skip_confirms: next });
  syncSkips(saved.skip_confirms);
  window.dispatchEvent(new Event("tavern:settings-changed")); // the app's copy of the settings is read again (Settings shows it)
  return skips;
}

function open(question) {
  return new Promise((resolve) => {
    if (!show) { resolve(question.input ? null : false); return; }
    show({ ...question, resolve });
  });
}

export async function ask(text, { ok = "Да", cancel = "Отмена", danger = false, hint = "", skip = "" } = {}) {
  if (skip && (await loadSkips())[skip]) return true;
  const answer = await open({ text, ok, cancel, danger, hint, skip });
  if (answer === true && skip && open.remember) { open.remember = false; setSkip(skip, true).catch(() => {}); }
  return answer;
}

export const askText = (text, { value = "", placeholder = "", ok = "Сохранить", cancel = "Отмена", hint = "" } = {}) =>
  open({ text, ok, cancel, hint, input: true, value, placeholder });

function Question({ question, onDone }) {
  const [value, setValue] = useState(question.value || "");
  const [never, setNever] = useState(false);
  const formRef = useRef(null);
  const inputRef = useRef(null);
  const okRef = useRef(null);
  const cancelRef = useRef(null);
  const opener = useRef(document.activeElement);

  useEffect(() => {
    if (question.input && inputRef.current) {
      inputRef.current.focus();
      if (inputRef.current.select) inputRef.current.select();
    } else if (question.danger && question.cancel && cancelRef.current) {
      cancelRef.current.focus();
    } else if (okRef.current) {
      okRef.current.focus();
    }
  }, []);

  const finish = (ans) => {
    onDone(ans);
    try { opener.current?.focus?.(); } catch (e) { /* opener detached */ }
  };
  const cancel = () => finish(question.input ? null : false);
  useEscape(cancel);

  const onKeyDown = (e) => {
    if (e.key === "Tab" && formRef.current) {
      const focusable = [...formRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled), [tabindex="0"]')];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return html`<div class="overlay ask-overlay" ...${backdrop(cancel)}>
    <form ref=${formRef} class="dialog ask" data-slot="dialog" data-danger=${question.danger ? "true" : "false"} role="alertdialog" aria-modal="true"
      aria-labelledby="ask-dialog-title" aria-describedby=${question.hint ? "ask-dialog-hint" : undefined}
      onKeyDown=${onKeyDown}
      onSubmit=${(e) => { e.preventDefault(); open.remember = never; finish(question.input ? value : true); }}>
      <div class="ask-head">
        ${question.danger && html`<span class="ask-danger-icon" aria-hidden="true"><${Icon} name="triangle-alert" size="20" /></span>`}
        <p id="ask-dialog-title" class="ask-text">${question.text}</p>
      </div>
      ${question.hint && html`<p id="ask-dialog-hint" class="muted ask-hint">${question.hint}</p>`}
      ${question.skip && html`<label class="ask-never" data-slot="ask-never"><input type="checkbox" checked=${never}
        onChange=${(e) => setNever(e.target.checked)} /> <span>Больше не спрашивать</span>
        <span class="hint">Вернуть вопрос можно в настройках, вкладка «Основное», раздел «Удаление».</span></label>`}
      ${question.input && html`<input ref=${inputRef} value=${value} placeholder=${question.placeholder}
        aria-label=${question.text} enterkeyhint="done" onInput=${(e) => setValue(e.target.value)} />`}
      <div class="row ask-actions">
        <span class="spacer"></span>
        ${question.cancel && html`<button ref=${cancelRef} type="button" class="ask-cancel" onClick=${cancel}>${question.cancel}</button>`}
        <button ref=${okRef} type="submit"
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
