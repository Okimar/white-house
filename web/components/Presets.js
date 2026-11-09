// Settings tab "Промпт": prompt presets. Pick the general preset, duplicate, delete, import/export
// SillyTavern files, and edit a preset: the list of blocks (order, on/off, role, place) and the
// model settings. The built-in preset is read-only; duplicate it to change it.
import { Select } from "./Select.js";
import { Suggest } from "./Suggest.js";
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { notify } from "../core/notify.js";
import { getChoice, setChoice } from "../core/choice.js";
import { backdrop, useEscape } from "../core/keys.js";
import { ROLES } from "./roles.js";
import { NoteList } from "./Notes.js";
import { RegexOwner } from "./Regex.js";
import { Icon, IconButton } from "./Icon.js";
import { ask, askText } from "./Dialogs.js";

const KIND_HINTS = {
  main: "главный промпт", persona: "карточка «это я»", cast: "карточки персонажей чата",
  examples: "примеры диалогов", lore_before: "лорбуки до переписки", lore_after: "лорбуки после переписки",
  history: "сама переписка", post_history: "после переписки", text: "свой текст",
  thinking: "размышление перед ответом",
};
// Behaviour blocks (block.when): text sent only while the behaviour is on. [what it is for, when it is sent]
const WHEN_HINTS = {
  no_write_for_me: "Когда в чате есть «это я» и модели нельзя писать за него (переключатель — в «Участниках»).",
  speaker_reply: "Когда в чате несколько персонажей: говорит, за кого сейчас писать ({{char}} — говорящий).",
  impersonate: "Когда вы нажали «Написать за меня».",
  narrator: "Когда включён «Рассказчик» после хода «все сразу» (переключатель — в «Участниках»). Идёт только его запросу.",
};
const TEXT_KINDS = ["main", "post_history", "text", "thinking"];
const REMOVABLE = ["text", "thinking"];
const THINKING_HINT = ["Например:", "Сцена: [где, кто рядом]", "Чего хочет {{char}} сейчас: [...]",
  "План ответа: [...]"].join("\n");
// [key, label, step, the English name people search for]
const PARAMS = [
  ["temperature", "Температура", 0.05, ""], ["top_p", "Ядро выборки", 0.01, "Top P"],
  ["top_k", "Число кандидатов", 1, "Top K"], ["max_tokens", "Длина ответа (токены)", 1, ""],
  ["presence_penalty", "Штраф за повторение тем", 0.05, "Presence penalty"],
  ["frequency_penalty", "Штраф за повторение слов", 0.05, "Frequency penalty"],
];
// [key, label, rows, explanation]
const SERVICE_TEXTS = [
  ["prefill", "Начало ответа модели", 3, "По-английски prefill: отправляется как начало её ответа. Пусто — нет."],
];

