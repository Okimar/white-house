// One chat message: text, swipe arrows, edit and delete. Text is rendered as plain text
// (never as HTML), so a model or a card cannot inject markup.
import { html, useState } from "../vendor/preact-htm.mjs";

export function Message({ message, streamText, busy, isLast, onEdit, onSwipe, onRemove, onRegenerate }) {
  const [editing, setEditing] = useState(null); // draft text while editing
  const streaming = streamText !== null && streamText !== undefined;
  const text = streaming ? streamText : message.content;
  const mine = message.role === "user";
  const canRegenerate = isLast && message.role === "assistant" && !busy;

  const save = async () => {
    const draft = editing;
    setEditing(null);
    if (draft !== message.content) await onEdit(message.id, draft);
  };

  return html`
    <article class=${"msg " + (mine ? "msg-user" : "msg-bot")}>
      <header>
        <strong>${message.name || (mine ? "Вы" : "Ассистент")}</strong>
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
          <button title="Удалить" onClick=${() => confirm("Удалить это сообщение и всё после него?") && onRemove(message.id)}>🗑</button>`}
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
