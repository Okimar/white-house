// Settings tab "Шаблоны": instruct templates for connections in text mode. Built-in ones are read-only
// (make a copy to change one); your own ones are edited field by field with a live preview of a short
// sample conversation. Import takes our files and SillyTavern instruct/context files; export gives both.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";

const FIELDS = [
  ["bos", "В самом начале"], ["story_string", "Обёртка системного текста ({{system}})"],
  ["chat_start", "Перед разговором"],
  ["system_prefix", "Система: перед"], ["system_suffix", "Система: после"],
  ["user_prefix", "Пользователь: перед"], ["user_suffix", "Пользователь: после"],
  ["assistant_prefix", "Модель: перед"], ["assistant_suffix", "Модель: после"],
];

export function Templates({ onError }) {
  const [templates, setTemplates] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [preview, setPreview] = useState(null);
  const [mergeInto, setMergeInto] = useState(false);
  const fileInput = useRef(null);

  const reload = () => api.get("/templates").then((d) => { setTemplates(d.templates); return d.templates; })
    .catch((e) => { onError(e.message); return []; });
  useEffect(() => { reload(); }, []);

  const open = templates.find((t) => t.id === openId);
  useEffect(() => { setDraft(open ? { ...open, stopText: (open.stop || []).join("\n") } : null); }, [openId, templates]);

  const asTemplate = (d) => {
    const out = { name: d.name, models: d.models, system_as_user: Boolean(d.system_as_user),
      stop: d.stopText.split("\n").filter((s) => s) };
    FIELDS.forEach(([k]) => { out[k] = d[k] || ""; });
    return out;
  };

  // The preview follows the draft as it is typed.
  useEffect(() => {
    if (!draft) { setPreview(null); return undefined; }
    const timer = setTimeout(() => {
      api.post("/templates/preview", { template: asTemplate(draft) })
        .then(setPreview).catch((e) => setPreview({ error: e.message }));
    }, 300);
    return () => clearTimeout(timer);
  }, [draft]);

  const save = async () => {
    try {
      await api.put(`/templates/${draft.id}`, asTemplate(draft));
      await reload();
    } catch (err) { onError(err.message); }
  };

  const copy = async () => {
    try {
      const made = await api.post("/templates", { ...asTemplate(draft), name: draft.name + " (копия)" });
      await reload();
      setOpenId(made.id);
    } catch (err) { onError(err.message); }
  };

  const create = async () => {
    try {
      const made = await api.post("/templates", { name: "Новый шаблон" });
      await reload();
      setOpenId(made.id);
    } catch (err) { onError(err.message); }
  };

  const remove = async () => {
    if (!confirm(`Удалить шаблон «${draft.name}»?`)) return;
    await api.del(`/templates/${draft.id}`).catch((e) => onError(e.message));
    setOpenId(null);
    reload();
  };

  const importFile = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const into = mergeInto && open && !open.builtin ? `?into=${open.id}` : "";
      const made = await api.post(`/templates/import${into}`, data);
      await reload();
      setOpenId(made.id);
    } catch (err) { onError(err instanceof SyntaxError ? "Файл не похож на JSON" : err.message); }
  };

  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value });
  const readOnly = draft && draft.builtin;

  return html`
    <h2>Шаблоны разметки</h2>
    <p class="muted">Нужны подключениям в режиме «text»: весь разговор уходит в модель одним текстом,
      обёрнутым в метки, на которых модель училась. «auto» в подключении выбирает шаблон по названию модели.</p>
    <div class="row">
      <select value=${openId || ""} aria-label="Шаблон" onChange=${(e) => setOpenId(e.target.value || null)}>
        <option value="">— выберите шаблон —</option>
        <optgroup label="Свои">${templates.filter((t) => !t.builtin).map((t) =>
          html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}</optgroup>
        <optgroup label="Встроенные">${templates.filter((t) => t.builtin).map((t) =>
          html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}</optgroup>
      </select>
      <button onClick=${create}>＋ Новый</button>
      <button onClick=${() => fileInput.current.click()} title="Наш файл или файлы SillyTavern (instruct, context)">Импорт</button>
      ${open && !open.builtin && html`<label><input type="checkbox" checked=${mergeInto}
        onChange=${(e) => setMergeInto(e.target.checked)} /> добавить файл ST в этот шаблон</label>`}
      <input ref=${fileInput} type="file" accept=".json,application/json" hidden onChange=${importFile} />
    </div>
    ${draft && html`
      <section class="template-editor stack" data-slot="template-editor">
        <div class="row">
          <input value=${draft.name} disabled=${readOnly} aria-label="Название шаблона" onInput=${set("name")} />
          ${readOnly && html`<span class="badge">встроенный</span>`}
          <span class="spacer"></span>
          <a class="button" href=${`/api/templates/${draft.id}/export`} title="Файл Таверны">↓</a>
          <a class="button" href=${`/api/templates/${draft.id}/export?format=st-instruct`}>↓ ST instruct</a>
          <a class="button" href=${`/api/templates/${draft.id}/export?format=st-context`}>↓ ST context</a>
        </div>
        <label class="stack">Названия моделей для «auto» (регулярное выражение)
          <input value=${draft.models} disabled=${readOnly} onInput=${set("models")} class="mono" /></label>
        <div class="template-fields">
          ${FIELDS.map(([key, label]) => html`<label key=${key} class="stack">${label}
            <textarea rows="2" class="mono" value=${draft[key] || ""} disabled=${readOnly} onInput=${set(key)} /></label>`)}
          <label class="stack">Стоп-строки (по одной на строку)
            <textarea rows="2" class="mono" value=${draft.stopText} disabled=${readOnly} onInput=${set("stopText")} /></label>
        </div>
        <label><input type="checkbox" checked=${Boolean(draft.system_as_user)} disabled=${readOnly}
          onChange=${(e) => setDraft({ ...draft, system_as_user: e.target.checked })} />
          у модели нет роли «система» — отправлять системный текст как сообщение пользователя</label>
        <div class="row">
          ${readOnly
            ? html`<button class="primary" onClick=${copy}>Сделать копию для правки</button>`
            : html`<button class="primary" onClick=${save}>Сохранить</button>
                   <button onClick=${copy}>Копия</button><button onClick=${remove}>Удалить</button>`}
        </div>
        <h3>Как это увидит модель</h3>
        ${preview && (preview.error
          ? html`<p class="muted">${preview.error}</p>`
          : html`<pre class="template-preview" data-slot="template-preview">${preview.text}</pre>
                 <p class="muted">Стоп: ${preview.stop.map((s) => JSON.stringify(s)).join(", ") || "нет"}</p>`)}
      </section>`}
  `;
}
