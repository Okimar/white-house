// One chat message: number, role, text, swipe arrows and its actions. Text is rendered as plain text
// (never as HTML), so a model or a card cannot inject markup.
// The role icon shows who the model takes the message from; pressing it opens a role menu.
// Actions: by default in a "…" list at the side; the setting message_actions = "always" keeps them all on the
// panel, and message_pinned lists those kept on the panel in any mode (core/messageActions.js).
// Author's notes kept after the message hang beside it: visible ones on the right (folded once
// AUTO_FOLD messages came after it), hidden ones on the left only when asked for. With the setting
// notes_display = "few" a column shows its last notes_shown notes (default 2, edited on the line under them)
// and the line «Видны последние [2] из 5 · Все заметки»; the notes button and that button open the
// drop-down with every note of the message.
// With spending shown under messages (`spend` = the spending settings), a small line says what this
// version cost ("≈" = the service gave no counts, or the reply was stopped).
// Regex rules (stage 11): `shown` is how the rules make the text look on screen (core/regex.js); the saved text is untouched.
// Plugins add buttons to the header and plain-text lines under the text (core/plugins.js).
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { MAX_SHOWN, setNotesShown, useNotesShown } from "../core/notesShown.js";
import { ROLES, roleOf } from "./roles.js";
import { usageLine } from "../core/spending.js";
import { messageDividers, messageExtras, messageMenuItems, messageTrees, usePluginUi } from "../core/plugins.js";
import { DEFAULT_PINNED, MESSAGE_ACTIONS, splitActions } from "../core/messageActions.js";
import { MessageTree } from "./PluginTree.js";
import { Icon, IconButton } from "./Icon.js";
import { MenuButton, openMenu } from "./Menu.js";
import { ask } from "./Dialogs.js";
import { RegexNodes } from "./Regex.js";

function usageTitle(u) {
  return `Запрос: ${u.prompt_tokens} токенов, ответ: ${u.completion_tokens}` +
    (u.requests > 1 ? `, запросов: ${u.requests}` : "") + (u.estimated ? ". Примерно: сервис не прислал счёт" : "") +
    (u.unpriced ? ". Цена модели неизвестна — укажите свою в настройках «Расход»" : "");
}

const AUTO_FOLD = 3;

// «Видны последние [2] из 5 · Все заметки»: the number is edited right here (and in Settings), it is the same
// setting for every message.
function NotesShownInput({ value }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const n = Number(draft);
    if (Number.isInteger(n) && n >= 1 && n <= MAX_SHOWN) { if (n !== value) setNotesShown(n).catch(() => {}); }
    else setDraft(String(value));
  };
  return html`<input class="num" type="number" min="1" max=${MAX_SHOWN} inputmode="numeric" value=${draft}
    aria-label="Сколько последних заметок показывать" onInput=${(e) => setDraft(e.target.value)}
    onBlur=${commit} onKeyDown=${(e) => e.key === "Enter" && e.target.blur()} />`;
}

function NoteColumn({ notes, side, few, onHide, onMore }) {
  const limit = useNotesShown();
  const shown = few && notes.length > limit ? notes.slice(-limit) : notes;
  return html`<aside class=${"msg-notes " + side}
    data-slot=${side === "left" ? "message-notes-hidden" : "message-notes-visible"}>
    ${shown.map((n) => html`<${NoteCard} key=${n.id} note=${n} onHide=${onHide} />`)}
    ${shown.length < notes.length && html`<div class="notes-line" data-slot="notes-more">
      <span>Видны последние</span><${NotesShownInput} value=${limit} /><span>из ${notes.length}.</span>
      <button class="notes-more" onClick=${onMore}>Все заметки</button></div>`}
  </aside>`;
}

function NoteCard({ note, onHide }) {
  return html`<div class=${"msg-note " + note.kind} data-slot="message-note" data-kind=${note.kind}
    title=${note.kind === "hidden" ? "Скрытая заметка: модель видит её, в чате она скрыта"
      : "Видимая заметка: модель видит её после этого сообщения"}>
    <span class="msg-note-head"><${Icon} name=${note.kind === "hidden" ? "eye-off" : "notebook-pen"} />
      ${note.title && html`<strong>${note.title}</strong>`}
      <span class="spacer"></span>
      ${onHide && html`<${IconButton} icon="chevron-down" label="Свернуть заметки сообщения" onClick=${onHide} />`}</span>
    ${note.text}</div>`;
}

