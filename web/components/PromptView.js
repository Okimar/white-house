// "Что видела модель": the exact prompt a reply was generated from, part by part (preset blocks,
// cards, messages), with an estimated size and an exact count on demand. Every part can be copied;
// older versions of a part (an edited card or message) can be looked at, never edited.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { roleOf } from "./roles.js";
import { Icon, IconButton } from "./Icon.js";
import { Pending } from "./State.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";

const PREVIEW = 400;
const ROLE_ICONS = { user: "user", assistant: "bot", system: "terminal" };

// Over plain http (White House opened by a LAN address from a phone) navigator.clipboard does not exist:
// the older way is a temporary text field and execCommand.
function copyByField(text) {
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.top = "0";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  try { return document.execCommand("copy"); } catch { return false; } finally { field.remove(); }
}

async function copy(text, onError) {
  let done = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      done = true;
    }
  } catch { /* try the older way */ }
  if (!done) done = copyByField(text);
  if (done) notify("Скопировано", "ok");
  else onError("Не удалось скопировать");
}

const tokens = (text) => Math.max(1, Math.floor(text.length / 4));

function plural(n, forms) {
  const last = n % 10;
  const two = n % 100;
  if (last === 1 && two !== 11) return forms[0];
  if (last >= 2 && last <= 4 && (two < 12 || two > 14)) return forms[1];
  return forms[2];
}
const tokenWord = (n) => plural(n, ["токен", "токена", "токенов"]);

function Part({ entry, onVersions, onError }) {
  const [open, setOpen] = useState(entry.text.length <= PREVIEW);
  return html`
    <div class=${"prompt-part kind-" + (entry.kind || "other")} data-slot="prompt-part">
      <div class="row">
        <span title=${roleOf(entry.role).label} aria-label=${roleOf(entry.role).label}><${Icon} name=${ROLE_ICONS[entry.role] || "user"} /></span>
        <strong>${entry.label || entry.kind}</strong>
        <span class="muted">≈${tokens(entry.text)} ${tokenWord(tokens(entry.text))}</span>
        <span class="spacer"></span>
        ${entry.versions > 1 && html`<button onClick=${() => onVersions(entry)}>Версии: ${entry.versions}</button>`}
        <${IconButton} icon="copy" label="Копировать" onClick=${() => copy(entry.text, onError)} />
      </div>
      <pre class="prompt-text">${open ? entry.text : entry.text.slice(0, PREVIEW) + "…"}</pre>
      ${entry.text.length > PREVIEW && html`<button class="link" onClick=${() => setOpen(!open)}>
        ${open ? "Свернуть" : "Показать всё"}</button>`}
    </div>`;
}

function Versions({ entry, onBack, onError }) {
  const [list, setList] = useState(null);
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    api.get(`/prompt/versions?source=${encodeURIComponent(entry.source)}`)
      .then((d) => setList(d.versions)).catch((e) => setError(e.message));
  };
  useEffect(load, [entry.source]);
  return html`
    <div class="stack">
      <div class="row"><button onClick=${onBack}><${Icon} name="chevron-left" /> Назад</button><strong>Версии: ${entry.label}</strong></div>
      <p class="muted">Все тексты, с которыми эта часть уходила модели. Только просмотр и копирование.</p>
      ${list === null && html`<${Pending} error=${error} onRetry=${load} />`}
      ${(list || []).map((v, i) => html`
        <div key=${v.hash} class=${"prompt-part" + (v.hash === entry.hash ? " current" : "")}>
          <div class="row">
            <strong>Версия ${i + 1}</strong>
            <span class="muted">${new Date(v.first_seen).toLocaleString()}</span>
            ${v.hash === entry.hash && html`<span class="badge">в этом ответе</span>`}
            <span class="spacer"></span>
            <${IconButton} icon="copy" label="Копировать" onClick=${() => copy(v.text, onError)} />
          </div>
          <pre class="prompt-text">${v.text}</pre>
        </div>`)}
    </div>`;
}

export function PromptView({ message, onClose, onError }) {
  const [record, setRecord] = useState(null);
  const [missing, setMissing] = useState("");
  const [exact, setExact] = useState(null);
  const [counting, setCounting] = useState(false);
  const [versionsOf, setVersionsOf] = useState(null);
  const [error, setError] = useState("");
  useEscape(onClose);

  const load = () => {
    setError("");
    api.get(`/messages/${message.id}/prompt?swipe=${message.active_swipe}`)
      .then(setRecord)
      .catch((e) => (e.code === "no_prompt" ? setMissing(e.message) : setError(e.message)));
  };
  useEffect(load, [message.id, message.active_swipe]);

  const count = async () => {
    setCounting(true);
    try {
      const result = await api.post(`/messages/${message.id}/prompt/count?swipe=${message.active_swipe}`);
      setExact(result.tokens);
    } catch (e) {
      onError(e.message);
    } finally {
      setCounting(false);
    }
  };

  const whole = record ? record.entries.map((e) => e.text).join("\n\n") : "";
  const meta = record ? record.meta : {};
  const estimate = record ? record.entries.reduce((sum, e) => sum + tokens(e.text), 0) : 0;
  const dropped = meta.dropped || {};

  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog wide" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Что видела модель">
        <div class="side-head"><strong>Что видела модель — №${message.depth + 1}</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        ${missing && html`<p class="muted">Для этого сообщения промпт не сохранён: оно написано вручную,
          импортировано или создано до появления этой функции.</p>`}
        ${!record && !missing && html`<${Pending} error=${error} onRetry=${load} />`}
        ${record && (versionsOf
          ? html`<${Versions} entry=${versionsOf} onBack=${() => setVersionsOf(null)} onError=${onError} />`
          : html`
          <div class="prompt-meta">
            <div>Пресет: <strong>${meta.preset_name}</strong> · модель: <strong>${meta.model || "—"}</strong>
              ${` (${meta.connection_name})`}</div>
            <div>Примерно ${estimate} ${tokenWord(estimate)}${meta.max_context_tokens ? ` из ${meta.max_context_tokens}` : ""}
              ${exact !== null && html` · точно: <strong>${exact}</strong>`}</div>
            ${(dropped.messages > 0 || (dropped.members || []).length > 0) && html`<div class="notice">
              Не влезло в память модели: ${dropped.messages > 0 ? `старых сообщений — ${dropped.messages}` : ""}
              ${(dropped.members || []).length ? ` карточки: ${dropped.members.join(", ")}` : ""}</div>`}
            ${(dropped.lore || []).length > 0 && html`<div class="notice">
              Не влезло в лимит лорбуков: ${dropped.lore.join(", ")}</div>`}
            <div class="row">
              <button onClick=${count} disabled=${counting}>${counting ? "Считаю…" : "Посчитать точно"}</button>
              <button onClick=${() => copy(whole, onError)}>Копировать всё</button>
            </div>
          </div>
          ${record.entries.map((entry, i) => html`
            ${(i === 0 || record.entries[i - 1].msg !== entry.msg) && html`<div class="prompt-msg-sep"
              key=${"sep" + i}>Сообщение ${entry.msg + 1} · ${roleOf(entry.role).label}</div>`}
            <${Part} key=${i} entry=${entry} onVersions=${setVersionsOf} onError=${onError} />`)}`)}
      </div>
    </div>
  `;
}
