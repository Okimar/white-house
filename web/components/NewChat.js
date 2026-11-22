// Starting and finding conversations, one way everywhere (owner's decision 2026-11-22, docs/UI_MAP.md §9а, 5–6):
// «+ Новый чат» asks «с кем» (NewChatPicker): a tap on a name opens the new chat with the greeting; «без персонажа»
// and a chat file are at the end. The home screen lists recent chats (RecentChats) and the chat menu lists the
// chats with its character (CharacterChats). A chat line tells chats with one title apart: date, length, last text.
import { html, useState, useRef } from "../vendor/preact-htm.mjs";
import { Icon, IconButton } from "./Icon.js";
import { backdrop, useEscape } from "../core/keys.js";

// «19 нояб., 00:06»: chats of one character share the name, the time tells them apart
export function shortDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleString("ru-RU", { day: "numeric", month: "short", ...(sameYear ? {} : { year: "numeric" }),
    hour: "2-digit", minute: "2-digit" });
}

// «22 нояб., 03:17 · 52 сообщ.» (the list gives `messages` and `last`, see ChatRepo.list_chats)
export function chatFacts(c) {
  return [c.updated_at ? shortDate(c.updated_at) : "", c.messages ? `${c.messages} сообщ.` : "пусто"]
    .filter(Boolean).join(" · ");
}

// «+ Новый …»: a quiet worded button at the top of a list (the same in all three lists and on the home screen)
export function NewButton({ label, onClick, slot }) {
  return html`<button type="button" class="section-new" data-slot=${slot} onClick=${onClick}>
    <${Icon} name="plus" /> ${label}</button>`;
}

export const lastText =(c) => (c.last && c.last.text) || "";
export const chatTitle = (c) => (c?.title || "").trim() || "Без названия";

function Avatar({ character, size = "" }) {
  const [failed, setFailed] = useState(false);
  if (!character || !character.avatar || failed) {
    return html`<span class=${"chat-avatar no-photo " + size} aria-hidden="true">${character
      ? ((character.name || "?").trim()[0] || "?").toUpperCase() : html`<${Icon} name="message-square" />`}</span>`;
  }
  return html`<img class=${"chat-avatar " + size} src=${`/api/characters/${character.id}/avatar`} alt=""
    loading="lazy" onError=${() => setFailed(true)} />`;
}

// One chat as a two-line row: avatar, title and facts; the start of the last message under them.
export function ChatLine({ chat, character, active, onOpen }) {
  const text = lastText(chat);
  return html`<button type="button" class=${"chat-line" + (active ? " active" : "")} data-slot="chat-line"
    aria-current=${active ? "true" : null} title=${chatTitle(chat)} onClick=${() => onOpen(chat.id)}>
    <${Avatar} character=${character} />
    <span class="chat-line-body">
      <span class="chat-line-head"><span class="chat-line-title">${chatTitle(chat)}</span>
        <span class="chat-line-facts muted">${chatFacts(chat)}</span></span>
      <span class="chat-line-text muted">${text || "Сообщений пока нет"}</span>
    </span></button>`;
}

// «Новый чат с…»: every character (search when many), then «без персонажа» and «загрузить файл чата».
export function NewChatPicker({ characters, chats, onPick, onImportFile, onClose }) {
  const [query, setQuery] = useState("");
  const file = useRef(null);
  useEscape(onClose);
  const q = query.trim().toLowerCase();
  const found = characters.filter((c) => (c.name || "").toLowerCase().includes(q));
  const count = (id) => chats.filter((c) => c.character_id === id).length;
  return html`<div class="overlay ask-overlay" ...${backdrop(onClose)}>
    <div class="dialog stack group-picker new-chat" data-slot="new-chat" role="dialog" aria-modal="true" aria-label="Новый чат">
      <div class="side-head"><strong class="dialog-name">Новый чат с…</strong><span class="spacer"></span>
        <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
      ${characters.length > 6 && html`<input type="search" value=${query} placeholder="Поиск по имени"
        aria-label="Поиск персонажа" class="group-picker-search" onInput=${(e) => setQuery(e.target.value)} />`}
      <ul class="list">
        ${found.map((c) => html`<li key=${c.id}><button type="button" class="item pick-character" title=${c.name}
          onClick=${() => onPick(c.id)}><${Avatar} character=${c} />
          <span class="pick-name">${c.name}</span>
          ${count(c.id) > 0 && html`<span class="muted pick-count">чатов: ${count(c.id)}</span>`}</button></li>`)}
        ${q && found.length === 0 && html`<li class="muted pick-empty">По запросу «${query}» никого нет</li>`}
        <li class="pick-divider" aria-hidden="true"></li>
        <li><button type="button" class="item" onClick=${() => onPick(null)}>
          <${Avatar} character=${null} /><span class="pick-name">Без персонажа</span></button></li>
        <li><button type="button" class="item" onClick=${() => file.current.click()}>
          <span class="chat-avatar no-photo" aria-hidden="true"><${Icon} name="upload" /></span>
          <span class="pick-name">Загрузить файл чата</span></button></li>
      </ul>
      <input ref=${file} type="file" hidden
        onChange=${(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) onImportFile(f); }} />
    </div></div>`;
}

// The chats with one character, newest first, and «+ Новый чат с …» on top.
export function CharacterChats({ character, chats, currentId, onOpen, onNew, onClose }) {
  useEscape(onClose);
  const own = chats.filter((c) => c.character_id === character.id);
  return html`<div class="overlay ask-overlay" ...${backdrop(onClose)}>
    <div class="dialog stack group-picker new-chat" data-slot="character-chats" role="dialog" aria-modal="true"
      aria-label=${"Чаты: " + character.name}>
      <div class="side-head"><strong class="dialog-name" title=${character.name}>Чаты: ${character.name}</strong>
        <span class="spacer"></span><${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
      <button type="button" class="section-new" onClick=${onNew}><${Icon} name="plus" /> Новый чат</button>
      <div class="chat-lines">
        ${own.map((c) => html`<${ChatLine} key=${c.id} chat=${c} character=${character} active=${c.id === currentId}
          onOpen=${onOpen} />`)}
      </div>
    </div></div>`;
}

// Home: the recent chats instead of «Выберите чат».
export function RecentChats({ chats, characters, onOpen, onNew }) {
  const byId = new Map(characters.map((c) => [c.id, c]));
  return html`<section class="recent-chats" data-slot="recent-chats" aria-label="Недавние чаты">
    <div class="recent-head"><h2>Недавние</h2><span class="spacer"></span>
      <button type="button" class="section-new" onClick=${onNew}><${Icon} name="plus" /> Новый чат</button></div>
    <div class="chat-lines">
      ${chats.slice(0, 30).map((c) => html`<${ChatLine} key=${c.id} chat=${c} character=${byId.get(c.character_id)}
        onOpen=${onOpen} />`)}
    </div></section>`;
}
