// Author's notes. NoteList edits the notes of one level (global, a character's, a chat's); Notes is
// the chat's notes window with tabs for the three levels and edits by message numbers;
// NotesByNumbers is the window over the chat that shows picked messages with their kept notes.
// Every change goes to the server at once; agents change the same things through the same API.
import { Select } from "./Select.js";
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { ROLES } from "./roles.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, EmptyNote } from "./State.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";

const MODES = [
  ["once", "Не сохраняется: только в этом запросе"],
  ["hidden", "Скрытая: в истории, в чате не видна"],
  ["visible", "Видимая: в истории и возле сообщения"],
];
const KINDS = [["visible", "Видимая"], ["hidden", "Скрытая"]];
const COLLAPSE_FROM = 8; // with more picked messages than this, they start folded

function RoleSelect({ value, onChange }) {
  return html`<${Select} value=${value} aria-label="От кого" onChange=${(e) => onChange(e.target.value)}>
    ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
  </${Select}>`;
}

function KindSelect({ value, onChange }) {
  return html`<${Select} value=${value} aria-label="Вид заметки" onChange=${(e) => onChange(e.target.value)}>
    ${KINDS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
  </${Select}>`;
}

function NumberField({ label, value, min, onChange }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const n = Number(draft);
    if (Number.isInteger(n) && n >= min && n !== value) onChange(n);
    else setDraft(String(value));
  };
  return html`<label>${label}
    <input type="number" min=${min} max="1000" value=${draft} class="num"
      onInput=${(e) => setDraft(e.target.value)} onBlur=${commit}
      onKeyDown=${(e) => e.key === "Enter" && commit()} /></label>`;
}

function NoteRow({ note, onSave, onDelete }) {
  const [text, setText] = useState(note.text);
  const [title, setTitle] = useState(note.title);
  useEffect(() => { setText(note.text); setTitle(note.title); }, [note.id, note.updated_at]);
  return html`
    <li class=${"note" + (note.enabled ? "" : " off")} data-slot="note">
      <div class="row">
        <label><input type="checkbox" checked=${note.enabled}
          onChange=${(e) => onSave({ enabled: e.target.checked })} /> Включена</label>
        <input class="grow" value=${title} placeholder="Название" aria-label="Название"
          onInput=${(e) => setTitle(e.target.value)} onBlur=${() => title !== note.title && onSave({ title })} />
        <${IconButton} icon="trash-2" label="Удалить заметку"
          onClick=${async () => { if (await ask("Удалить заметку?", { ok: "Удалить", danger: true })) onDelete(); }} />
      </div>
      <textarea rows="3" value=${text} placeholder="Текст заметки. Можно использовать {{char}} и {{user}}"
        aria-label="Текст заметки" onInput=${(e) => setText(e.target.value)}
        onBlur=${() => text !== note.text && onSave({ text })}></textarea>
      <div class="row">
        <${NumberField} label="Глубина" value=${note.depth} min=${0} onChange=${(depth) => onSave({ depth })} />
        <${NumberField} label="Пропускать ответов" value=${note.interval} min=${0}
          onChange=${(interval) => onSave({ interval })} />
        <${RoleSelect} value=${note.role} onChange=${(role) => onSave({ role })} />
        <${Select} value=${note.mode} aria-label="Вид заметки" onChange=${(e) => onSave({ mode: e.target.value })}>
          ${MODES.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
        </${Select}>
      </div>
      <p class="hint">Глубина: за сколько сообщений до конца чата вставить, 0 — в самом конце. Пропуск: сколько ответов
        пропустить между вставками, 0 — вставлять в каждый ответ.</p>
    </li>`;
}

