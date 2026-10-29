// Lorebooks (stage 5): books of bubbles that belong to no character.
// - LorebookShelf: the "Лорбуки" part of the left panel (list, + Лорбук, import, open, download, delete);
//   it opens LorebookEditor itself.
// - LorebookEditor: name, description, "↶ Отменить" / "🕘 История", and the book's bubble map (the
//   same CharacterTree as a character's, rooted at the book).
// - BookPicker: which books are switched on for one place (global, a chat, a character); saves at once.
// - LorebookSettings: Settings → "Лорбуки": global books, budget, chain steps.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { CharacterTree } from "./CharacterTree.js";
import { HistoryPanel } from "./CharacterEditor.js";

export function BookPicker({ scope, target = null, title, hint, onError }) {
  const [books, setBooks] = useState(null);
  const [on, setOn] = useState([]);
  useEffect(() => {
    const query = `/lorebook-uses?scope=${scope}` + (target ? `&target=${encodeURIComponent(target)}` : "");
    Promise.all([api.get("/lorebooks"), api.get(query)])
      .then(([list, uses]) => { setBooks(list.lorebooks); setOn(uses.lorebook_ids); })
      .catch((e) => onError(e.message));
  }, [scope, target]);
  const toggle = async (id, wanted) => {
    const next = wanted ? [...on, id] : on.filter((x) => x !== id);
    setOn(next);
    try {
      setOn((await api.put("/lorebook-uses", { scope, target, lorebook_ids: next })).lorebook_ids);
    } catch (e) { onError(e.message); }
  };
  return html`<fieldset class="book-picker" data-slot="book-picker">
    <legend>${title}</legend>
    ${books === null && html`<span class="muted">Загрузка…</span>`}
    ${books && books.length === 0 && html`<span class="muted">Книг пока нет: создайте их в разделе «Лорбуки» слева.</span>`}
    ${books && books.map((b) => html`<label key=${b.id}>
      <input type="checkbox" checked=${on.includes(b.id)} onChange=${(e) => toggle(b.id, e.target.checked)} />
      📖 ${b.name} <span class="muted">(${b.blocks})</span></label>`)}
    ${hint && html`<span class="muted">${hint}</span>`}
  </fieldset>`;
}

export function LorebookEditor({ bookId, settings, onClose, onChanged, onError }) {
  const [book, setBook] = useState(null);
  const [revision, setRevision] = useState(0); // bumps after undo / restore: everything reloads
  const [showHistory, setShowHistory] = useState(false);
  const [status, setStatus] = useState("");
  const base = `/lorebooks/${bookId}`;

  useEffect(() => {
    api.get(base).then(setBook).catch((e) => { onError(e.message); onClose(); });
  }, [bookId, revision]);
  const say = (text) => { setStatus(text); setTimeout(() => setStatus(""), 3500); };
  const undo = async () => {
    try {
      const { undone } = await api.post(`${base}/undo`);
      say(undone ? `Отменено: ${undone.what}` : "Отменять больше нечего");
      setRevision((r) => r + 1);
      onChanged();
    } catch (e) { onError(e.message); }
  };
  const close = () => {
    api.post(`${base}/history/close`).catch(() => {});
    onClose();
  };
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
    if (key === "name" && !value.trim()) { onError("У книги должно быть название"); return; }
    try {
      setBook(await api.put(base, { [key]: value }));
      say("✓ сохранено");
      onChanged();
    } catch (e) { onError(e.message); }
  };

  return html`
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && close()}>
      <div class="dialog stack tree-dialog" data-slot="dialog" role="dialog" aria-modal="true"
        aria-label=${book ? book.name : "Лорбук"}>
        <div class="side-head"><strong>📖 ${book ? book.name : "Лорбук"}</strong>
          ${status && html`<span class="muted history-status" role="status">${status}</span>`}
          <span class="spacer"></span>
          <button type="button" onClick=${undo} title="Отменить последнее изменение книги или её пузырей (Ctrl+Z)">↶ Отменить</button>
          <button type="button" aria-pressed=${showHistory} onClick=${() => setShowHistory(!showHistory)}
            title="Версии книги">🕘 История</button>
          <a class="button" href=${`/api${base}/export.json`} title="Скачать файл лорбука (подходит и для SillyTavern)">↓</a>
          <button type="button" onClick=${close} title="Закрыть">✕</button></div>
        ${showHistory && html`<${HistoryPanel} base=${base} revision=${revision}
          onRestored=${(text) => { say(text); setRevision((r) => r + 1); onChanged(); }} onError=${onError} />`}
        ${book === null && html`<p class="muted">Загрузка…</p>`}
        ${book && html`<div class="row book-head" data-slot="book-head">
          <label class="grow">Название
            <input key=${"n" + revision} value=${book.name} onBlur=${(e) => saveField("name", e.target.value.trim())} /></label>
          <label class="grow">Описание (для себя, модели не уходит)
            <input key=${"d" + revision} value=${book.description} onBlur=${(e) => saveField("description", e.target.value)} /></label>
        </div>`}
        ${book && html`<${CharacterTree} character=${{ id: bookId, name: book.name, book: true }}
          settings=${settings} revision=${revision} onError=${onError} />`}
      </div>
    </div>`;
}

