// One chat message: number, role, text, swipe arrows, edit, delete and the branch list. Text is
// rendered as plain text (never as HTML), so a model or a card cannot inject markup.
// The role icon shows who the model takes the message from; clicking it opens a role menu.
// Author's notes kept after the message hang beside it: visible ones on the right (folded once
// AUTO_FOLD messages came after it), hidden ones on the left only when asked for. With the setting
// notes_display = "few" a column shows its last FEW notes and an "ещё N" chip; 📝 and the chip
// open the drop-down with every note of the message.
// With spending shown under messages (`spend` = the spending settings), a small line says what this
// version cost ("≈" = the service gave no counts, or the reply was stopped).
// Plugins add buttons to the header and plain-text lines under the text (core/plugins.js).
import { html, useState } from "../vendor/preact-htm.mjs";
import { ROLES, roleOf } from "./roles.js";
import { usageLine } from "../core/spending.js";
import { messageExtras, usePluginUi } from "../core/plugins.js";

function usageTitle(u) {
  return `Запрос: ${u.prompt_tokens} токенов, ответ: ${u.completion_tokens}` +
    (u.requests > 1 ? `, запросов: ${u.requests}` : "") + (u.estimated ? ". Примерно: сервис не прислал счёт" : "") +
    (u.unpriced ? ". Цена модели неизвестна — укажите свою в настройках «Расход»" : "");
}

const AUTO_FOLD = 3;
const FEW = 2;

function NoteColumn({ notes, side, few, onHide, onMore }) {
  const shown = few && notes.length > FEW ? notes.slice(-FEW) : notes;
  return html`<aside class=${"msg-notes " + side}
    data-slot=${side === "left" ? "message-notes-hidden" : "message-notes-visible"}>
    ${shown.map((n) => html`<${NoteCard} key=${n.id} note=${n} onHide=${onHide} />`)}
    ${shown.length < notes.length && html`<button class="notes-more" data-slot="notes-more" onClick=${onMore}
      title="Все заметки этого сообщения">ещё ${notes.length - shown.length}</button>`}
  </aside>`;
}

function NoteCard({ note, onHide }) {
  return html`<div class=${"msg-note " + note.kind} data-slot="message-note" data-kind=${note.kind}
    title=${note.kind === "hidden" ? "Невидимая заметка: модель видит её, в чате она скрыта"
      : "Видимая заметка: модель видит её после этого сообщения"}>
    <span class="msg-note-head">${note.kind === "hidden" ? "👻" : "📝"}
      ${note.title && html`<strong>${note.title}</strong>`}
      <span class="spacer"></span>
      ${onHide && html`<button title="Скрыть заметки сообщения" onClick=${onHide}>✕</button>`}</span>
    ${note.text}</div>`;
}