function BlockRow({ block, index, count, readOnly, groups, onChange, onMove, onRemove }) {
  const [open, setOpen] = useState(false);
  const set = (patch) => onChange({ ...block, ...patch });
  return html`
    <li class=${"preset-block" + (block.enabled ? "" : " off")} data-slot="preset-block">
      <div class="row">
        <input type="checkbox" checked=${block.enabled} disabled=${readOnly} aria-label=${"Включить блок: " + block.name}
          onChange=${(e) => set({ enabled: e.target.checked })} />
        <button class="item" onClick=${() => setOpen(!open)} aria-expanded=${open}>
          <${Icon} name=${open ? "chevron-down" : "chevron-right"} /> ${block.name} <span class="muted">· ${block.when ? "по условию" : KIND_HINTS[block.kind]}
          ${block.position === "depth" ? ` · глубина ${block.depth}` : ""}</span>
          ${block.group && html`<span class="tag" title="Из блоков группы включён только один"><${Icon} name="arrow-left-right" /> ${block.group}</span>`}</button>
        ${!readOnly && html`
          <${IconButton} icon="arrow-up" label="Выше" disabled=${index === 0} onClick=${() => onMove(index, -1)} />
          <${IconButton} icon="arrow-down" label="Ниже" disabled=${index === count - 1} onClick=${() => onMove(index, 1)} />
          ${REMOVABLE.includes(block.kind) && !block.when && html`<${IconButton} icon="trash-2" label="Удалить блок" class="danger-text"
            onClick=${() => onRemove(index)} />`}`}
      </div>
      ${open && html`<div class="stack block-edit">
        <label class="stack">Название
          <input value=${block.name} disabled=${readOnly} onInput=${(e) => set({ name: e.target.value })} /></label>
        ${block.kind === "history" ? html`
          <div class="row">
            <label>Отправлять
              <${Select} value=${block.history.mode} disabled=${readOnly}
                onChange=${(e) => set({ history: { ...block.history, mode: e.target.value } })}>
                <option value="all">всю историю</option>
                <option value="last">последние N сообщений</option>
              </${Select}></label>
            ${block.history.mode === "last" && html`<label>N
              <input type="number" min="1" value=${block.history.count} disabled=${readOnly}
                onInput=${(e) => set({ history: { ...block.history, count: Number(e.target.value) || 1 } })} /></label>`}
            <label>Не больше токенов
              <input type="number" min="0" value=${block.history.max_tokens} disabled=${readOnly}
                onInput=${(e) => set({ history: { ...block.history, max_tokens: Number(e.target.value) || 0 } })} /></label>
            <label>Слать прошлые размышления (ответов)
              <input type="number" min="0" value=${block.history.thoughts || 0} disabled=${readOnly}
                onInput=${(e) => set({ history: { ...block.history, thoughts: Math.max(0, Number(e.target.value) || 0) } })} /></label>
          </div>
          <p class="hint">Токенов: 0 — без ограничения. Прошлые размышления: сколько последних ответов уходят модели
            вместе с их размышлением; повторяющиеся строки не шлются; 0 — не слать.</p>`
        : block.when ? html`
          <p class="hint" data-slot="when-hint">${WHEN_HINTS[block.when]} Выключатель слева — отправлять ли этот текст вообще.</p>
          <div class="row">
            <label>От кого
              <${Select} value=${block.role} disabled=${readOnly} onChange=${(e) => set({ role: e.target.value })}>
                ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
              </${Select}></label>
          </div>
          <textarea rows="4" value=${block.text} disabled=${readOnly} aria-label=${block.name}
            placeholder="Текст. Можно {{char}} и {{user}}" onInput=${(e) => set({ text: e.target.value })}></textarea>
          <p class="hint">Идёт в конце промпта, после переписки, и только в нужный момент.</p>`
        : html`
          <div class="row">
            <label>От кого
              <${Select} value=${block.role} disabled=${readOnly} onChange=${(e) => set({ role: e.target.value })}>
                ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
              </${Select}></label>
            <label>Где
              <${Select} value=${block.position} disabled=${readOnly} onChange=${(e) => set({ position: e.target.value })}>
                <option value="top">на своём месте в списке</option>
                <option value="depth">внутри чата, на глубине</option>
              </${Select}></label>
            ${block.position === "depth" && html`<label>Глубина
              <input type="number" min="0" max="1000" value=${block.depth} disabled=${readOnly}
                onInput=${(e) => set({ depth: Number(e.target.value) || 0 })} /></label>`}
            <label>Группа «один из»
              <${Suggest} value=${block.group || ""} options=${groups} disabled=${readOnly} aria-label="Группа" placeholder="нет"
                onInput=${(e) => set({ group: e.target.value })} /></label>
          </div>
          <p class="hint">Блоки с одной группой — варианты на выбор: включите один, остальные выключатся.</p>
          ${block.kind === "thinking" && html`<p class="muted">Шаблон, который модель заполняет перед ответом.
            White House сам попросит думать внутри ${"<thinking>…</thinking>"} и уберёт размышление из текста ответа.</p>`}
          ${TEXT_KINDS.includes(block.kind) && html`<textarea rows="5" value=${block.text} disabled=${readOnly}
            placeholder=${block.kind === "thinking" ? THINKING_HINT
              : "Текст. Можно {{char}} и {{user}}"} onInput=${(e) => set({ text: e.target.value })}></textarea>`}`}
      </div>`}
    </li>`;
}