// Notes of one level. scope: "global" | "character" | "chat"; ownerId for the last two.
export function NoteList({ scope, ownerId, initial, onError }) {
  const [notes, setNotes] = useState(initial || null); // null while loading: no «Заметок нет» flash
  const [loadError, setLoadError] = useState("");
  const load = () => {
    if (initial) { setNotes(initial); return; }
    setLoadError("");
    const query = scope === "global" ? "scope=global" : `scope=${scope}&owner_id=${encodeURIComponent(ownerId)}`;
    api.get(`/notes?${query}`).then((d) => setNotes(d.notes)).catch((e) => setLoadError(e.message));
  };
  useEffect(load, [scope, ownerId, initial]);
  if (!notes) return html`<${Pending} error=${loadError} onRetry=${load} />`;

  const add = () => api.post("/notes", { scope, owner_id: ownerId || null, text: "" })
    .then((n) => setNotes([...notes, n])).catch((e) => onError(e.message));
  const save = (note, patch) => api.put(`/notes/${note.id}`, patch)
    .then((n) => setNotes((list) => list.map((x) => (x.id === n.id ? n : x)))).catch((e) => onError(e.message));
  const remove = (note) => api.del(`/notes/${note.id}`)
    .then(() => setNotes((list) => list.filter((x) => x.id !== note.id))).catch((e) => onError(e.message));

  return html`
    <ul class="notes" data-slot="note-list">
      ${notes.length === 0 && html`<li><${EmptyNote} title="Заметок пока нет"
        hint="Заметка — текст, который модель получает вместе с историей чата." /></li>`}
      ${notes.map((n) => html`<${NoteRow} key=${n.id} note=${n} onSave=${(p) => save(n, p)} onDelete=${() => remove(n)} />`)}
    </ul>
    <button onClick=${add}><${Icon} name="plus" /> Добавить заметку</button>`;
}

const TABS = [["chat", "Чат"], ["characters", "Персонажи"], ["global", "Общие"], ["numbers", "По номерам"]];