export function Message({ message, notes = [], showHidden, notesOpen, notesDisplay = "few", age = 0, avatar,
  streamText, streamThinking, busy, isLast, selecting, selected, onSelect, onRole, onEdit, onSwipe, onRemove, onRegenerate,
  onBranches, onPrompt, onExclude, onNotesOpen, onOpenNotes, spend }) {
  const plugins = usePluginUi(); // buttons and lines added by plugins (core/plugins.js)
  const [avatarFailed, setAvatarFailed] = useState(false);
  const [editing, setEditing] = useState(null); // draft text while editing
  const [picking, setPicking] = useState(false); // role menu open
  const streaming = streamText !== null && streamText !== undefined;
  const text = streaming ? streamText : message.content;
  const meta = message.swipe_meta || {};
  const thinking = streaming ? streamThinking : meta.reasoning;
  const role = roleOf(message.role);
  const canRegenerate = isLast && message.role !== "user" && !busy;
  const fallbackName = { user: "Вы", assistant: "Ассистент", system: "Система" }[message.role];

  const save = async () => {
    const draft = editing;
    setEditing(null);
    if (draft !== message.content) await onEdit(message.id, draft);
  };

  const pick = async (id) => {
    setPicking(false);
    if (id !== message.role) await onRole(id, { ids: [message.id] });
  };

  // notesOpen: true / false once the user pressed 📝 or ✕ here; undefined = automatic.
  const hidden = notes.filter((n) => n.kind === "hidden");
  const visible = notes.filter((n) => n.kind === "visible");
  const showVisible = notesOpen === true || (notesOpen === undefined && age < AUTO_FOLD);
  const showHiddenHere = notesOpen === true || (notesOpen !== false && showHidden);
  const anyShown = (showVisible && visible.length > 0) || (showHiddenHere && hidden.length > 0);
  const hide = () => onNotesOpen(message.id, false);
  const more = () => onOpenNotes(message, notesOpen === true);
  const few = notesDisplay !== "all";

  return html`<div class="msg-row" data-slot="message-row">
    ${showHiddenHere && hidden.length > 0 && html`<${NoteColumn} notes=${hidden} side="left" few=${few}
      onHide=${hide} onMore=${more} />`}
    <article class=${"msg msg-" + message.role + (selected ? " msg-selected" : "") + (message.exclude_from_prompt ? " msg-excluded" : "")}
      data-slot="message" data-role=${message.role} data-streaming=${streaming ? "true" : "false"}
      data-excluded=${message.exclude_from_prompt ? "true" : "false"}>
      <header data-slot="message-header">
        ${selecting && html`<input type="checkbox" checked=${selected} disabled=${busy}
          aria-label=${"Выбрать сообщение " + (message.depth + 1)} onChange=${() => onSelect(message.id)} />`}
        ${avatar && !avatarFailed && html`<img class="msg-avatar" data-slot="message-avatar" src=${avatar}
          alt="" loading="lazy" onError=${() => setAvatarFailed(true)} />`}
        <span class="msg-num" title="Номер сообщения">#${message.depth + 1}</span>
        <span class="role-wrap">
          <button class="role-icon" disabled=${busy} aria-haspopup="menu" aria-expanded=${picking}
            title=${"Модель видит это как сообщение от: " + role.label + ". Нажмите, чтобы сменить"}
            onClick=${() => setPicking(!picking)}>${role.icon}</button>
          ${picking && html`<span class="role-menu" role="menu">
            ${ROLES.map((r) => html`<button key=${r.id} role="menuitemradio" aria-checked=${r.id === message.role}
              onClick=${() => pick(r.id)}>${r.icon} ${r.label}</button>`)}
          </span>`}
        </span>
        <strong data-slot="message-name">${message.name || fallbackName}</strong>
        ${message.swipe_count > 1 && html`
          <span class="swipes">
            <button title="Предыдущий вариант" disabled=${busy || message.active_swipe === 0}
              onClick=${() => onSwipe(message.id, message.active_swipe - 1)}>‹</button>
            ${message.active_swipe + 1} / ${message.swipe_count}
            <button title="Следующий вариант" disabled=${busy || message.active_swipe >= message.swipe_count - 1}
              onClick=${() => onSwipe(message.id, message.active_swipe + 1)}>›</button>
          </span>`}
        <span class="spacer"></span>
        ${!busy && !editing && html`
          <button title="Редактировать" onClick=${() => setEditing(message.content)}>✎</button>
          <button title="Удалить" onClick=${() => confirm("Удалить это сообщение и всё после него?") && onRemove(message.id)}>🗑</button>
          <button title="Показать ветки" onClick=${() => onBranches(message.id)}>⑂</button>
          <button title="Что видела модель перед этим сообщением" onClick=${() => onPrompt(message)}>👁</button>
          <button data-slot="message-exclude" aria-pressed=${message.exclude_from_prompt}
            title=${message.exclude_from_prompt ? "Скрыто от модели. Нажмите, чтобы модель снова его видела"
              : "Скрыть от модели: сообщение останется в чате, но модель его не прочтёт"}
            onClick=${() => onExclude(message.id, !message.exclude_from_prompt)}>🙈</button>
          <button class=${"notes-toggle" + (notes.length ? " has" : "")} aria-pressed=${anyShown}
            title=${notes.length ? `Заметки этого сообщения (${notes.length}): посмотреть, изменить, добавить`
              : "Нет заметки. Нажмите, чтобы написать"}
            onClick=${more}>📝${notes.length ? html`<small>${notes.length}</small>` : ""}</button>
          ${!streaming && plugins.messageButtons.map((b, i) => html`<button key=${b.owner + i} data-slot="message-plugin-button"
            data-plugin=${b.owner} title=${typeof b.title === "function" ? b.title(message) : b.title}
            onClick=${() => b.onClick(message)}>${b.icon}</button>`)}`}
        ${canRegenerate && html`<button title="Другой вариант" onClick=${onRegenerate}>↻</button>`}
      </header>
      ${editing !== null
        ? html`
          <textarea class="edit" value=${editing} rows="6" onInput=${(e) => setEditing(e.target.value)}></textarea>
          <div class="row">
            <button class="primary" onClick=${save}>Сохранить</button>
            <button onClick=${() => setEditing(null)}>Отмена</button>
          </div>`
        : html`${thinking && html`<details class="thinking" data-slot="message-thinking" open=${streaming && !text}>
            <summary>💭 Размышления${streaming && !text ? "…" : ""}</summary><div class="thinking-text">${thinking}</div>
          </details>`}
          ${(meta.images || []).length > 0 && html`<div class="msg-images" data-slot="message-images">
            ${meta.images.map((name) => html`<a key=${name} href=${"/assets/" + encodeURIComponent(name)} target="_blank"
              rel="noopener"><img src=${"/assets/" + encodeURIComponent(name)} alt="картинка" loading="lazy" /></a>`)}
          </div>`}
          ${meta.via && !streaming && html`<div class="msg-via muted" data-slot="message-via"
            title="Основное подключение не ответило, ответил запасной путь">↪ через ${meta.via}</div>`}
          ${message.exclude_from_prompt && html`<div class="msg-excluded-note muted" data-slot="message-excluded">🙈 скрыто от модели</div>`}
          <div class="body" data-slot="message-body">${text}${streaming && html`<span class="caret" aria-hidden="true"></span>`}</div>
          ${!streaming && messageExtras(message).map((line, i) => html`<div key=${line.owner + i} class="msg-plugin"
            data-slot="message-plugin" data-plugin=${line.owner}>${line.text}</div>`)}
          ${spend && spend.show.message && meta.usage && !streaming && html`<div class="msg-usage muted"
            data-slot="message-usage" title=${usageTitle(meta.usage)}>${usageLine(meta.usage, spend)}</div>`}`}
    </article>
    ${showVisible && visible.length > 0 && html`<${NoteColumn} notes=${visible} side="right" few=${few}
      onHide=${hide} onMore=${more} />`}
  </div>`;
}
