// Left panel: chats, characters (import a card from a JSON or PNG file) and lorebooks.
// On a phone it is a sheet under the top bar showing one section (`section`), chosen by the small icons
// in the bar. A press on a character only opens its list of actions: a new chat is an explicit choice.
import { html, useRef } from "../vendor/preact-htm.mjs";
import { LorebookShelf } from "./Lorebooks.js";
import { IconButton } from "./Icon.js";
import { MenuButton, openMenu } from "./Menu.js";
import { ask, askText } from "./Dialogs.js";
import { EmptyNote } from "./State.js";

export function Sidebar({ characters, chats, chatId, onOpenChat, onNewChat, onImport, onImportChat, onRenameChat,
  onExportCharacter, onEditCharacter, onDeleteChat, onDeleteCharacter, onSettings, onAssistant, section,
  settings, onError }) {
  const fileInput = useRef(null);
  const chatFileInput = useRef(null);

  const pickFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) await onImport(file);
  };

  const characterActions = (c) => [
    { icon: "message-square-plus", label: "Новый чат", onClick: () => onNewChat(c.id) },
    { icon: "pencil", label: "Карточка персонажа", onClick: () => onEditCharacter(c.id) },
    { icon: "download", label: "Скачать (PNG)", onClick: () => onExportCharacter(c.id) },
    { icon: "trash-2", label: "Удалить", danger: true, onClick: async () => {
      if (await ask(`Удалить персонажа «${c.name}»?`, { ok: "Удалить", danger: true, hint: "Чаты останутся." })) onDeleteCharacter(c.id);
    } },
  ];

  const chatActions = (c) => [
    { icon: "pencil", label: "Переименовать", onClick: async () => {
      const title = ((await askText("Название чата", { value: c.title })) || "").trim();
      if (title && title !== c.title) onRenameChat(c.id, title);
    } },
    { icon: "download", label: "Скачать", href: `/api/chats/${c.id}/export.jsonl` },
    { icon: "trash-2", label: "Удалить", danger: true, onClick: async () => {
      if (await ask(`Удалить чат «${c.title}»?`, { ok: "Удалить", danger: true })) onDeleteChat(c.id);
    } },
  ];

  return html`
    <aside class="sidebar" data-slot="sidebar" data-section=${section || ""}>
      <div class="side-head only-wide">
        <strong class="brand">White House</strong>
        <span class="spacer"></span>
        <${IconButton} icon="wand" label="Помощник" data-slot="assistant-button" onClick=${onAssistant} />
        <${IconButton} icon="settings" label="Настройки" onClick=${onSettings} />
      </div>

      <section class="side-section" data-name="chats" data-slot="chats">
        <div class="section-head">
          <h2>Чаты</h2><span class="spacer"></span>
          <${MenuButton} label="Новый чат или импорт" icon="plus" items=${() => [
            { icon: "message-square", label: "Чат без персонажа", onClick: () => onNewChat(null) },
            { icon: "upload", label: "Импорт чата", onClick: () => chatFileInput.current.click() },
          ]} />
        </div>
        <input ref=${chatFileInput} type="file" accept=".jsonl,application/x-ndjson" hidden
          onChange=${async (e) => { const file = e.target.files[0]; e.target.value = ""; if (file) await onImportChat(file); }} />
        ${chats.length === 0 && html`<${EmptyNote} hint="Чатов пока нет. Новый чат начинается с персонажа: нажмите на него в разделе «Персонажи»." />`}
        <ul class="list">
          ${chats.map((c) => html`
            <li key=${c.id} class=${c.id === chatId ? "active" : ""}>
              <button class="item" aria-current=${c.id === chatId ? "true" : null} onClick=${() => onOpenChat(c.id)}
                title=${"Изменён: " + new Date(c.updated_at).toLocaleString("ru-RU")}>${c.title}</button>
              <${MenuButton} label=${"Действия с чатом " + c.title} data-slot="chat-rename" items=${() => chatActions(c)} />
            </li>`)}
        </ul>
      </section>

      <section class="side-section" data-name="characters" data-slot="characters">
        <div class="section-head">
          <h2>Персонажи</h2><span class="spacer"></span>
          <${IconButton} icon="plus" label="Новый персонаж" onClick=${() => onEditCharacter(null)} />
          <${IconButton} icon="upload" label="Импорт карточки" onClick=${() => fileInput.current.click()} />
        </div>
        <input ref=${fileInput} type="file" accept=".json,.png,application/json,image/png" hidden onChange=${pickFile} />
        ${characters.length === 0 && html`<${EmptyNote} hint="Создайте персонажа или загрузите карточку, чтобы начать чат." />`}
        <ul class="list">
          ${characters.map((c) => html`
            <li key=${c.id}>
              <button class="item" aria-haspopup="menu" title="Действия с персонажем"
                onClick=${(e) => openMenu(e.currentTarget, characterActions(c))}>${c.name}</button>
            </li>`)}
        </ul>
      </section>

      <section class="side-section" data-name="lorebooks">
        <${LorebookShelf} settings=${settings} onError=${onError} />
      </section>
    </aside>
  `;
}