export function Message({ message, notes = [], showHidden, notesOpen, notesDisplay = "few", age = 0, avatar,
  streamText, streamThinking, busy, isLast, found, selecting, selected, onSelect, onRole, onEdit, onSwipe, onRemove, onRegenerate,
  onBranches, onPrompt, onExclude, onNotesOpen, onOpenNotes, spend, thinkingDisplay = "folded",
  actionsMode = "menu", pinned = DEFAULT_PINNED, shown = null, onRegexUndo }) {
  const plugins = usePluginUi(); // buttons and lines added by plugins (core/plugins.js)
  const [avatarFailed, setAvatarFailed] = useState(false);
  const [editing, setEditing] = useState(null); // draft text while editing
  const [saving, setSaving] = useState(false);
  const streaming = streamText !== null && streamText !== undefined;
  const text = streaming ? streamText : message.content;
  const meta = message.swipe_meta || {};
  const thinking = streaming ? streamThinking : meta.reasoning;
  const role = roleOf(message.role);
  const canRegenerate = isLast && message.role !== "user" && !busy;
  const fallbackName = { user: "Вы", assistant: "Персонаж", system: "Система" }[message.role];

  // The draft stays open until the server has it: a failed save must not lose the text.
  const save = async () => {
    if (editing === message.content) { setEditing(null); return; }
    setSaving(true);
    const ok = await onEdit(message.id, editing);
    setSaving(false);
    if (ok !== false) setEditing(null);
  };

  // Leaving with unsaved changes asks once.
  const cancel = async () => {
    if (saving) return;
    if (editing !== message.content && !(await ask("Выйти без сохранения? Изменения пропадут", { ok: "Выйти", danger: true }))) return;
    setEditing(null);
  };
  // The field is as tall as its text, so the chat scrolls instead of the field.
  const grow = (el) => { if (el) { el.style.height = "auto"; el.style.height = el.scrollHeight + "px"; } };
  const editRef = useRef(null);
  const isEditing = editing !== null;
  useEffect(() => {
    const el = editRef.current;
    if (!isEditing || !el) return;
    grow(el);
    el.focus({ preventScroll: true });
    el.setSelectionRange(el.value.length, el.value.length);
  }, [isEditing]);

  const pickRole = (button) => openMenu(button, ROLES.map((r) => ({
    icon: r.icon, label: r.label + (r.id === message.role ? " (сейчас)" : ""), disabled: r.id === message.role,
    onClick: () => onRole(r.id, { ids: [message.id] }),
  })));

  // notesOpen: true / false once the user pressed the notes button or fold here; undefined = automatic.
  const hidden = notes.filter((n) => n.kind === "hidden");
  const visible = notes.filter((n) => n.kind === "visible");
  const showVisible = notesOpen === true || (notesOpen === undefined && age < AUTO_FOLD);
  const showHiddenHere = notesOpen === true || (notesOpen !== false && showHidden);
  const anyShown = (showVisible && visible.length > 0) || (showHiddenHere && hidden.length > 0);
  const hide = () => onNotesOpen(message.id, false);
  const more = () => onOpenNotes(message, notesOpen === true);
  const few = notesDisplay !== "all";

  // Reading tools (what the model saw, notes) work during a reply; those that change the chat wait for it.
  const excluded = Boolean(message.exclude_from_prompt);
  const actions = {
    edit: { disabled: busy, onClick: () => setEditing(message.content) },
    regenerate: { onClick: onRegenerate },
    branches: { disabled: busy, onClick: () => onBranches(message.id) },
    prompt: { onClick: () => onPrompt(message) },
    exclude: {
      disabled: busy, label: excluded ? "Вернуть модели" : "Скрыть от модели", pressed: excluded,
      onClick: () => onExclude(message.id, !excluded),
    },
    notes: { label: notes.length ? `Заметки: ${notes.length}` : "Написать заметку", pressed: anyShown, count: notes.length, onClick: more },
    delete: {
      disabled: busy, danger: true,
      onClick: async () => {
        if (await ask("Удалить это сообщение? Остальные останутся", { ok: "Удалить", danger: true, skip: "message" })) onRemove(message.id);
      },
    },
  };
  const available = MESSAGE_ACTIONS.map((a) => a.id).filter((id) => id !== "regenerate" || canRegenerate);
  const status = [excluded && "exclude", notes.length > 0 && "notes"].filter(Boolean);
  const { bar, menu } = splitActions(available, { mode: actionsMode, pinned, status });
  const describe = (id) => ({ ...MESSAGE_ACTIONS.find((a) => a.id === id), ...actions[id] });
  const pluginButtons = streaming ? [] : plugins.messageButtons;
  const titleOf = (b) => (typeof b.title === "function" ? b.title(message) : b.title);
  const pluginMenu = streaming ? [] : messageMenuItems(message);
  const menuItems = () => [
    ...menu.map(describe),
    ...pluginMenu.map((m) => ({ icon: m.icon || "", emoji: true, label: m.label, onClick: () => m.onClick(message) })),
    ...(actionsMode === "always" ? [] : pluginButtons.map((b) => ({
      icon: b.icon, emoji: true, label: titleOf(b), onClick: () => b.onClick(message),
    }))),
  ];

  const dividers = streaming ? [] : messageDividers(message);
  const row = html`<div class="msg-row" data-slot="message-row">
    ${showHiddenHere && hidden.length > 0 && html`<${NoteColumn} notes=${hidden} side="left" few=${few}
      onHide=${hide} onMore=${more} />`}
    <article class=${"msg msg-" + message.role + (selected ? " msg-selected" : "") + (found ? " msg-found" : "") + (excluded ? " msg-excluded" : "")}
      data-slot="message" data-role=${message.role} data-streaming=${streaming ? "true" : "false"}
      data-excluded=${excluded ? "true" : "false"}>
      <header data-slot="message-header">
        ${selecting && html`<input type="checkbox" checked=${selected} disabled=${busy}
          aria-label=${"Выбрать сообщение " + (message.depth + 1)} onChange=${() => onSelect(message.id)} />`}
        <span class="msg-num" data-slot="message-number" title="Номер сообщения">${message.depth + 1}</span>
        ${avatar && !avatarFailed && html`<img class="msg-avatar" data-slot="message-avatar" src=${avatar}
          alt="" loading="lazy" onError=${() => setAvatarFailed(true)} />`}
        <${IconButton} icon=${role.icon} class="role-icon" disabled=${busy} aria-haspopup="menu"
          label=${"Модель видит это как сообщение от: " + role.label + ". Нажмите, чтобы сменить"}
          onClick=${(e) => pickRole(e.currentTarget)} />
        <strong class="msg-name" data-slot="message-name">${message.name || fallbackName}</strong>
        ${message.swipe_count > 1 && html`
          <span class="swipes" data-slot="message-swipes">
            <${IconButton} icon="chevron-left" label="Предыдущий вариант" disabled=${busy || message.active_swipe === 0}
              onClick=${() => onSwipe(message.id, message.active_swipe - 1)} />
            <span class="swipe-count">${message.active_swipe + 1} / ${message.swipe_count}</span>
            <${IconButton} icon="chevron-right" label="Следующий вариант" disabled=${busy || message.active_swipe >= message.swipe_count - 1}
              onClick=${() => onSwipe(message.id, message.active_swipe + 1)} />
          </span>`}
        <span class="spacer"></span>
        ${editing === null && !streaming && html`<span class="msg-actions" data-slot="message-actions">
          ${bar.map((id) => {
            const a = describe(id);
            return html`<${IconButton} key=${id} icon=${a.icon} label=${a.label} disabled=${a.disabled}
              class=${id === "notes" ? "notes-toggle" + (notes.length ? " has" : "") : ""}
              data-slot=${id === "exclude" ? "message-exclude" : null} aria-pressed=${a.pressed}
              onClick=${a.onClick}>${a.count ? html`<small>${a.count}</small>` : null}<//>`;
          })}
          ${actionsMode === "always" && pluginButtons.map((b, i) => html`<button key=${b.owner + i} type="button"
            class="icon-btn" data-slot="message-plugin-button" data-plugin=${b.owner} title=${titleOf(b)}
            aria-label=${titleOf(b)} onClick=${() => b.onClick(message)}>${b.icon}</button>`)}
          ${(menu.length > 0 || pluginMenu.length > 0 || (actionsMode !== "always" && pluginButtons.length > 0)) && html`<${MenuButton}
            label="Действия с сообщением" data-slot="message-menu" items=${menuItems} />`}
        </span>`}
      </header>
      ${editing !== null
        ? html`
          <textarea class="body edit-inline" ref=${editRef} value=${editing} rows="1" spellcheck="false"
            aria-label="Текст сообщения" disabled=${saving}
            onInput=${(e) => { setEditing(e.target.value); grow(e.target); }}
            onKeyDown=${(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
              else if (e.key === "Escape") { e.preventDefault(); cancel(); }
            }}></textarea>
          <div class="edit-bar" data-slot="message-edit-bar">
            <button class="primary" disabled=${saving} onClick=${save}>Сохранить</button>
            <button disabled=${saving} onClick=${cancel}>Отмена</button>
          </div>`
        : html`${thinking && thinkingDisplay !== "hidden" && html`<details class="thinking" data-slot="message-thinking"
            open=${thinkingDisplay === "open" || (streaming && !text)}>
            <summary><${Icon} name="brain" /> Размышления${streaming && !text ? "…" : ""}</summary><div class="thinking-text">${thinking}</div>
          </details>`}
          ${messageTrees(message).map((t, i) => html`<${MessageTree} key=${t.owner + i} owner=${t.owner} tree=${t.tree} />`)}
          ${(meta.images || []).length > 0 && html`<div class="msg-images" data-slot="message-images">
            ${meta.images.map((name) => html`<a key=${name} href=${"/assets/" + encodeURIComponent(name)} target="_blank"
              rel="noopener"><img src=${"/assets/" + encodeURIComponent(name)} alt="картинка" loading="lazy" /></a>`)}
          </div>`}
          ${meta.via && !streaming && html`<div class="msg-via muted" data-slot="message-via"
            title="Основное подключение не ответило, ответил запасной путь"><${Icon} name="corner-down-right" /> через ${String(meta.via).replaceAll("↪", "→")}</div>`}
          ${excluded && html`<div class="msg-excluded-note muted" data-slot="message-excluded"><${Icon} name="eye-off" /> Скрыто от модели</div>`}
          <div class=${"body" + (shown && shown.pending ? " regex-pending" : "")} data-slot="message-body">${shown && shown.nodes
            ? html`<${RegexNodes} nodes=${shown.nodes} />` : shown && !shown.pending ? shown.text : text}${streaming && html`<span class="caret" aria-hidden="true"></span>`}</div>
          ${typeof meta.regex_before === "string" && !streaming && onRegexUndo && html`<div class="msg-regex-note muted" data-slot="message-regex-note">
            Текст изменён регекс-правилом. <button type="button" class="link" onClick=${() => onRegexUndo(message.id)}>Вернуть как было</button></div>`}
          ${!streaming && messageExtras(message).map((line, i) => html`<div key=${line.owner + i} class="msg-plugin"
            data-slot="message-plugin" data-plugin=${line.owner}>${line.text}</div>`)}
          ${spend && spend.show.message && meta.usage && !streaming && html`<div class="msg-usage muted"
            data-slot="message-usage" title=${usageTitle(meta.usage)}>${usageLine(meta.usage, spend)}</div>`}`}
    </article>
    ${showVisible && visible.length > 0 && html`<${NoteColumn} notes=${visible} side="right" few=${few}
      onHide=${hide} onMore=${more} />`}
  </div>`;
  if (dividers.length === 0) return row;
  return html`<div class="msg-block" data-slot="message-block">
    ${dividers.map((d, i) => html`<button key=${d.owner + i} type="button" class=${"msg-divider" + (d.state ? " " + d.state : "")}
      data-slot="message-divider" data-plugin=${d.owner} title=${d.title || ""} onClick=${d.onClick || null}>
      <span class="msg-divider-label">${d.label}</span></button>`)}
    ${row}</div>`;
}
