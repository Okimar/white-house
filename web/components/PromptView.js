// "Что видела модель": the exact prompt a reply was generated from, part by part (preset blocks,
// cards, messages), with an estimated size and an exact count on demand. Every part can be copied;
// older versions of a part (an edited card or message) can be looked at, never edited.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { roleOf } from "./roles.js";

const PREVIEW = 400;

const copy = (text, onError) => navigator.clipboard.writeText(text).catch(() => onError("Не удалось скопировать"));
const tokens = (text) => Math.max(1, Math.floor(text.length / 4));

function Part({ entry, onVersions, onError }) {
  const [open, setOpen] = useState(entry.text.length <= PREVIEW);
  return html`
    <div class=${"prompt-part kind-" + (entry.kind || "other")} data-slot="prompt-part">
      <div class="row">
        <span title=${roleOf(entry.role).label}>${roleOf(entry.role).icon}</span>
        <strong>${entry.label || entry.kind}</strong>
        <span class="muted">~${tokens(entry.text)} ток.</span>
        <span class="spacer"></span>
        ${entry.versions > 1 && html`<button onClick=${() => onVersions(entry)}
          title="Посмотреть прошлые версии (только чтение)">Версии: ${entry.versions}</button>`}
        <button onClick=${() => copy(entry.text, onError)} title="Копировать">⧉</button>
      </div>
      <pre class="prompt-text">${open ? entry.text : entry.text.slice(0, PREVIEW) + "…"}</pre>
      ${entry.text.length > PREVIEW && html`<button class="link" onClick=${() => setOpen(!open)}>
        ${open ? "Свернуть" : "Показать всё"}</button>`}
    </div>`;
}

function Versions({ entry, onBack, onError }) {
  const [list, setList] = useState(null);
  useEffect(() => {
    api.get(`/prompt/versions?source=${encodeURIComponent(entry.source)}`)
      .then((d) => setList(d.versions)).catch((e) => onError(e.message));
  }, [entry.source]);
  return html`
    <div class="stack">
      <div class="row"><button onClick=${onBack}>← Назад</button><strong>Версии: ${entry.label}</strong></div>
      <p class="muted">Все тексты, с которыми эта часть уходила модели. Только просмотр и копирование.</p>
      ${list === null && html`<p class="muted">Загрузка…</p>`}
      ${(list || []).map((v, i) => html`
        <div key=${v.hash} class=${"prompt-part" + (v.hash === entry.hash ? " current" : "")}>
          <div class="row">
            <strong>Версия ${i + 1}</strong>
            <span class="muted">${new Date(v.first_seen).toLocaleString()}</span>
            ${v.hash === entry.hash && html`<span class="badge">в этом ответе</span>`}
            <span class="spacer"></span>
            <button onClick=${() => copy(v.text, onError)} title="Копировать">⧉</button>
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

  useEffect(() => {
    api.get(`/messages/${message.id}/prompt?swipe=${message.active_swipe}`)
      .then(setRecord)
      .catch((e) => (e.code === "no_prompt" ? setMissing(e.message) : onError(e.message)));
  }, [message.id, message.active_swipe]);

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
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="dialog wide" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Что видела модель">
        <div class="side-head"><strong>Что видела модель — №${message.depth + 1}</strong><span class="spacer"></span>
          <button onClick=${onClose} title="Закрыть">✕</button></div>
        ${missing && html`<p class="muted">Для этого сообщения промпт не сохранён: оно написано вручную,
          импортировано или создано до появления этой функции.</p>`}
        ${!record && !missing && html`<p class="muted">Загрузка…</p>`}
        ${record && (versionsOf
          ? html`<${Versions} entry=${versionsOf} onBack=${() => setVersionsOf(null)} onError=${onError} />`
          : html`
          <div class="prompt-meta">
            <div>Пресет: <strong>${meta.preset_name}</strong> · модель: <strong>${meta.model || "—"}</strong>
              ${` (${meta.connection_name})`}</div>
            <div>Примерно ${estimate} токенов${meta.max_context_tokens ? ` из ${meta.max_context_tokens}` : ""}
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