export function LorebookShelf({ settings, onError }) {
  const [books, setBooks] = useState([]);
  const [editing, setEditing] = useState(null);
  const fileInput = useRef(null);
  const load = () => api.get("/lorebooks").then((d) => setBooks(d.lorebooks)).catch((e) => onError(e.message));
  useEffect(() => { load(); }, []);

  const create = async () => {
    const name = prompt("Название книги", "Новый лорбук");
    if (!name || !name.trim()) return;
    try {
      const book = await api.post("/lorebooks", { name: name.trim() });
      await load();
      setEditing(book.id);
    } catch (e) { onError(e.message); }
  };
  const pick = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const book = await api.postBinary(`/lorebooks/import?name=${encodeURIComponent(file.name)}`, file, "application/json");
      await load();
      setEditing(book.id);
    } catch (err) { onError(err.message); }
  };
  const remove = async (book) => {
    if (!confirm(`Удалить лорбук «${book.name}» со всеми записями?`)) return;
    try { await api.del(`/lorebooks/${book.id}`); await load(); } catch (e) { onError(e.message); }
  };
  const where = (b) => [b.global ? "везде" : "", b.chats ? `чатов: ${b.chats}` : "", b.characters ? `персонажей: ${b.characters}` : ""]
    .filter(Boolean).join(", ") || "не подключена";

  return html`<section class="lorebook-shelf" data-slot="lorebooks">
    <h2>Лорбуки</h2>
    <ul class="list">
      ${books.length === 0 && html`<li class="muted">Книг пока нет</li>`}
      ${books.map((b) => html`<li key=${b.id}>
        <button class="item" onClick=${() => setEditing(b.id)} title=${`Открыть: записей ${b.blocks}; ${where(b)}`}>
          📖 ${b.name} <span class="muted">${b.blocks}</span></button>
        <a class="button" href=${`/api/lorebooks/${b.id}/export.json`} title="Скачать (подходит и для SillyTavern)">↓</a>
        <button title="Удалить лорбук" onClick=${() => remove(b)}>🗑</button>
      </li>`)}
    </ul>
    <input ref=${fileInput} type="file" accept=".json,application/json" hidden onChange=${pick} />
    <div class="row">
      <button onClick=${create}>+ Лорбук</button>
      <button onClick=${() => fileInput.current.click()} title="Файл лорбука SillyTavern или наш">Импорт лорбука</button>
    </div>
    ${editing && html`<${LorebookEditor} bookId=${editing} settings=${settings} onChanged=${load}
      onClose=${() => { setEditing(null); load(); }} onError=${onError} />`}
  </section>`;
}

export function LorebookSettings({ onChanged, onError }) {
  const [values, setValues] = useState(null);
  useEffect(() => { api.get("/lorebooks").then((d) => setValues(d.settings)).catch((e) => onError(e.message)); }, []);
  const save = async (key, raw) => {
    const value = raw === "" ? 0 : Number(raw);
    try {
      const saved = await api.put("/settings", { lorebooks: { ...values, [key]: value } });
      setValues({ ...values, ...saved.lorebooks });
      onChanged();
    } catch (e) { onError(e.message); }
  };
  const field = (key, label, title, max) => html`<label title=${title}>${label}
    <input class="num" inputmode="numeric" pattern="[0-9]*" value=${String(values[key])}
      onChange=${(e) => save(key, e.target.value.replace(/\D+/g, "").slice(0, String(max).length))} /></label>`;
  return html`<div class="stack" data-slot="lorebook-settings">
    <${BookPicker} scope="global" title="Общие лорбуки — во всех чатах" onError=${onError} />
    <p class="muted">Лорбуки чата включаются в окне 👥 чата, лорбуки персонажа — в его карточке.</p>
    ${values && html`<div class="row">
      ${field("budget_tokens", "Лимит, токенов", "Сколько места в промпте могут занять записи лорбуков", 1000000)}
      ${field("budget_percent", "или % окна модели", "Если больше 0 — лимит считается от окна модели, а не в токенах", 100)}
      ${field("recursion", "Шагов цепочки", "Сколько раз текст найденных записей ищет следующие (0 — без цепочек)", 10)}
    </div>
    <p class="muted">Если записи не влезают в лимит, первыми отпадают зелёные с большим номером; что не влезло, видно в 👁 у ответа.</p>`}
  </div>`;
}
