// Menu: chats, characters (import a card from a JSON or PNG file) and lorebooks. `mode` "side" = the left
// panel; "top" = a drop-down under the menu bar (main.js), one section at a time like on a phone.
// On a phone it is a sheet under the top bar showing one section (`section`), chosen by the small icons
// in the bar. A tap on a character opens its latest chat (as in SillyTavern); its «⋯» has the other actions.
// One rule for all three lists: a worded «+ Новый …» on top; a chat asks «с кем» (NewChatPicker, in main.js),
// a character or a book asks where it comes from (by hand, a file, the assistant).
// The chat import picker has no `accept`: Android greys out .jsonl (no MIME type there); the server checks the file.
import { html, useRef } from "../vendor/preact-htm.mjs";
import { LorebookShelf } from "./Lorebooks.js";
import { GroupedItems } from "./Groups.js";
import { IconButton } from "./Icon.js";
import { MenuButton, openMenu } from "./Menu.js";
import { ask, askText } from "./Dialogs.js";
import { EmptyNote } from "./State.js";
import { NewButton, chatFacts, lastText } from "./NewChat.js";

export function Sidebar({ characters, chats, chatId, onOpenChat, onNewChat, onPickNewChat, onImport, onImportChat,
  onRenameChat, onExportCharacter, onEditCharacter, onDeleteChat, onDeleteCharacter, onSettings, onAssistant, onAskAssistant,
  onSearch, onHome, section, settings, onError, mode, onFold }) {
  const fileInput = useRef(null);
  const chatFileInput = useRef(null);

  const pickFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (file) await onImport(file);
  };

  // A tap on a character opens the latest chat with them, as in SillyTavern (a new one when there is none).
  const latestChat = (c) => chats.filter((x) => x.character_id === c.id)
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0))[0];
  const openCharacter = (c) => {
    const last = latestChat(c);
    if (last) onOpenChat(last.id); else onNewChat(c.id);
  };
  const characterActions = (c, tools) => {
    const charName = (c?.name || "").trim() || "Без имени";
    return [
      ...(latestChat(c) ? [{ icon: "message-square", label: "Последний чат", onClick: () => openCharacter(c) }] : []),
      { icon: "message-square-plus", label: "Новый чат", onClick: () => onNewChat(c.id) },
      { icon: "pencil", label: "Карточка персонажа", onClick: () => onEditCharacter(c.id) },
      { icon: "download", label: "Скачать (PNG)", onClick: () => onExportCharacter(c.id) },
      { icon: "library", label: "В группу…", onClick: tools.move },
      { icon: "trash-2", label: "Удалить", danger: true, onClick: async () => {
        if (await ask(`Удалить персонажа «${charName}»?`, {
          ok: "Удалить", danger: true,
          hint: "Карточка будет удалена. Все уже начатые чаты с этим персонажем останутся в сохранности.",
        })) onDeleteCharacter(c.id);
      } },
    ];
  };

  const chatActions = (c, tools) => {
    const chatTitle = (c?.title || "").trim() || "Без названия";
    return [
      { icon: "pencil", label: "Переименовать", onClick: async () => {
        const title = ((await askText("Название чата", { value: c.title || "" })) || "").trim();
        if (title && title !== c.title) onRenameChat(c.id, title);
      } },
      { icon: "download", label: "Скачать", href: `/api/chats/${c.id}/export.jsonl` },
      { icon: "library", label: "В группу…", onClick: tools.move },
      { icon: "trash-2", label: "Удалить", danger: true, onClick: async () => {
        if (await ask(`Удалить чат «${chatTitle}»?`, {
          ok: "Удалить", danger: true,
          hint: "Все сообщения этой беседы и её веток будут удалены без возможности восстановления.",
        })) onDeleteChat(c.id);
      } },
    ];
  };

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
          <${NewButton} label="Новый чат" slot="new-chat-button" onClick=${onPickNewChat} />
        </div>
        <input ref=${chatFileInput} type="file" hidden
          onChange=${async (e) => { const file = e.target.files[0]; e.target.value = ""; if (file) await onImportChat(file); }} />
        ${chats.length === 0 && html`<${EmptyNote} icon="message-square" title="Чатов пока нет"
          hint="Выберите персонажа или начните свободную беседу.">
          <div class="empty-actions">
            <button type="button" class="primary empty-btn" onClick=${onPickNewChat}>Новый чат</button>
            <button type="button" class="empty-btn" onClick=${() => chatFileInput.current.click()}>Импорт чата</button>
          </div>
        <//>`}
        <${GroupedItems} kind="chat" items=${chats} idOf=${(c) => c.id}
          labelOf=${(c) => (c?.title || "").trim() || "Без названия"} activeId=${chatId}
          onError=${onError}
          onPress=${(c, el, tools) => openMenu(el, [
            { icon: "message-square", label: "Открыть", onClick: () => onOpenChat(c.id) }, ...chatActions(c, tools)])}
          renderRow=${(c, tools) => {
            const chatTitle = (c?.title || "").trim() || "Без названия";
            return html`
            <li key=${c.id} class=${c.id === chatId ? "active" : ""}>
              <button class="item" aria-current=${c.id === chatId ? "true" : null} onClick=${() => onOpenChat(c.id)}
                title=${chatTitle + (c?.updated_at ? "\nИзменён: " + new Date(c.updated_at).toLocaleString("ru-RU") : "")}>
                <span class="item-title">${chatTitle}</span>
                <span class="item-sub muted">${chatFacts(c)}${lastText(c) ? " · " + lastText(c) : ""}</span></button>
              <${MenuButton} label=${"Действия с чатом " + chatTitle} data-slot="chat-rename" items=${() => chatActions(c, tools)} />
            </li>`;
          }} />
      </section>

      <section class="side-section" data-name="characters" data-slot="characters">
        <div class="section-head">
          <h2>Персонажи</h2><span class="spacer"></span>
          <${NewButton} label="Новый персонаж" slot="new-character-button" onClick=${(e) => openMenu(e.currentTarget, [
            { icon: "pencil", label: "Заполнить самому", onClick: () => onEditCharacter(null) },
            { icon: "upload", label: "Загрузить карточку (PNG или JSON)", onClick: () => fileInput.current.click() },
            ...(onAskAssistant ? [{ icon: "wand", label: "Попросить помощника",
              onClick: () => onAskAssistant("Создай персонажа: ") }] : []),
          ])} />
        </div>
        <input ref=${fileInput} type="file" accept=".json,.png,application/json,image/png" hidden onChange=${pickFile} />
        ${characters.length === 0 && html`<${EmptyNote} icon="users" title="Персонажей пока нет"
          hint="Создайте персонажа или загрузите готовую карточку (PNG или JSON).">
          <div class="empty-actions">
            <button type="button" class="primary empty-btn" onClick=${() => onEditCharacter(null)}>Создать</button>
            <button type="button" class="empty-btn" onClick=${() => fileInput.current.click()}>Импорт карточки</button>
          </div>
        <//>`}
        <${GroupedItems} kind="character" items=${characters} idOf=${(c) => c.id}
          labelOf=${(c) => (c?.name || "").trim() || "Без имени"}
          onError=${onError} onPress=${(c, el, tools) => openMenu(el, characterActions(c, tools))}
          renderRow=${(c, tools) => {
            const charName = (c?.name || "").trim() || "Без имени";
            return html`
            <li key=${c.id}>
              <button class="item" title=${charName + (latestChat(c) ? " — открыть последний чат" : " — начать чат")}
                onClick=${() => openCharacter(c)}>${charName}</button>
              <${MenuButton} label=${"Действия с персонажем " + charName} items=${() => characterActions(c, tools)} />
            </li>`;
          }} />
      </section>

      <section class="side-section" data-name="lorebooks">
        <${LorebookShelf} settings=${settings} onError=${onError} onAskAssistant=${onAskAssistant} />
      </section>
    </aside>
  `;
}