// Global settings for finding character tree blocks (green bubbles).
function RetrievalSettings({ settings, onChanged, onError }) {
  const [connections, setConnections] = useState([]);
  const stored = { scan_messages: 3, budget_tokens: 2000, threshold: 0.55, meaning_on: true, meaning_messages: 3,
    meaning_user: true, meaning_characters: true, ...(settings.retrieval || {}) };
  const manual = !!stored.threshold_manual;
  const chosen = connections.find((c) => c.id === settings.embedding_connection);
  const svc = chosen?.meaning_threshold || null; // {service, value}: what suits the chosen vector service
  // until the user types a number, the service's own default stands (the server does the same)
  const current = { ...stored, threshold: !manual && svc ? svc.value : stored.threshold };
  const [draft, setDraft] = useState(current);
  const [vecCheck, setVecCheck] = useState(null); // {busy} | {ok, text} | {ok: false, text}
  const checkVectors = async () => {
    setVecCheck({ busy: true });
    try {
      const d = await api.post("/vectors/test", {});
      setVecCheck(d.ok ? { ok: true, text: `Работает: модель ${d.model}, вектор из ${d.size} чисел, ${d.ms} мс` }
        : { ok: false, text: d.error });
    } catch (e) { setVecCheck({ ok: false, text: e.message }); }
  };
  useEffect(() => { setVecCheck(null); }, [settings.embedding_connection]);
  useEffect(() => { api.get("/connections").then((d) => setConnections(d.connections)).catch((e) => onError(e.message)); }, []);
  const save = async (patch) => {
    try { await api.put("/settings", patch); notify("Сохранено", "ok"); onChanged(); } catch (e) { onError(e.message); }
  };
  const num = (key, value) => setDraft({ ...draft, [key]: value === "" ? "" : Number(value) });
  const FIELDS = ["scan_messages", "budget_tokens", "threshold", "meaning_messages"];
  const setMeaning = (patch) => save({ retrieval: { ...stored, ...patch } });
  const dirty = FIELDS.some((k) => draft[k] !== current[k]);
  const setServiceThreshold = async () => {
    const next = { ...stored, threshold: svc.value, threshold_manual: false };
    setDraft({ ...draft, threshold: svc.value });
    await save({ retrieval: next });
  };
  // picking a service moves the threshold to its default, unless the user set one by hand
  const pickConnection = async (id) => {
    const found = connections.find((c) => c.id === id)?.meaning_threshold;
    const patch = { embedding_connection: id };
    if (found && !manual) patch.retrieval = { ...stored, threshold: found.value, threshold_manual: false };
    if (found && !manual) setDraft((d) => ({ ...d, threshold: found.value }));
    await save(patch);
  };
  const saveRetrieval = async () => {
    const next = {
      ...stored,
      scan_messages: Math.max(1, Math.round(Number(draft.scan_messages) || 3)),
      meaning_messages: Math.min(20, Math.max(1, Math.round(Number(draft.meaning_messages) || 3))),
      budget_tokens: Math.max(0, Math.round(Number(draft.budget_tokens) || 0)),
      threshold: Math.min(1, Math.max(0, Number(draft.threshold) || 0)) };
    next.threshold_manual = manual || next.threshold !== current.threshold;
    await save({ retrieval: next });
    setDraft(next); // what was stored, so the button does not stay lit after an empty field
  };
  return html`
    <h2>Поиск пузырей персонажей</h2>
    <p class="muted">Зелёные пузыри из дерева персонажа подключаются, когда тема всплывает в последних сообщениях:
      по ключевым словам и, если выбрано подключение для векторов, по смыслу.</p>
    <div class="param-grid">
      <label class="stack">Искать в последних сообщениях
        <input type="number" min="1" max="50" value=${draft.scan_messages} onInput=${(e) => num("scan_messages", e.target.value)} /></label>
      <label class="stack">Места для зелёных пузырей (токены)
        <input type="number" min="0" step="100" value=${draft.budget_tokens} onInput=${(e) => num("budget_tokens", e.target.value)} /></label>
      <label class="stack">Порог похожести
        <input type="number" min="0" max="1" step="0.05" value=${draft.threshold} onInput=${(e) => num("threshold", e.target.value)} />
        <span class="hint">От 0 до 1: насколько близким по смыслу должен быть пузырь.</span></label>
    </div>
    ${svc && html`<div class="row" data-testid="threshold-hint">
      <span class="hint">Для ${svc.service} подходит около ${svc.value.toFixed(2).replace(".", ",")}</span>
      ${Number(draft.threshold) !== svc.value && html`<button onClick=${setServiceThreshold}>Поставить</button>`}
    </div>`}
    <label class="check"><input type="checkbox" checked=${stored.meaning_on !== false}
      onChange=${(e) => setMeaning({ meaning_on: e.target.checked })} />
      Искать по смыслу</label>
    <p class="hint">Выключено: векторы и подключение остаются, но запросов за смыслом нет, только ключевые слова.</p>
    <div class="param-grid">
      <label class="stack">Сообщений для поиска по смыслу
        <input type="number" min="1" max="20" disabled=${stored.meaning_on === false} value=${draft.meaning_messages}
          onInput=${(e) => num("meaning_messages", e.target.value)} />
        <span class="hint">Каждое сообщение ищется отдельно, пузырь берёт лучшее совпадение. Запрос один на весь ответ.</span></label>
    </div>
    <label class="check"><input type="checkbox" checked=${stored.meaning_user !== false} disabled=${stored.meaning_on === false}
      onChange=${(e) => setMeaning({ meaning_user: e.target.checked })} />
      Мои сообщения</label>
    <label class="check"><input type="checkbox" checked=${stored.meaning_characters !== false} disabled=${stored.meaning_on === false}
      onChange=${(e) => setMeaning({ meaning_characters: e.target.checked })} />
      Сообщения персонажей</label>
    <div class="row">
      <button disabled=${!dirty} onClick=${saveRetrieval}>Сохранить поиск</button>
    </div>
    <label class="stack">Подключение для поиска по смыслу (векторы)
      <${Select} value=${settings.embedding_connection || ""} onChange=${(e) => pickConnection(e.target.value)}>
        <option value="">— нет, только ключевые слова —</option>
        ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name} · ${c.model || "модель не задана"}</option>`)}
      </${Select}></label>
    ${chosen && chosen.model && !looksLikeEmbedding(chosen.model) && html`
      <p class="hint">Похоже, это модель для чата, а не для векторов. Нажмите «Проверить векторы».</p>`}
    <label class="check"><input type="checkbox" checked=${!!settings.auto_vectors} disabled=${!settings.embedding_connection}
      onChange=${(e) => save({ auto_vectors: e.target.checked })} />
      Автовекторизация: после правки пузыря векторы обновляются сами</label>
    <p class="hint">Выключено: ничего не тратится, а в дереве видно, сколько пузырей без векторов, и есть кнопка «Векторизовать».</p>
    <div class="row">
      <button disabled=${!settings.embedding_connection || vecCheck?.busy} onClick=${checkVectors}>Проверить векторы</button>
    </div>
    ${vecCheck && !vecCheck.busy && html`<p class=${vecCheck.ok ? "hint" : "hint err"} role="status">${vecCheck.text}</p>`}
    ${vecCheck?.busy && html`<p class="hint" role="status">Проверяю…</p>`}`;
}

// Words that embedding models carry in their names; a chat model has none of them (same list as providers/catalog.py).
const EMBED_HINTS = ["embed", "voyage", "e5", "bge", "gte", "nomic", "minilm", "mxbai", "arctic", "jina", "bert", "snowflake", "uae", "sentence"];
const looksLikeEmbedding = (model) => EMBED_HINTS.some((h) => (model || "").toLowerCase().includes(h));

function ExportDialog({ preset, onClose }) {
  useEscape(onClose);
  return html`<div class="overlay" ...${backdrop(onClose)}>
    <div class="dialog stack" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Формат файла">
      <strong>Скачать «${preset.name}»</strong>
      <a class="button primary" href=${`/api/presets/${preset.id}/export?format=tavern`}
        onClick=${onClose}>Наш формат — все настройки сохранятся</a>
      <a class="button" href=${`/api/presets/${preset.id}/export?format=st`}
        onClick=${onClose}>SillyTavern — часть настроек пропадёт</a>
      <p class="muted">В SillyTavern нет места, например, для лимита истории и наших служебных текстов.</p>
      <button onClick=${onClose}>Отмена</button>
    </div>
  </div>`;
}

export function Presets({ settings, onChanged, onError }) {
  const [presets, setPresets] = useState([]);
  const [active, setActive] = useState(settings.active_preset || "default");
  const [current, setCurrent] = useState(null); // {id, name, builtin, data} being viewed/edited
  const [dirty, setDirty] = useState(false);
  const [ready, setReady] = useState(false); // the first list and preset are here: until then an empty box keeps the place (no jump)
  const [exporting, setExporting] = useState(null); // preset whose file format is being chosen
  const fileInput = useRef(null);

  const reload = () => api.get("/presets").then((d) => { setPresets(d.presets); setActive(d.active); return d; })
    .catch((e) => onError(e.message));
  const open = (id) => api.get(`/presets/${id}`).then((p) => { setCurrent(p); setDirty(false); setChoice("preset", id); })
    .catch((e) => onError(e.message));
  // Reopens the preset that was being looked at; if it was deleted meanwhile, the active one.
  useEffect(() => {
    reload().then((d) => {
      if (!d) return;
      const mine = getChoice("preset");
      return open(mine && d.presets.some((p) => p.id === mine) ? mine : d.active);
    }).finally(() => setReady(true));
  }, []);

  // Leaving a preset with unsaved edits asks first.
  const discardOk = async () => !dirty || ask("Выбросить несохранённые правки?", { ok: "Выбросить", danger: true });
  const go = async (id) => {
    if (current && current.id === id) return;
    if (await discardOk()) open(id);
  };

  const choose = async (id) => {
    await api.put("/settings", { active_preset: id === "default" ? "" : id }).catch((e) => onError(e.message));
    setActive(id);
    onChanged();
  };
  const copyOf = async (p) => {
    if (!(await discardOk())) return;
    const name = await askText("Название дубликата", { value: `${p.name} (дубликат)` });
    if (!name) return;
    try {
      const created = await api.post("/presets", { name, from: p.id });
      await reload();
      open(created.id);
    } catch (e) { onError(e.message); }
  };
  // A new preset starts as the built-in one, ready to edit.
  const createNew = async () => {
    if (!(await discardOk())) return;
    const name = await askText("Название нового пресета", { value: "Мой пресет" });
    if (!name || !name.trim()) return;
    try {
      const created = await api.post("/presets", { name: name.trim(), from: "default" });
      await reload();
      open(created.id);
    } catch (e) { onError(e.message); }
  };
  const remove = async (p) => {
    if (!(await ask(`Удалить пресет «${p.name}»?`, { ok: "Удалить", danger: true }))) return;
    await api.del(`/presets/${p.id}`).catch((e) => onError(e.message));
    await reload();
    if (current && current.id === p.id) open("default");
    onChanged();
  };
  const importFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file || !(await discardOk())) return;
    try {
      const created = await api.postBinary(`/presets/import?name=${encodeURIComponent(file.name)}`, file,
        "application/json");
      await reload();
      open(created.id);
    } catch (err) { onError(err.message); }
  };
  const save = async () => {
    try {
      const saved = await api.put(`/presets/${current.id}`, { name: current.name, data: current.data });
      setCurrent({ ...saved, builtin: false });
      setDirty(false);
      await reload();
      notify("Сохранено", "ok");
      onChanged();
    } catch (e) { onError(e.message); }
  };

  const edit = (data) => { setCurrent({ ...current, data }); setDirty(true); };
  const blocks = current ? current.data.blocks : [];
  // A block switched on in a group switches the others of that group off.
  const setBlock = (i, block) => edit({ ...current.data, blocks: blocks.map((b, j) => {
    if (j === i) return block;
    const group = (block.group || "").trim();
    return block.enabled && group && (b.group || "").trim() === group && b.enabled ? { ...b, enabled: false } : b;
  }) });
  const groups = [...new Set(blocks.map((b) => (b.group || "").trim()).filter(Boolean))];
  const hasThinking = blocks.some((b) => b.kind === "thinking");
  const addThinking = () => edit({ ...current.data, blocks: [...blocks, {
    id: "thinking", kind: "thinking", name: "Размышление", enabled: true, role: "system",
    text: "", position: "top", depth: 4, group: "" }] });
  const move = (i, step) => {
    const next = [...blocks];
    [next[i], next[i + step]] = [next[i + step], next[i]];
    edit({ ...current.data, blocks: next });
  };
  const removeBlock = (i) => edit({ ...current.data, blocks: blocks.filter((_, j) => j !== i) });
  const addBlock = () => edit({ ...current.data, blocks: [...blocks, {
    id: "text-" + Date.now().toString(36), kind: "text", name: "Свой блок", enabled: true, role: "system",
    text: "", position: "top", depth: 4, group: "" }] });
  const setParam = (key, raw) => {
    const params = { ...current.data.params };
    if (raw === "") delete params[key];
    else params[key] = Number(raw);
    edit({ ...current.data, params });
  };
  const readOnly = !current || current.builtin;

  if (!ready) return html`<div class="settings-pending" aria-busy="true"></div>`;
  return html`
    ${Array.isArray(settings.service_texts_moved) && settings.service_texts_moved.length > 0 && html`
      <div class="notice" data-slot="moved-notice" role="status">
        <p>Служебные тексты («не писать за меня», «рассказчик», «кто отвечает», «написать за меня») стали блоками пресета.
          Ваши формулировки перенесены без потерь в пресеты: ${settings.service_texts_moved.join(", ")}.</p>
        <button onClick=${() => api.put("/settings", { service_texts_moved: [] }).then(onChanged).catch((e) => onError(e.message))}>
          Понятно</button>
      </div>`}
    <h2>Пресеты промпта</h2>
    <p class="hint">Пресет — это порядок блоков промпта и настройки модели. Пресет с пометкой «Общий»
      используется во всех чатах, где не выбран свой (меню «Участники» в чате).</p>
    <ul class="list">
      ${presets.map((p) => html`<li key=${p.id} class=${current && current.id === p.id ? "active" : ""}>
        <label class="pick-active">
          <input type="radio" name="active-preset" checked=${p.id === active} onChange=${() => choose(p.id)} />
          <span>${p.id === active ? "Общий" : "Выбрать"}</span>
        </label>
        <button class="item" onClick=${() => go(p.id)}>${p.name}${p.builtin ? " · встроенный" : ""}</button>
        <${IconButton} icon="copy" label="Дублировать" onClick=${() => copyOf(p)} />
        <${IconButton} icon="download" label="Скачать файл пресета" onClick=${() => setExporting(p)} />
        ${!p.builtin && html`<${IconButton} icon="trash-2" label="Удалить" class="danger-text" onClick=${() => remove(p)} />`}
      </li>`)}
    </ul>
    <input ref=${fileInput} type="file" accept=".json,application/json" hidden onChange=${importFile} />
    <div class="row">
      <button onClick=${createNew}><${Icon} name="plus" /> Новый пресет</button>
      <button onClick=${() => fileInput.current.click()}><${Icon} name="upload" /> Загрузить из файла</button>
    </div>
    <p class="hint">Файл пресета: наш или SillyTavern.</p>
    ${exporting && html`<${ExportDialog} preset=${exporting} onClose=${() => setExporting(null)} />`}

    ${current && html`
      <h2>${readOnly ? "Просмотр" : "Правка"}: ${current.name}</h2>
      ${readOnly && html`<p class="notice">Встроенный пресет менять нельзя. Нажмите «Дублировать» в списке, чтобы сделать дубликат для правки.</p>`}
      ${!readOnly && html`<label class="stack">Название
        <input value=${current.name} onInput=${(e) => { setCurrent({ ...current, name: e.target.value }); setDirty(true); }} /></label>`}
      <h3>Блоки (сверху вниз — как пойдут к модели)</h3>
      <ul class="preset-blocks">
        ${blocks.map((b, i) => html`<${BlockRow} key=${b.id} block=${b} index=${i} count=${blocks.length}
          readOnly=${readOnly} groups=${groups} onChange=${(nb) => setBlock(i, nb)} onMove=${move} onRemove=${removeBlock} />`)}
      </ul>
      ${!readOnly && html`<div class="row">
        <button onClick=${addBlock}><${Icon} name="plus" /> Свой блок</button>
        ${!hasThinking && html`<button onClick=${addThinking}><${Icon} name="plus" /> Размышление</button>`}
      </div>
      ${!hasThinking && html`<p class="hint">«Размышление» — шаблон, который модель заполняет перед ответом.</p>`}`}

      ${!current.builtin && html`<details class="preset-regex"><summary><strong>Регекс-правила пресета</strong></summary>
        <p class="hint">Работают в чатах с этим пресетом, после общих правил. Правила, пришедшие с файлом пресета, ждут вашего разрешения.</p>
        <${RegexOwner} scope="preset" owner=${current.id} onError=${onError} /></details>`}

      <h3>Модель</h3>
      <div class="param-grid">
        ${PARAMS.map(([key, label, step, english]) => html`<label key=${key} class="stack">${label}
          <input type="number" step=${step} disabled=${readOnly} placeholder="как у модели"
            value=${current.data.params[key] ?? ""} onInput=${(e) => setParam(key, e.target.value)} />
          ${english && html`<span class="hint">${english}</span>`}</label>`)}
        <label class="stack">Уровень размышлений
          <${Select} disabled=${readOnly} value=${current.data.params.reasoning_effort || ""}
            onChange=${(e) => { const params = { ...current.data.params };
              if (e.target.value) params.reasoning_effort = e.target.value; else delete params.reasoning_effort;
              edit({ ...current.data, params }); }}>
            <option value="">как у модели</option>
            <option value="minimal">минимальный</option><option value="low">низкий</option>
            <option value="medium">средний</option><option value="high">высокий</option>
          </${Select}>
          <span class="hint">Сколько модель думает перед ответом (Gemini 3 и другие думающие модели).</span></label>
        <label class="stack">Размер памяти (токены)
          <input type="number" min="0" disabled=${readOnly} value=${current.data.max_context_tokens}
            onInput=${(e) => edit({ ...current.data, max_context_tokens: Number(e.target.value) || 0 })} />
          <span class="hint">0 — как в подключении.</span></label>
      </div>
      <label><input type="checkbox" checked=${current.data.prefer_card_prompts} disabled=${readOnly}
        onChange=${(e) => edit({ ...current.data, prefer_card_prompts: e.target.checked })} />
        Свой промпт карточки заменяет главный промпт пресета</label>
      <h3>Начало ответа модели</h3>
      <p class="hint">Тексты «не писать за меня», «рассказчик», «кто отвечает» и «написать за меня» теперь блоки в списке выше.</p>
      ${SERVICE_TEXTS.map(([key, label, rows, hint]) => html`<label key=${key} class="stack">${label}
        <textarea rows=${rows} disabled=${readOnly} value=${current.data.prompts[key] || ""}
          onInput=${(e) => edit({ ...current.data, prompts: { ...current.data.prompts, [key]: e.target.value } })}></textarea>
        ${hint && html`<span class="hint">${hint}</span>`}</label>`)}
      ${!readOnly && html`<div class="row sticky-save">
        <button class="primary" disabled=${!dirty} onClick=${save}>Сохранить пресет</button>
        ${dirty && html`<button onClick=${() => open(current.id)}>Отменить правки</button>`}
      </div>`}`}
    <${RetrievalSettings} settings=${settings} onChanged=${onChanged} onError=${onError} />
    <h2>Общие заметки автора</h2>
    <p class="muted">Работают во всех чатах. Заметки чата и персонажей — кнопка с блокнотом <${Icon} name="notebook-pen" /> в шапке чата.</p>
    <${NoteList} scope="global" onError=${onError} />
  `;
}
