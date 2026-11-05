// Lorebooks (stage 5): sets of bubbles that belong to no character.
// - LorebookShelf: the "Лорбуки" part of the left panel (list, new, import, open, download, delete);
//   it opens LorebookEditor itself. Props: settings, onError, showHeading (default true: the «Лорбуки»
//   title with the buttons; false: the buttons alone, for a panel that has its own title).
// - LorebookEditor: name, description, undo / history, and the book's bubble map (the
//   same CharacterTree as a character's, rooted at the book).
// - BookPicker: which books are switched on for one place (global, a chat, a character); saves at once.
// - LorebookSettings: Settings → "Лорбуки": global books, budget, chain steps.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { CharacterTree } from "./CharacterTree.js";
import { HistoryPanel } from "./CharacterEditor.js";
import { Icon, IconButton } from "./Icon.js";
import { MenuButton } from "./Menu.js";
import { EmptyNote, Pending, useLoad } from "./State.js";
import { ask, askText } from "./Dialogs.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";

// Where a book is switched on, for the shelf row and the book window.
const whereOf = (b) => [b.global ? "везде" : "", b.chats ? `чатов: ${b.chats}` : "", b.characters ? `персонажей: ${b.characters}` : ""]
  .filter(Boolean).join(", ") || "не подключён";

export function BookPicker({ scope, target = null, title, hint, onError }) {
  const query = `/lorebook-uses?scope=${scope}` + (target ? `&target=${encodeURIComponent(target)}` : "");
  const { data, error, reload } = useLoad(() => Promise.all([api.get("/lorebooks"), api.get(query)]), [scope, target]);
  const [on, setOn] = useState([]);
  useEffect(() => { if (data) setOn(data[1].lorebook_ids); }, [data]);
  const books = data ? data[0].lorebooks : null;
  const toggle = async (id, wanted) => {
    const before = on;
    const next = wanted ? [...on, id] : on.filter((x) => x !== id);
    setOn(next);
    try {
      setOn((await api.put("/lorebook-uses", { scope, target, lorebook_ids: next })).lorebook_ids);
    } catch (e) { setOn(before); onError(e.message); }
  };
  return html`<fieldset class="book-picker" data-slot="book-picker">
    <legend>${title}</legend>
    ${books === null && html`<${Pending} error=${error} onRetry=${reload} />`}
    ${books && books.length === 0 && html`<span class="muted">Лорбуков пока нет: создайте их в разделе «Лорбуки» слева</span>`}
    ${books && books.map((b) => html`<label key=${b.id}>
      <input type="checkbox" checked=${on.includes(b.id)} onChange=${(e) => toggle(b.id, e.target.checked)} />
      ${b.name} <span class="muted">(${b.blocks})</span></label>`)}
    ${hint && html`<span class="hint">${hint}</span>`}
  </fieldset>`;
}

