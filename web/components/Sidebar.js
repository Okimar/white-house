// Left panel: characters (import a card from a JSON file), lorebooks and the chats.
import { html, useRef } from "../vendor/preact-htm.mjs";
import { LorebookShelf } from "./Lorebooks.js";

export function Sidebar({ characters, chats, chatId, onOpenChat, onNewChat, onImport, onImportChat,
  onExportCharacter, onEditCharacter, onDeleteChat, onDeleteCharacter, onSettings, open, onClose,
  settings, onError }) {
  const fileInput = useRef(null);
  const chatFileInput = useRef(null);

  const pickFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) await onImport(file);
  };

  return html`
    <aside class=${"sidebar" + (open ? " open" : "")} data-slot="sidebar">
      <div class="side-head">
        <strong>Tavern</strong>
        <span class="spacer"></span>
        <button title="Настройки" onClick=${onSettings}>⚙</button>
        <button class="only-mobile" title="Закрыть" onClick=${onClose}>✕</button>
      </div>

      <h2>Персонажи</h2>
      <ul class="list">
        ${characters.map((c) => html`
          <li key=${c.id}>
            <button class="item" onClick=${() => onNewChat(c.id)} title="Начать новый чат">
              ${c.name}
            </button>
            <button title="Карточка персонажа" onClick=${() => onEditCharacter(c.id)}>✎</button>
            <button title="Экспортировать PNG" onClick=${() => onExportCharacter(c.id)}>↓</button>
            <button title="Удалить персонажа"
              onClick=${() => confirm(`Удалить персонажа «${c.name}»? Чаты останутся.`) && onDeleteCharacter(c.id)}>🗑</button>
          </li>`)}
      </ul>
      <input ref=${fileInput} type="file" accept=".json,.png,application/json,image/png" hidden onChange=${pickFile} />
      <input ref=${chatFileInput} type="file" accept=".jsonl,application/x-ndjson" hidden
        onChange=${async (e) => { const file = e.target.files[0]; e.target.value = ""; if (file) await onImportChat(file); }} />
      <div class="row">
        <button onClick=${() => onEditCharacter(null)}>+ Персонаж</button>
        <button onClick=${() => fileInput.current.click()}>Импорт карточки</button>
        <button onClick=${() => chatFileInput.current.click()}>Импорт чата</button>
        ${chatId && html`<a class="button" href=${`/api/chats/${chatId}/export.jsonl`}
          title="Скачать открытый чат (JSONL)">Экспорт чата</a>`}
        <button onClick=${() => onNewChat(null)}>Чат без персонажа</button>
      </div>

      <${LorebookShelf} settings=${settings} onError=${onError} />

      <h2>Чаты</h2>
      <ul class="list">
        ${chats.length === 0 && html`<li class="muted">Чатов пока нет</li>`}
        ${chats.map((c) => html`
          <li key=${c.id} class=${c.id === chatId ? "active" : ""}>
            <button class="item" onClick=${() => onOpenChat(c.id)}>${c.title}</button>
            <a class="button" href=${`/api/chats/${c.id}/export.jsonl`} title="Скачать чат (JSONL)">↓</a>
            <button title="Удалить чат"
              onClick=${() => confirm(`Удалить чат «${c.title}»?`) && onDeleteChat(c.id)}>🗑</button>
          </li>`)}
      </ul>
    </aside>
  `;
}
