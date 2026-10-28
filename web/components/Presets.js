// Settings tab "Промпт": prompt presets. Pick the general preset, copy, delete, import/export
// SillyTavern files, and edit a preset: the list of blocks (order, on/off, role, place) and the
// model settings. The built-in preset is read-only; copy it to change it.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { ROLES } from "./roles.js";
import { NoteList } from "./Notes.js";

const KIND_HINTS = {
  main: "главный промпт", persona: "карточка «это я»", cast: "карточки персонажей чата",
  examples: "примеры диалогов", lore_before: "лорбуки (этап 5)", lore_after: "лорбуки (этап 5)",
  history: "сама переписка", post_history: "после переписки", text: "свой текст",
};
const TEXT_KINDS = ["main", "post_history", "text"];
const PARAMS = [
  ["temperature", "Температура", 0.05], ["top_p", "Top P", 0.01], ["top_k", "Top K", 1],
  ["max_tokens", "Длина ответа (токены)", 1], ["presence_penalty", "Presence penalty", 0.05],
  ["frequency_penalty", "Frequency penalty", 0.05],
];

function BlockRow({ block, index, count, readOnly, onChange, onMove, onRemove }) {
  const [open, setOpen] = useState(false);
  const set = (patch) => onChange({ ...block, ...patch });
  return html`
    <li class=${"preset-block" + (block.enabled ? "" : " off")} data-slot="preset-block">
      <div class="row">
        <input type="checkbox" checked=${block.enabled} disabled=${readOnly} title="Включить / выключить"
          onChange=${(e) => set({ enabled: e.target.checked })} />
        <button class="item" onClick=${() => setOpen(!open)} aria-expanded=${open}>
          ${open ? "▾" : "▸"} ${block.name} <span class="muted">· ${KIND_HINTS[block.kind]}
          ${block.position === "depth" ? ` · глубина ${block.depth}` : ""}</span></button>
        ${!readOnly && html`
          <button title="Выше" disabled=${index === 0} onClick=${() => onMove(index, -1)}>▲</button>
          <button title="Ниже" disabled=${index === count - 1} onClick=${() => onMove(index, 1)}>▼</button>
          ${block.kind === "text" && html`<button title="Удалить блок" onClick=${() => onRemove(index)}>🗑</button>`}`}
      </div>
      ${open && html`<div class="stack block-edit">
        <label class="stack">Название
          <input value=${block.name} disabled=${readOnly} onInput=${(e) => set({ name: e.target.value })} /></label>
        ${block.kind === "history" ? html`
          <div class="row">
            <label>Отправлять
              <select value=${block.history.mode} disabled=${readOnly}
                onChange=${(e) => set({ history: { ...block.history, mode: e.target.value } })}>
                <option value="all">всю историю</option>
                <option value="last">последние N сообщений</option>
              </select></label>
            ${block.history.mode === "last" && html`<label>N
              <input type="number" min="1" value=${block.history.count} disabled=${readOnly}
                onInput=${(e) => set({ history: { ...block.history, count: Number(e.target.value) || 1 } })} /></label>`}
            <label title="0 — без ограничения">Не больше токенов
              <input type="number" min="0" value=${block.history.max_tokens} disabled=${readOnly}
                onInput=${(e) => set({ history: { ...block.history, max_tokens: Number(e.target.value) || 0 } })} /></label>
          </div>`
        : html`
          <div class="row">
            <label>От кого
              <select value=${block.role} disabled=${readOnly} onChange=${(e) => set({ role: e.target.value })}>
                ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.icon} ${r.label}</option>`)}
              </select></label>
            <label>Где
              <select value=${block.position} disabled=${readOnly} onChange=${(e) => set({ position: e.target.value })}>
                <option value="top">на своём месте в списке</option>
                <option value="depth">внутри чата, на глубине</option>
              </select></label>
            ${block.position === "depth" && html`<label>Глубина
              <input type="number" min="0" max="1000" value=${block.depth} disabled=${readOnly}
                onInput=${(e) => set({ depth: Number(e.target.value) || 0 })} /></label>`}
          </div>
          ${TEXT_KINDS.includes(block.kind) && html`<textarea rows="5" value=${block.text} disabled=${readOnly}
            placeholder="Текст. Можно {{char}} и {{user}}" onInput=${(e) => set({ text: e.target.value })}></textarea>`}`}
      </div>`}
    </li>`;
}

// Global settings for finding character tree blocks (green bubbles).
function RetrievalSettings({ settings, onChanged, onError }) {
  const [connections, setConnections] = useState([]);
  const current = { scan_messages: 3, budget_tokens: 2000, threshold: 0.55, ...(settings.retrieval || {}) };
  const [draft, setDraft] = useState(current);
  useEffect(() => { api.get("/connections").then((d) => setConnections(d.connections)).catch(() => {}); }, []);
  const save = async (patch) => {
    try { await api.put("/settings", patch); onChanged(); } catch (e) { onError(e.message); }
  };
  const num = (key, value) => setDraft({ ...draft, [key]: value === "" ? "" : Number(value) });
  const dirty = JSON.stringify(draft) !== JSON.stringify(current);
  return html`
    <h2>Поиск пузырей персонажей</h2>
    <p class="muted">Зелёные пузыри из дерева персонажа подключаются, когда тема всплывает в последних сообщениях:
      по ключевым словам и, если выбрано подключение для векторов, по смыслу.</p>
    <div class="param-grid">
      <label class="stack">Искать в последних сообщениях
        <input type="number" min="1" max="50" value=${draft.scan_messages} onInput=${(e) => num("scan_messages", e.target.value)} /></label>
      <label class="stack">Места для зелёных пузырей (токены)
        <input type="number" min="0" step="100" value=${draft.budget_tokens} onInput=${(e) => num("budget_tokens", e.target.value)} /></label>
      <label class="stack" title="0..1: насколько близким по смыслу должен быть пузырь">Порог похожести
        <input type="number" min="0" max="1" step="0.05" value=${draft.threshold} onInput=${(e) => num("threshold", e.target.value)} /></label>
    </div>
    <button class="primary" disabled=${!dirty} onClick=${() => save({ retrieval: {
      scan_messages: Math.max(1, Math.round(Number(draft.scan_messages) || 3)),
      budget_tokens: Math.max(0, Math.round(Number(draft.budget_tokens) || 0)),
      threshold: Math.min(1, Math.max(0, Number(draft.threshold) || 0)) } })}>Сохранить</button>
    <label class="stack">Подключение для поиска по смыслу (векторы)
      <select value=${settings.embedding_connection || ""} onChange=${(e) => save({ embedding_connection: e.target.value })}>
        <option value="">— нет, только ключевые слова —</option>
        ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name} · ${c.model || "модель не задана"}</option>`)}
      </select></label>`;
}

