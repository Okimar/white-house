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
import { MenuButton, openMenu } from "./Menu.js";
import { Select } from "./Select.js";
import { GroupedItems } from "./Groups.js";
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
    ${books && books.length === 0 && html`<p class="muted book-picker-empty">Лорбуков пока нет. Создайте первый лорбук на боковой панели в разделе «Лорбуки», чтобы подключить его здесь.</p>`}
    ${books && books.map((b) => html`<label key=${b.id} title=${b.name}>
      <input type="checkbox" checked=${on.includes(b.id)} onChange=${(e) => toggle(b.id, e.target.checked)} />
      <span class="pick-name">${b.name} <span class="muted">(${b.blocks})</span></span></label>`)}
    ${hint && html`<span class="hint">${hint}</span>`}
  </fieldset>`;
}

export function LorebookEditor({ bookId, settings, connected = "", onClose, onChanged, onError }) {
  const [book, setBook] = useState(null);
  const [revision, setRevision] = useState(0); // bumps after undo / restore: everything reloads
  const [showHistory, setShowHistory] = useState(false);
  const [fieldSaving, setFieldSaving] = useState(false);
  const [fieldSavedRecently, setFieldSavedRecently] = useState(false);
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
    setFieldSaving(true);
    try {
      setBook(await api.put(base, { [key]: value }));
      setFieldSavedRecently(true);
      setTimeout(() => setFieldSavedRecently(false), 2200);
      notify("Сохранено", "ok");
      onChanged();
    } catch (e) { onError(e.message); } finally { setFieldSaving(false); }
  };

  return html`
    <div class="overlay" ...${backdrop(close)}>
      <div class="dialog stack tree-dialog" data-slot="dialog" role="dialog" aria-modal="true"
        aria-label=${book ? book.name : "Лорбук"}>
        <div class="side-head"><${Icon} name="book-open" /><strong class="dialog-name" title=${book ? book.name : ""}>${book ? book.name : "Лорбук"}</strong>
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
        ${book && html`<div class="book-head-card" data-slot="book-head-card">
          <div class="row book-head" data-slot="book-head">
            <label class="grow">Название
              <input key=${"n" + revision} value=${book.name} onBlur=${(e) => saveField("name", e.target.value.trim())} /></label>
            <label class="grow">Описание
              <span class="hint">Для себя, модели не передаётся</span>
              <input key=${"d" + revision} value=${book.description} onBlur=${(e) => saveField("description", e.target.value)} /></label>
          </div>
          <div class="row book-head-sub">
            ${connected ? html`<div class="book-connected-pill" role="status"><${Icon} name="link" size=${13} /> <span>Подключён: <strong>${connected}</strong></span></div>` : null}
            <span class="spacer"></span>
            <span class="save-state-pill saved book-auto-save-pill"><${Icon} name="check" size=${13} /> <span>Сохраняется при выходе из поля</span></span>
          </div>
        </div>`}
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
    if (!await ask(`Удалить лорбук «${book.name}» со всеми записями?`, { ok: "Удалить", danger: true,
      hint: "Лорбук будет удалён безвозвратно. Его записи больше не будут отправляться модели." })) return;
    try { await api.del(`/lorebooks/${book.id}`); await reload(); } catch (e) { onError(e.message); }
  };
  const bookActions = (b, tools) => [
    { icon: "download", label: "Скачать", href: `/api/lorebooks/${b.id}/export.json`, download: true },
    { icon: "library", label: "В группу…", onClick: tools.move },
    { icon: "trash-2", label: "Удалить", danger: true, onClick: () => remove(b) },
  ];
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
    ${books && books.length === 0 && html`<${EmptyNote}
      icon="book-open"
      title="Лорбуков пока нет"
      hint="Лорбук — это база знаний о вашем мире: места, фракции, предметы, правила и факты. Нужные записи автоматически отправляются модели, когда в чате упоминается их тема.">
      <div class="row empty-actions" style="margin-top: 12px; gap: 8px">
        <button class="primary" onClick=${create}><${Icon} name="plus" /> Создать лорбук</button>
        <button onClick=${() => fileInput.current.click()}><${Icon} name="file-up" /> Импортировать</button>
      </div>
    </${EmptyNote}>`}
    ${books && books.length > 0 && html`<${GroupedItems} kind="lorebook" items=${books} idOf=${(b) => b.id}
      labelOf=${(b) => b.name} onError=${onError}
      onPress=${(b, el, tools) => openMenu(el, [{ icon: "book-open", label: "Открыть", onClick: () => setEditing(b.id) }, ...bookActions(b, tools)])}
      renderRow=${(b, tools) => html`<li key=${b.id}>
        <button class="item book-item" title=${b.name} onClick=${() => setEditing(b.id)}>
          <span class="book-line"><span class="book-name">${b.name}</span><span class="muted count">${b.blocks}</span></span>
          <span class="muted book-where">${whereOf(b)}</span></button>
        <${MenuButton} label="Действия с лорбуком" items=${() => bookActions(b, tools)} />
      </li>`} />`}
    ${editing && html`<${LorebookEditor} bookId=${editing} settings=${settings} onChanged=${reload}
      connected=${editingBook ? whereOf(editingBook) : ""}
      onClose=${() => { setEditing(null); reload(); }} onError=${onError} />`}
  </section>`;
}

export function LorebookSettings({ settings = {}, onChanged, onError }) {
  const [values, setValues] = useState(null);
  const [fieldRev, setFieldRev] = useState(0); // bumps when the server refuses: the fields show the real numbers again
  // Depth and sensitivity of the search are shared with the character bubbles (settings "retrieval").
  const retrieval = { scan_messages: 3, budget_tokens: 2000, threshold: 0.55, ...(settings.retrieval || {}) };
  const [search, setSearch] = useState(retrieval);
  // Every save sends the whole set, so it starts from the newest values, not from this render's: two quick changes
  // (a switch, then a field) must not undo each other.
  const latest = useRef(null);
  useEffect(() => {
    api.get("/lorebooks").then((d) => { latest.current = d.settings; setValues(d.settings); })
      .catch((e) => onError(e.message));
  }, []);
  const save = async (key, raw) => {
    // numbers come from the text fields; switches and the book order are saved as they are
    const value = typeof raw === "string" && key !== "strategy" ? (raw === "" ? 0 : Number(raw)) : raw;
    const before = latest.current[key];
    latest.current = { ...latest.current, [key]: value };
    setValues(latest.current);
    try {
      await api.put("/settings", { lorebooks: latest.current });
      notify("Сохранено", "ok");
      onChanged();
    } catch (e) {
      latest.current = { ...latest.current, [key]: before };
      setValues(latest.current);
      setFieldRev((r) => r + 1);
      onError(e.message);
    }
  };
  const saveSearch = async (key, value) => {
    try {
      const saved = await api.put("/settings", { retrieval: { ...search, [key]: value } });
      setSearch({ ...search, ...saved.retrieval });
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
  return html`<div class="stack lore-settings" data-slot="lorebook-settings">
    <h2>Включены везде</h2>
    <${BookPicker} scope="global" title="Общие лорбуки — во всех чатах" onError=${onError}
      hint="Лорбуки чата включаются в окне участников чата, лорбуки персонажа — в его карточке." />
    ${values && html`
    <h2>Сколько места занимают записи</h2>
    <div class="param-grid">
      ${field("budget_tokens", "Лимит, токенов", "Сколько места в запросе могут занять записи всех подключённых лорбуков", 1000000)}
      ${field("budget_percent", "Или процент окна модели", "Если больше 0, лимит рассчитывается от окна модели в процентах (как в SillyTavern: 25%). Лимит в токенах используется, когда размер окна модели неизвестен", 100)}
    </div>
    <p class="hint">Если записи не помещаются в лимит токенов, первыми отпадают записи с меньшим «Порядком» (Order из SillyTavern, либо стоящие ниже в книге). Записи с правилом «не считать в лимите» отправляются всегда. Что именно вошло в запрос, можно увидеть в просмотре промпта (значок 👁 у ответа).</p>
    <h2>Как ищутся записи</h2>
    <div class="param-grid">
      ${field("scan_messages", "Сообщений в поиске", "Сколько последних сообщений чата проверять на ключевые слова. В SillyTavern — 2. 0 — по сообщениям не искать, идут только постоянные записи", 1000)}
      <label class="stack">Порог похожести
        <input key=${"thr" + fieldRev} class="num" type="number" min="0" max="1" step="0.05" value=${search.threshold}
          onChange=${(e) => saveSearch("threshold", Math.min(1, Math.max(0, Number(e.target.value) || 0)))} />
        <span class="hint">От 0 до 1, для векторного поиска по смыслу: чем выше порог, тем ближе по теме должна быть запись. Общий с пузырями персонажей.</span></label>
      ${field("recursion", "Шагов цепочки", "Сколько раз текст найденных записей может включить следующие записи. 0 — цепочки выключены, как в SillyTavern", 10)}
    </div>
    <label class="check" data-slot="lorebook-names">
      <input type="checkbox" checked=${values.include_names} onChange=${(e) => save("include_names", e.target.checked)} />
      Читать сообщения с именами</label>
    <p class="hint">Ключ «Аня» найдётся и тогда, когда Аня сама пишет, но не называет себя: поиск видит «Аня: текст». Так делает SillyTavern.</p>
    <label class="check" data-slot="lorebook-scan-notes">
      <input type="checkbox" checked=${values.scan_notes} onChange=${(e) => save("scan_notes", e.target.checked)} />
      Искать ключи и в заметках автора</label>
    <p class="hint">Если включено, записи находятся и по словам из сработавших заметок автора, а не только из сообщений. В SillyTavern по умолчанию выключено.</p>
    <h2>В каком порядке берутся записи</h2>
    <ol class="hint lore-order">
      <li>Лорбуки текущего чата.</li>
      <li>Лорбуки вашего персонажа (персоны).</li>
      <li>${STRATEGY_LINES[values.strategy] || STRATEGY_LINES.character_first}</li>
    </ol>
    <label class="stack" data-slot="lorebook-strategy">Персонажи или общие — кто раньше
      <${Select} value=${values.strategy} onChange=${(e) => save("strategy", e.target.value)}>
        <option value="character_first">Сначала лорбуки персонажей</option>
        <option value="global_first">Сначала общие лорбуки</option>
        <option value="evenly">Поровну, решает только «Порядок»</option>
      </${Select}>
      <span class="hint">Когда записи не помещаются в лимит, первыми отпадают книги из конца этого списка, а внутри книги — записи с меньшим «Порядком».</span></label>
    <details class="conn-more lore-fold" data-slot="lorebook-more">
      <summary>Тонкая настройка, как в SillyTavern</summary>
      <div class="param-grid">
        ${field("budget_cap", "Потолок лимита, токенов", "Лимит из процента окна не будет больше этого числа. 0 — без потолка", 1000000)}
        ${field("min_activations", "Найти не меньше записей", "Если по последним сообщениям нашлось меньше, поиск идёт глубже в чат, пока не найдёт столько. 0 — выключено", 100)}
        ${field("min_depth_max", "Предел глубины, сообщений", "Для «Найти не меньше записей»: глубже этого числа сообщений не искать. 0 — до начала чата", 1000)}
      </div>
      <label class="check" data-slot="lorebook-scoring">
        <input type="checkbox" checked=${values.group_scoring} onChange=${(e) => save("group_scoring", e.target.checked)} />
        Групповой счёт</label>
      <p class="hint">Из записей одной группы остаётся та, у которой нашлось больше разных ключевых слов, а не случайная. Запись может задать это сама.</p>
    </details>`}
  </div>`;
}

const STRATEGY_LINES = {
  character_first: "Лорбуки персонажей чата, затем общие (включённые везде).",
  global_first: "Общие лорбуки (включённые везде), затем лорбуки персонажей чата.",
  evenly: "Лорбуки персонажей и общие вместе: между ними решает «Порядок» записи.",
};