export function LorebookEditor({ bookId, settings, connected = "", onClose, onChanged, onError }) {
  const [book, setBook] = useState(null);
  const [revision, setRevision] = useState(0); // bumps after undo / restore: everything reloads
  const [showHistory, setShowHistory] = useState(false);
  const base = `/lorebooks/${bookId}`;

  useEffect(() => {
    api.get(base).then(setBook).catch((e) => { onError(e.message); onClose(); });
  }, [bookId, revision]);
  const undo = async () => {
    try {
      const { undone } = await api.post(`${base}/undo`);
      if (undone) notify(`Отменено: ${undone.what}`, "ok"); else notify("Отменять больше нечего", "info");
      setRevision((r) => r + 1);
      onChanged();
    } catch (e) { onError(e.message); }
  };
  const close = () => {
    // a best-effort note that the version ends; closing must not fail because of it
    api.post(`${base}/history/close`).catch(() => {});
    onClose();
  };
  useEscape(close);
  useEffect(() => {
    const key = (e) => {
      const typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable]");
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [bookId]);
  // Name and description save when the field is left (like the bubbles, no "Сохранить" button).
  const saveField = async (key, value) => {
    if (!book || book[key] === value) return;
    if (key === "name" && !value.trim()) { onError("У лорбука должно быть название"); return; }
    try {
      setBook(await api.put(base, { [key]: value }));
      notify("Сохранено", "ok");
      onChanged();
    } catch (e) { onError(e.message); }
  };

  return html`
    <div class="overlay" ...${backdrop(close)}>
      <div class="dialog stack tree-dialog" data-slot="dialog" role="dialog" aria-modal="true"
        aria-label=${book ? book.name : "Лорбук"}>
        <div class="side-head"><${Icon} name="book-open" /><strong>${book ? book.name : "Лорбук"}</strong>
          <span class="spacer"></span>
          <${IconButton} icon="undo-2" label="Отменить последнее изменение"
            title="Отменить последнее изменение лорбука или его пузырей (Ctrl+Z)" onClick=${undo} />
          <${IconButton} icon="history" label="История версий" aria-pressed=${showHistory}
            onClick=${() => setShowHistory(!showHistory)} />
          <a class="button icon-btn" href=${`/api${base}/export.json`} aria-label="Скачать лорбук"
            title="Скачать файл лорбука (подходит и для SillyTavern)"><${Icon} name="download" /></a>
          <${IconButton} icon="x" label="Закрыть" onClick=${close} /></div>
        ${showHistory && html`<${HistoryPanel} base=${base} revision=${revision}
          onRestored=${(text) => { notify(text, "ok"); setRevision((r) => r + 1); onChanged(); }} onError=${onError} />`}
        ${book === null && html`<${Pending} />`}
        ${book && html`<div class="row book-head" data-slot="book-head">
          <label class="grow">Название
            <input key=${"n" + revision} value=${book.name} onBlur=${(e) => saveField("name", e.target.value.trim())} /></label>
          <label class="grow">Описание
            <span class="hint">Для себя, модели не уходит</span>
            <input key=${"d" + revision} value=${book.description} onBlur=${(e) => saveField("description", e.target.value)} /></label>
        </div>`}
        ${book && connected && html`<p class="hint">Где подключён: ${connected}</p>`}
        ${book && html`<${CharacterTree} character=${{ id: bookId, name: book.name, book: true }}
          settings=${settings} revision=${revision} onError=${onError} />`}
      </div>
    </div>`;
}

export function LorebookShelf({ settings, onError, showHeading = true }) {
  const { data, error, reload } = useLoad(() => api.get("/lorebooks"), []);
  const [editing, setEditing] = useState(null);
  const fileInput = useRef(null);
  useEffect(() => {
    window.addEventListener("tavern:lorebooks", reload); // the assistant changed books
    return () => window.removeEventListener("tavern:lorebooks", reload);
  }, [reload]);
  const books = data ? data.lorebooks : null;

  const create = async () => {
    const name = await askText("Название лорбука", { value: "Новый лорбук", ok: "Создать" });
    if (!name || !name.trim()) return;
    try {
      const book = await api.post("/lorebooks", { name: name.trim() });
      await reload();
      setEditing(book.id);
    } catch (e) { onError(e.message); }
  };
  const pick = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const book = await api.postBinary(`/lorebooks/import?name=${encodeURIComponent(file.name)}`, file, "application/json");
      await reload();
      notify("Лорбук импортирован", "ok");
      setEditing(book.id);
    } catch (err) { onError(err.message); }
  };
  const remove = async (book) => {
    if (!await ask(`Удалить лорбук «${book.name}» со всеми записями?`, { ok: "Удалить", danger: true })) return;
    try { await api.del(`/lorebooks/${book.id}`); await reload(); } catch (e) { onError(e.message); }
  };
  const editingBook = books && books.find((b) => b.id === editing);

  return html`<section class="lorebook-shelf" data-slot="lorebooks">
    <div class="section-head">
      ${showHeading && html`<h2>Лорбуки</h2>`}
      <span class="spacer"></span>
      <${IconButton} icon="plus" label="Новый лорбук" onClick=${create} />
      <${MenuButton} label="Другие действия" items=${[
        { icon: "file-up", label: "Импорт лорбука", onClick: () => fileInput.current.click() },
      ]} />
    </div>
    <input ref=${fileInput} type="file" accept=".json,application/json" hidden onChange=${pick} />
    ${books === null && html`<${Pending} error=${error} onRetry=${reload} />`}
    ${books && books.length === 0 && html`<${EmptyNote} title="Лорбуков пока нет"
      hint="Лорбук — набор записей о мире: места, предметы, правила. Запись попадает к модели, когда в чате всплывает её тема" />`}
    ${books && books.length > 0 && html`<ul class="list">
      ${books.map((b) => html`<li key=${b.id}>
        <button class="item book-item" onClick=${() => setEditing(b.id)}>
          <span class="book-line"><span class="book-name">${b.name}</span><span class="muted count">${b.blocks}</span></span>
          <span class="muted book-where">${whereOf(b)}</span></button>
        <${MenuButton} label="Действия с лорбуком" items=${[
          { icon: "download", label: "Скачать", href: `/api/lorebooks/${b.id}/export.json`, download: true },
          { icon: "trash-2", label: "Удалить", danger: true, onClick: () => remove(b) },
        ]} />
      </li>`)}
    </ul>`}
    ${editing && html`<${LorebookEditor} bookId=${editing} settings=${settings} onChanged=${reload}
      connected=${editingBook ? whereOf(editingBook) : ""}
      onClose=${() => { setEditing(null); reload(); }} onError=${onError} />`}
  </section>`;
}

