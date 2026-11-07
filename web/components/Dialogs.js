// Own questions instead of the browser's confirm() and prompt(): they follow the theme, and a phone's
// browser cannot silently suppress them.
//   if (await ask("Удалить чат?", { ok: "Удалить", danger: true })) ...
//   const name = await askText("Название чата", { value: old });  // null = cancelled
// <DialogHost /> lives once in the app shell.
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { backdrop, useEscape } from "../core/keys.js";
import { api } from "../core/api.js";

let show = null;

// «Не спрашивать больше»: a delete question that is safe to skip carries a key (`ask(text, { skip: "message" })`).
// The dialog then has the checkbox; once ticked and confirmed the question is never asked again, until
// Settings > Основное turns it back on. The choice is the server setting `skip_confirms` (key -> true).
// Never for whole characters, chats, lorebooks, connections and backups: those stay asked.
export const SKIP_ASKS = [
  ["message", "Удаление сообщения (и всего после него)"],
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
      onSubmit=${(e) => { e.preventDefault(); open.remember = never; onDone(question.input ? value : true); }}>
      <p class="ask-text">${question.text}</p>
      ${question.hint && html`<p class="muted">${question.hint}</p>`}
      ${question.skip && html`<label class="ask-never" data-slot="ask-never"><input type="checkbox" checked=${never}
        onChange=${(e) => setNever(e.target.checked)} /> Больше не спрашивать
        <span class="hint">Вернуть вопрос можно в настройках, вкладка «Основное», раздел «Удаление».</span></label>`}
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
