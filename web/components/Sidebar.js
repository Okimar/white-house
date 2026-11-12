// Menu: chats, characters (import a card from a JSON or PNG file) and lorebooks. `mode` "side" = the left
// panel; "top" = a drop-down under the menu bar (main.js), one section at a time like on a phone.
// On a phone it is a sheet under the top bar showing one section (`section`), chosen by the small icons
// in the bar. A press on a character only opens its list of actions: a new chat is an explicit choice.
import { html, useRef } from "../vendor/preact-htm.mjs";
import { LorebookShelf } from "./Lorebooks.js";
import { GroupedItems } from "./Groups.js";
import { IconButton } from "./Icon.js";
import { MenuButton, openMenu } from "./Menu.js";
import { ask, askText } from "./Dialogs.js";
import { EmptyNote } from "./State.js";

export function Sidebar({ characters, chats, chatId, onOpenChat, onNewChat, onImport, onImportChat, onRenameChat,
  onExportCharacter, onEditCharacter, onDeleteChat, onDeleteCharacter, onSettings, onAssistant, onSearch, onHome, section,
  settings, onError, mode, onFold }) {
  const fileInput = useRef(null);
  const chatFileInput = useRef(null);

  const pickFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) await onImport(file);
  };

  const characterActions = (c, tools) => [
    { icon: "message-square-plus", label: "Новый чат", onClick: () => onNewChat(c.id) },
    { icon: "pencil", label: "Карточка персонажа", onClick: () => onEditCharacter(c.id) },
    { icon: "download", label: "Скачать (PNG)", onClick: () => onExportCharacter(c.id) },
    { icon: "library", label: "В группу…", onClick: tools.move },
    { icon: "trash-2", label: "Удалить", danger: true, onClick: async () => {
      if (await ask(`Удалить персонажа «${c.name}»?`, { ok: "Удалить", danger: true, hint: "Чаты останутся." })) onDeleteCharacter(c.id);
    } },
  ];

  const chatActions = (c, tools) => [
    { icon: "pencil", label: "Переименовать", onClick: async () => {
      const title = ((await askText("Название чата", { value: c.title })) || "").trim();
      if (title && title !== c.title) onRenameChat(c.id, title);
    } },
    { icon: "download", label: "Скачать", href: `/api/chats/${c.id}/export.jsonl` },
    { icon: "library", label: "В группу…", onClick: tools.move },
    { icon: "trash-2", label: "Удалить", danger: true, onClick: async () => {
      if (await ask(`Удалить чат «${c.title}»?`, { ok: "Удалить", danger: true })) onDeleteChat(c.id);
    } },
  ];

  return html`
    <aside class="sidebar" data-slot="sidebar" data-section=${section || ""} data-mode=${mode || "side"}>
      <div class="side-head only-wide">
        ${onFold && html`<${IconButton} icon="chevron-left" label="Свернуть меню" class="fold-menu" data-slot="fold-menu"
          onClick=${onFold} />`}
        <button class="brand brand-home" data-slot="home-button" title="На главную: чат останется в списке"
          aria-label="На главную" onClick=${onHome}>White House</button>
        <span class="spacer"></span>
        <${IconButton} icon="search" label="Поиск по всем чатам" data-slot="search-button" onClick=${onSearch} />
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
        <input ref=${chatFileInput} type="file" accept=".jsonl,.json,application/x-ndjson,application/json,text/plain" hidden
          onChange=${async (e) => { const file = e.target.files[0]; e.target.value = ""; if (file) await onImportChat(file); }} />
        ${chats.length === 0 && html`<${EmptyNote} hint="Чатов пока нет. Новый чат начинается с персонажа: нажмите на него в разделе «Персонажи»." />`}
        <${GroupedItems} kind="chat" items=${chats} idOf=${(c) => c.id} labelOf=${(c) => c.title} activeId=${chatId}
          onError=${onError}
          onPress=${(c, el, tools) => openMenu(el, [
            { icon: "message-square", label: "Открыть", onClick: () => onOpenChat(c.id) }, ...chatActions(c, tools)])}
          renderRow=${(c, tools) => html`
            <li key=${c.id} class=${c.id === chatId ? "active" : ""}>
              <button class="item" aria-current=${c.id === chatId ? "true" : null} onClick=${() => onOpenChat(c.id)}
                title=${c.title + "\nИзменён: " + new Date(c.updated_at).toLocaleString("ru-RU")}>${c.title}</button>
              <${MenuButton} label=${"Действия с чатом " + c.title} data-slot="chat-rename" items=${() => chatActions(c, tools)} />
            </li>`} />
      </section>

      <section class="side-section" data-name="characters" data-slot="characters">
        <div class="section-head">
          <h2>Персонажи</h2><span class="spacer"></span>
          <${IconButton} icon="plus" label="Новый персонаж" onClick=${() => onEditCharacter(null)} />
          <${IconButton} icon="upload" label="Импорт карточки" onClick=${() => fileInput.current.click()} />
        </div>
        <input ref=${fileInput} type="file" accept=".json,.png,application/json,image/png" hidden onChange=${pickFile} />
        ${characters.length === 0 && html`<${EmptyNote} hint="Создайте персонажа или загрузите карточку, чтобы начать чат." />`}
        <${GroupedItems} kind="character" items=${characters} idOf=${(c) => c.id} labelOf=${(c) => c.name}
          onError=${onError} onPress=${(c, el, tools) => openMenu(el, characterActions(c, tools))}
          renderRow=${(c, tools) => html`
            <li key=${c.id}>
              <button class="item" aria-haspopup="menu" title=${c.name + " — действия с персонажем"}
                onClick=${(e) => openMenu(e.currentTarget, characterActions(c, tools))}>${c.name}</button>
            </li>`} />
      </section>

      <section class="side-section" data-name="lorebooks">
        <${LorebookShelf} settings=${settings} onError=${onError} />
      </section>
    </aside>
  `;
}