export function LorebookSettings({ onChanged, onError }) {
  const [values, setValues] = useState(null);
  const [fieldRev, setFieldRev] = useState(0); // bumps when the server refuses: the fields show the real numbers again
  useEffect(() => { api.get("/lorebooks").then((d) => setValues(d.settings)).catch((e) => onError(e.message)); }, []);
  const save = async (key, raw) => {
    const value = raw === "" ? 0 : Number(raw);
    try {
      const saved = await api.put("/settings", { lorebooks: { ...values, [key]: value } });
      setValues({ ...values, ...saved.lorebooks });
      notify("Сохранено", "ok");
      onChanged();
    } catch (e) { setFieldRev((r) => r + 1); onError(e.message); }
  };
  const clamp = (raw, max) => {
    const digits = raw.replace(/\D+/g, "").slice(0, String(max).length);
    return digits === "" ? "" : String(Math.min(max, Number(digits)));
  };
  const field = (key, label, hint, max) => html`<label class="stack">${label}
    <input key=${key + fieldRev} class="num" inputmode="numeric" pattern="[0-9]*" value=${String(values[key])}
      onChange=${(e) => save(key, clamp(e.target.value, max))} />
    <span class="hint">${hint}</span></label>`;
  return html`<div class="stack" data-slot="lorebook-settings">
    <${BookPicker} scope="global" title="Общие лорбуки — во всех чатах" onError=${onError} />
    <p class="hint">Лорбуки чата включаются в окне участников чата, лорбуки персонажа — в его карточке.</p>
    ${values && html`<div class="row">
      ${field("budget_tokens", "Лимит, токенов", "Сколько места в запросе могут занять записи", 1000000)}
      ${field("budget_percent", "или % окна модели", "Если больше 0, лимит считается от окна модели", 100)}
      ${field("recursion", "Шагов цепочки", "Сколько раз текст найденных записей ищет следующие; 0 — без цепочек", 10)}
    </div>
    <p class="hint">Если записи не влезают в лимит, первыми отпадают зелёные с большим номером. Что не влезло, видно в просмотре запроса у ответа.</p>`}
  </div>`;
}