export function Notes({ chat, showHidden, onShowHidden, onClearHidden, onEditNumbers, onClose, onChanged, onError }) {
  const [tab, setTab] = useState("chat");
  const [data, setData] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [numbers, setNumbers] = useState("");
  useEscape(onClose);

  const load = () => {
    setLoadError("");
    api.get(`/chats/${chat.id}/notes`).then(setData).catch((e) => setLoadError(e.message));
  };
  useEffect(load, [chat.id]);

  const removeByNumbers = async () => {
    if (!numbers.trim() || !await ask(`Удалить заметки у сообщений ${numbers}?`, { ok: "Удалить", danger: true })) return;
    api.post(`/chats/${chat.id}/message-notes/delete-by-numbers`, { numbers })
      .then((d) => { notify(`Удалено заметок: ${d.removed}`, "ok"); onChanged(); }).catch((e) => onError(e.message));
  };
  const clearHidden = async () => {
    if (await ask("Удалить из чата все скрытые заметки? Модель перестанет их видеть.", { ok: "Удалить", danger: true })) onClearHidden();
  };

  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Заметки автора">
        <div class="side-head"><strong>Заметки автора</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        <div class="row">
          <label><input type="checkbox" checked=${showHidden} onChange=${(e) => onShowHidden(e.target.checked)} />
            показать все скрытые заметки</label>
          <button onClick=${clearHidden}>Удалить все скрытые</button>
        </div>
        <div class="tabs" role="tablist">
          ${TABS.map(([id, label]) => html`<button key=${id} role="tab" aria-selected=${tab === id}
            onClick=${() => setTab(id)}>${label}</button>`)}
        </div>
        ${!data && tab !== "numbers" && html`<${Pending} error=${loadError} onRetry=${load} />`}
        ${data && tab === "chat" && html`
          <p class="muted">Работают только в этом чате.</p>
          <${NoteList} scope="chat" ownerId=${chat.id} initial=${data.chat} onError=${onError} />`}
        ${data && tab === "characters" && html`
          <p class="muted">Заметка персонажа работает во всех чатах, где он участвует.</p>
          ${data.characters.length === 0 && html`<p class="muted">В чате нет персонажей.</p>`}
          ${data.characters.map((c) => html`<section key=${c.character_id} class="note-owner">
            <h3>${c.name}${c.is_me ? " (это я)" : ""}</h3>
            <${NoteList} scope="character" ownerId=${c.character_id} initial=${c.notes} onError=${onError} />
          </section>`)}`}
        ${data && tab === "global" && html`
          <p class="muted">Работают во всех чатах. Их же можно править в настройках, во вкладке «Промпт».</p>
          <${NoteList} scope="global" initial=${data.global} onError=${onError} />`}
        ${tab === "numbers" && html`
          <p class="muted">Заметки у сообщений по номерам, например «1-52 68-72» или «75 85».</p>
          <div class="row">
            <input class="grow" value=${numbers} placeholder="Номера: 1-52 68-72" aria-label="Номера сообщений"
              onInput=${(e) => setNumbers(e.target.value)}
              onKeyDown=${(e) => e.key === "Enter" && numbers.trim() && onEditNumbers(numbers)} />
            <button class="primary" disabled=${!numbers.trim()} onClick=${() => onEditNumbers(numbers)}>Изменить</button>
            <button disabled=${!numbers.trim()} onClick=${removeByNumbers}>Удалить</button>
          </div>`}
      </div>
    </div>`;
}

function AttachedNote({ note, onChanged, onError }) {
  // Saved as you go: the text when the field loses focus, the role and kind at once.
  const [text, setText] = useState(note.text);
  useEffect(() => setText(note.text), [note.id, note.text]);
  const save = (patch) => api.put(`/message-notes/${note.id}`, patch).then(onChanged).catch((e) => onError(e.message));
  const remove = async () => {
    if (await ask("Удалить заметку?", { ok: "Удалить", danger: true })) {
      api.del(`/message-notes/${note.id}`).then(onChanged).catch((e) => onError(e.message));
    }
  };
  return html`<div class=${"attached-note " + note.kind} data-slot="attached-note">
    <textarea rows="2" value=${text} aria-label="Заметка" onInput=${(e) => setText(e.target.value)}
      onBlur=${() => text !== note.text && save({ text })}></textarea>
    <div class="row">
      <${RoleSelect} value=${note.role} onChange=${(role) => save({ role })} />
      <${KindSelect} value=${note.kind} onChange=${(kind) => save({ kind })} />
      ${note.title && html`<span class="muted">${note.title}</span>`}
      <span class="spacer"></span>
      <${IconButton} icon="trash-2" label="Удалить заметку" onClick=${remove} />
    </div>
  </div>`;
}

function NewNote({ messageId, chatId, hasNotes, onChanged, onError }) {
  const [text, setText] = useState("");
  const [role, setRole] = useState("system");
  const [kind, setKind] = useState("visible");
  const add = () => api.post(`/chats/${chatId}/message-notes`, { message_id: messageId, text, role, kind })
    .then(() => { setText(""); onChanged(); }).catch((e) => onError(e.message));
  return html`<div class="attached-note new">
    <textarea rows="2" value=${text}
      placeholder=${hasNotes ? "Ещё одна заметка" : "Новая заметка"}
      aria-label="Новая заметка" onInput=${(e) => setText(e.target.value)}></textarea>
    ${text.trim() && html`<div class="row"><${RoleSelect} value=${role} onChange=${setRole} />
      <${KindSelect} value=${kind} onChange=${setKind} />
      <button class="primary" onClick=${add}>Добавить</button></div>`}
  </div>`;
}

// The window over the chat: picked messages, each with its saved notes or "нет заметки".
export function NotesByNumbers({ chatId, numbers, onClose, onChanged, onError }) {
  const [items, setItems] = useState(null);
  const [common, setCommon] = useState("");
  const [commonRole, setCommonRole] = useState("system");
  const [commonKind, setCommonKind] = useState("visible");
  const load = () => api.post(`/chats/${chatId}/message-notes/by-numbers`, { numbers })
    .then((d) => setItems(d.items)).catch((e) => { onError(e.message); onClose(); });
  useEscape(onClose);
  useEffect(() => { load(); }, [chatId, numbers]);
  const changed = () => { load(); onChanged(); };

  const addToAll = () => api.post(`/chats/${chatId}/message-notes/add-by-numbers`,
    { numbers, text: common, role: commonRole, kind: commonKind })
    .then(() => { setCommon(""); changed(); }).catch((e) => onError(e.message));
  const kindForAll = (kind) => api.post(`/chats/${chatId}/message-notes/kind-by-numbers`, { numbers, kind })
    .then(changed).catch((e) => onError(e.message));

  const folded = items && items.length > COLLAPSE_FROM;
  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog wide" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Заметки по номерам">
        <div class="side-head"><strong>Заметки у сообщений ${numbers}</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        ${!items && html`<${Pending} />`}
        ${items && html`
          <details class="common-note">
            <summary>Одна заметка на все выбранные (${items.length})</summary>
            <textarea rows="3" value=${common} aria-label="Общая заметка" onInput=${(e) => setCommon(e.target.value)}></textarea>
            <div class="row"><${RoleSelect} value=${commonRole} onChange=${setCommonRole} />
              <${KindSelect} value=${commonKind} onChange=${setCommonKind} />
              <button class="primary" disabled=${!common.trim()} onClick=${addToAll}>Добавить всем</button></div>
          </details>
          <div class="row">
            <button onClick=${() => kindForAll("visible")}>Все заметки видимыми</button>
            <button onClick=${() => kindForAll("hidden")}>Все заметки скрытыми</button>
          </div>
          <ul class="numbered">
            ${items.map((item) => html`<li key=${item.message.id} data-slot="numbered-message">
              <details open=${!folded}>
                <summary>
                  <span class="msg-num">#${item.number}</span>
                  <strong>${item.message.name || item.message.role}</strong>
                  <span class="muted"> ${item.message.content.slice(0, 80)}</span>
                  ${item.notes.length ? html`<span class="badge"><${Icon} name="notebook-pen" /> ${item.notes.length}</span>`
                    : html`<span class="badge muted">нет заметки</span>`}
                </summary>
                <div class="body">${item.message.content}</div>
                ${item.notes.map((n) => html`<${AttachedNote} key=${n.id + n.text} note=${n} onChanged=${changed} onError=${onError} />`)}
                <${NewNote} messageId=${item.message.id} chatId=${chatId} hasNotes=${item.notes.length > 0}
                  onChanged=${changed} onError=${onError} />
              </details>
            </li>`)}
          </ul>`}
      </div>
    </div>`;
}