export function Presets({ settings, onChanged, onError }) {
  const [presets, setPresets] = useState([]);
  const [active, setActive] = useState(settings.active_preset || "default");
  const [current, setCurrent] = useState(null); // {id, name, builtin, data} being viewed/edited
  const [dirty, setDirty] = useState(false);
  const [exporting, setExporting] = useState(null); // preset whose file format is being chosen
  const fileInput = useRef(null);

  const reload = () => api.get("/presets").then((d) => { setPresets(d.presets); setActive(d.active); return d; })
    .catch((e) => onError(e.message));
  const open = (id) => api.get(`/presets/${id}`).then((p) => { setCurrent(p); setDirty(false); })
    .catch((e) => onError(e.message));
  useEffect(() => { reload().then((d) => d && open(d.active)); }, []);

  const choose = async (id) => {
    await api.put("/settings", { active_preset: id === "default" ? "" : id }).catch((e) => onError(e.message));
    setActive(id);
    onChanged();
  };
  const copyOf = async (p) => {
    const name = prompt("Название копии", `${p.name} (копия)`);
    if (!name) return;
    try {
      const created = await api.post("/presets", { name, from: p.id });
      await reload();
      open(created.id);
    } catch (e) { onError(e.message); }
  };
  // A new preset starts as the built-in one, ready to edit.
  const createNew = async () => {
    const name = prompt("Название нового пресета", "Мой пресет");
    if (!name || !name.trim()) return;
    try {
      const created = await api.post("/presets", { name: name.trim(), from: "default" });
      await reload();
      open(created.id);
    } catch (e) { onError(e.message); }
  };
  const remove = async (p) => {
    if (!confirm(`Удалить пресет «${p.name}»?`)) return;
    await api.del(`/presets/${p.id}`).catch((e) => onError(e.message));
    await reload();
    if (current && current.id === p.id) open("default");
    onChanged();
  };
  const importFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
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
      onChanged();
    } catch (e) { onError(e.message); }
  };

  const edit = (data) => { setCurrent({ ...current, data }); setDirty(true); };
  const blocks = current ? current.data.blocks : [];
  const setBlock = (i, block) => edit({ ...current.data, blocks: blocks.map((b, j) => (j === i ? block : b)) });
  const move = (i, step) => {
    const next = [...blocks];
    [next[i], next[i + step]] = [next[i + step], next[i]];
    edit({ ...current.data, blocks: next });
  };
  const removeBlock = (i) => edit({ ...current.data, blocks: blocks.filter((_, j) => j !== i) });
  const addBlock = () => edit({ ...current.data, blocks: [...blocks, {
    id: "text-" + Date.now().toString(36), kind: "text", name: "Свой блок", enabled: true, role: "system",
    text: "", position: "top", depth: 4 }] });
  const setParam = (key, raw) => {
    const params = { ...current.data.params };
    if (raw === "") delete params[key];
    else params[key] = Number(raw);
    edit({ ...current.data, params });
  };
  const readOnly = !current || current.builtin;

  return html`
    <h2>Пресеты промпта</h2>
    <p class="muted">Пресет — это порядок блоков промпта и настройки модели. Отмеченный кружком
      используется во всех чатах, где не выбран свой (меню «Участники» в чате).</p>
    <ul class="list">
      ${presets.map((p) => html`<li key=${p.id} class=${current && current.id === p.id ? "active" : ""}>
        <input type="radio" name="active-preset" checked=${p.id === active} title="Общий пресет"
          onChange=${() => choose(p.id)} />
        <button class="item" onClick=${() => open(p.id)}>${p.name}${p.builtin ? " · встроенный" : ""}</button>
        <button title="Сделать копию" onClick=${() => copyOf(p)}>⧉</button>
        <button title="Скачать файл пресета" onClick=${() => setExporting(p)}>↓</button>
        ${!p.builtin && html`<button title="Удалить" onClick=${() => remove(p)}>🗑</button>`}
      </li>`)}
    </ul>
    <input ref=${fileInput} type="file" accept=".json,application/json" hidden onChange=${importFile} />
    <div class="row">
      <button class="primary" onClick=${createNew}>+ Создать пресет</button>
      <button onClick=${() => fileInput.current.click()}>Загрузить пресет (наш или SillyTavern)…</button>
    </div>
    ${exporting && html`<div class="overlay" onClick=${(e) => e.target === e.currentTarget && setExporting(null)}>
      <div class="dialog stack" role="dialog" aria-modal="true" aria-label="Формат файла">
        <strong>Скачать «${exporting.name}»</strong>
        <a class="button primary" href=${`/api/presets/${exporting.id}/export?format=tavern`}
          onClick=${() => setExporting(null)}>Наш формат — все настройки сохранятся</a>
        <a class="button" href=${`/api/presets/${exporting.id}/export?format=st`}
          onClick=${() => setExporting(null)}>SillyTavern — часть настроек пропадёт</a>
        <p class="muted">В SillyTavern нет места, например, для лимита истории и наших служебных текстов.</p>
        <button onClick=${() => setExporting(null)}>Отмена</button>
      </div>
    </div>`}

    ${current && html`
      <h2>${readOnly ? "Просмотр" : "Правка"}: ${current.name}</h2>
      ${readOnly && html`<p class="notice">Встроенный пресет менять нельзя. Нажмите ⧉, чтобы сделать копию для правки.</p>`}
      ${!readOnly && html`<label class="stack">Название
        <input value=${current.name} onInput=${(e) => { setCurrent({ ...current, name: e.target.value }); setDirty(true); }} /></label>`}
      <h3>Блоки (сверху вниз — как пойдут к модели)</h3>
      <ul class="preset-blocks">
        ${blocks.map((b, i) => html`<${BlockRow} key=${b.id} block=${b} index=${i} count=${blocks.length}
          readOnly=${readOnly} onChange=${(nb) => setBlock(i, nb)} onMove=${move} onRemove=${removeBlock} />`)}
      </ul>
      ${!readOnly && html`<button onClick=${addBlock}>+ Свой блок</button>`}

      <h3>Модель</h3>
      <div class="param-grid">
        ${PARAMS.map(([key, label, step]) => html`<label key=${key} class="stack">${label}
          <input type="number" step=${step} disabled=${readOnly} placeholder="как у модели"
            value=${current.data.params[key] ?? ""} onInput=${(e) => setParam(key, e.target.value)} /></label>`)}
        <label class="stack" title="Сколько модель думает перед ответом (Gemini 3 и другие думающие модели)">
          Уровень размышлений
          <select disabled=${readOnly} value=${current.data.params.reasoning_effort || ""}
            onChange=${(e) => { const params = { ...current.data.params };
              if (e.target.value) params.reasoning_effort = e.target.value; else delete params.reasoning_effort;
              edit({ ...current.data, params }); }}>
            <option value="">как у модели</option>
            <option value="minimal">минимальный</option><option value="low">низкий</option>
            <option value="medium">средний</option><option value="high">высокий</option>
          </select></label>
        <label class="stack" title="0 — как в подключении">Размер памяти (токены)
          <input type="number" min="0" disabled=${readOnly} value=${current.data.max_context_tokens}
            onInput=${(e) => edit({ ...current.data, max_context_tokens: Number(e.target.value) || 0 })} /></label>
      </div>
      <label><input type="checkbox" checked=${current.data.prefer_card_prompts} disabled=${readOnly}
        onChange=${(e) => edit({ ...current.data, prefer_card_prompts: e.target.checked })} />
        Свой промпт карточки заменяет главный промпт пресета</label>
      <h3>Служебные тексты</h3>
      <label class="stack">«Написать за меня»
        <textarea rows="3" disabled=${readOnly} value=${current.data.prompts.impersonate}
          onInput=${(e) => edit({ ...current.data, prompts: { ...current.data.prompts, impersonate: e.target.value } })}></textarea></label>
      ${[
        ["no_write_for_me", "Запрет писать за моего персонажа", 3],
        ["prefill", "Начало ответа модели (prefill): отправляется как начало её ответа; пусто — нет", 3],
        ["personality_format", "Формат характера, например [{{char}}'s personality: {{personality}}]; пусто — «Personality:»", 1],
        ["scenario_format", "Формат сценария с {{scenario}}; пусто — «Scenario:»", 1],
      ].map(([key, label, rows]) => html`<label key=${key} class="stack">${label}
        <textarea rows=${rows} disabled=${readOnly} value=${current.data.prompts[key] || ""}
          onInput=${(e) => edit({ ...current.data, prompts: { ...current.data.prompts, [key]: e.target.value } })}></textarea></label>`)}
      ${!readOnly && html`<div class="row sticky-save">
        <button class="primary" disabled=${!dirty} onClick=${save}>Сохранить пресет</button>
        ${dirty && html`<button onClick=${() => open(current.id)}>Отменить правки</button>`}
      </div>`}`}
    <${RetrievalSettings} settings=${settings} onChanged=${onChanged} onError=${onError} />
    <h2>Общие заметки автора</h2>
    <p class="muted">Работают во всех чатах. Заметки чата и персонажей — кнопка 📝 в шапке чата.</p>
    <${NoteList} scope="global" onError=${onError} />
  `;
}
