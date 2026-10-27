// One chat message: number, role, text, swipe arrows, edit, delete and the branch list. Text is
// rendered as plain text (never as HTML), so a model or a card cannot inject markup.
// The role icon shows who the model takes the message from; clicking it opens a role menu.
import { html, useState } from "../vendor/preact-htm.mjs";
import { ROLES, roleOf } from "./roles.js";

export function Message({ message, streamText, busy, isLast, selecting, selected, onSelect,
  onRole, onEdit, onSwipe, onRemove, onRegenerate, onBranches }) {
  const [editing, setEditing] = useState(null); // draft text while editing
  const [picking, setPicking] = useState(false); // role menu open
  const streaming = streamText !== null && streamText !== undefined;
  const text = streaming ? streamText : message.content;
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

  return html`
    <article class=${"msg msg-" + message.role + (selected ? " msg-selected" : "")}>
      <header>
        ${selecting && html`<input type="checkbox" checked=${selected} disabled=${busy}
          aria-label=${"Выбрать сообщение " + (message.depth + 1)} onChange=${() => onSelect(message.id)} />`}
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
        <strong>${message.name || fallbackName}</strong>
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
          <button title="Показать ветки" onClick=${() => onBranches(message.id)}>⑂</button>`}
        ${canRegenerate && html`<button title="Другой вариант" onClick=${onRegenerate}>↻</button>`}
      </header>
      ${editing !== null
        ? html`
          <textarea class="edit" value=${editing} rows="6" onInput=${(e) => setEditing(e.target.value)}></textarea>
          <div class="row">
            <button class="primary" onClick=${save}>Сохранить</button>
            <button onClick=${() => setEditing(null)}>Отмена</button>
          </div>`
        : html`<div class="body">${text}${streaming && html`<span class="caret" aria-hidden="true"></span>`}</div>`}
    </article>
  `;
}