// The drop-down of one message (its notes button or "ещё N"): every note of it, to read, switch, delete or add.
export function MessageNotes({ chatId, message, shown, onShown, onClose, onChanged, onError }) {
  const [notes, setNotes] = useState(null);
  const number = String(message.depth + 1);
  const load = () => api.post(`/chats/${chatId}/message-notes/by-numbers`, { numbers: number })
    .then((d) => setNotes(d.items[0] ? d.items[0].notes : [])).catch((e) => { onError(e.message); onClose(); });
  useEffect(() => { load(); }, [chatId, message.id]);
  const changed = () => { load(); onChanged(); };
  const kindForAll = (kind) => api.post(`/chats/${chatId}/message-notes/kind-by-numbers`, { numbers: number, kind })
    .then(changed).catch((e) => onError(e.message));
  useEscape(onClose);

  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Заметки сообщения">
        <div class="side-head"><strong>Заметки сообщения #${number}</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        <p class="muted message-preview">${message.content.slice(0, 200)}</p>
        ${!notes && html`<${Pending} />`}
        ${notes && html`
          <div class="row">
            <label><input type="checkbox" checked=${shown} onChange=${(e) => onShown(e.target.checked)} />
              показывать все заметки возле сообщения, и скрытые тоже</label>
          </div>
          ${notes.length > 1 && html`<div class="row">
            <button onClick=${() => kindForAll("visible")}>Все видимыми</button>
            <button onClick=${() => kindForAll("hidden")}>Все скрытыми</button>
          </div>`}
          ${notes.map((n) => html`<${AttachedNote} key=${n.id} note=${n} onChanged=${changed} onError=${onError} />`)}
          <${NewNote} messageId=${message.id} chatId=${chatId} hasNotes=${notes.length > 0}
            onChanged=${changed} onError=${onError} />`}
      </div>
    </div>`;
}
