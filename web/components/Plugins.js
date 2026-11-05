// Settings tab "Дополнения": installed plugins with their switch, what each says it does, its
// settings, model access (allowed at all, its own limits, ask each time) and spending; install from
// a file or a GitHub link, check for updates, go back to the previous version, delete. A plugin that is not built in is switched on
// only after the full-access warning (ApproveDialog). `PluginAsks` is the "may it call a model?"
// window shown over the app while a plugin waits for an answer.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { money } from "../core/spending.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, EmptyNote, useLoad } from "./State.js";

const USES = {
  commands: "добавляет команды в чат (/…)",
  messages: "добавляет сообщения в чат",
  storage: "хранит свои данные в чатах",
  models: "обращается к моделям (тратит ваши запросы)",
  generator: "пишет ответы по-своему",
  events: "реагирует на события (новый ответ, участники…)",
  ui: "добавляет кнопки в окно",
  network: "ходит в интернет",
};
const SOURCE = { builtin: "встроенное", folder: "папка", file: "из файла", github: "GitHub" };

function ApproveDialog({ plugin, onYes, onNo }) {
  useEscape(onNo);
  return html`<div class="overlay" ...${backdrop(onNo)}>
    <div class="dialog" data-slot="dialog" role="alertdialog" aria-modal="true" aria-label="Включить дополнение">
      <p class="ask-text">Включить «${plugin.name}»?</p>
      <p class="muted">${plugin.author ? `Автор: ${plugin.author}. ` : ""}Версия ${plugin.version}.
        ${plugin.source.url ? html` Источник: ${plugin.source.url}` : ""}</p>
      ${plugin.uses.length > 0 && html`<p>Оно заявляет о себе, что:</p>
        <ul>${plugin.uses.map((u) => html`<li key=${u}>${USES[u] || u}</li>`)}</ul>`}
      <p class="warn" data-slot="plugin-warning"><${Icon} name="triangle-alert" /> Дополнение получит полный доступ к White House и к файлам на этом
        устройстве, в том числе к вашим ключам. Список выше — только его слова. Ставьте дополнения
        только из мест, которым доверяете.</p>
      <div class="row">
        <button class="primary" onClick=${onYes}>Включить</button>
        <button onClick=${onNo}>Отмена</button>
      </div>
    </div>
  </div>`;
}

// Connections for "connection" settings, loaded once per opened tab.
let connectionsPromise = null;
function useConnections() {
  const [list, setList] = useState([]);
  useEffect(() => {
    connectionsPromise ||= api.get("/connections").then((d) => d.connections)
      .catch((e) => { notify(e.message, "error"); return []; });
    connectionsPromise.then(setList);
    return () => { connectionsPromise = null; };
  }, []);
  return list;
}

function ConnectionField({ field, value, onSave }) {
  const connections = useConnections();
  return html`<label>${field.label}<br /><select value=${value} onChange=${(e) => onSave(e.target.value)}>
    <option value="">Как в чате</option>
    ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
  </select></label>`;
}

// "list": rows of small fields (e.g. the thinkers of the tree of thoughts); add, remove, edit.
function ListField({ field, value, onSave }) {
  const rows = value || [];
  const blank = () => Object.fromEntries(field.fields.map((f) => [f.key, f.default]));
  const put = (i, key, v) => onSave(rows.map((r, j) => (j === i ? { ...r, [key]: v } : r)));
  const dropRow = async (i) => {
    if (await ask("Убрать эту строку?", { ok: "Убрать", danger: true })) onSave(rows.filter((_, j) => j !== i));
  };
  return html`<fieldset class="setting-list" data-slot="plugin-setting-list"><legend>${field.label}</legend>
    ${rows.map((row, i) => html`<div key=${i} class="setting-row">
      ${field.fields.map((f) => html`<${SettingField} key=${f.key} field=${f} value=${row[f.key]}
        onSave=${(v) => put(i, f.key, v)} />`)}
      <${IconButton} icon="trash-2" label="Убрать строку" class="danger-text" onClick=${() => dropRow(i)} />
    </div>`)}
    <button onClick=${() => onSave([...rows, blank()])}><${Icon} name="plus" /> Добавить</button>
  </fieldset>`;
}

function SettingField({ field, value, onSave }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  if (field.type === "list") return html`<${ListField} field=${field} value=${value} onSave=${onSave} />`;
  if (field.type === "connection") return html`<${ConnectionField} field=${field} value=${value} onSave=${onSave} />`;
  if (field.type === "bool") {
    return html`<label><input type="checkbox" checked=${Boolean(value)}
      onChange=${(e) => onSave(e.target.checked)} /> ${field.label}</label>`;
  }
  if (field.type === "choice") {
    return html`<label>${field.label}<br /><select value=${value} onChange=${(e) => onSave(e.target.value)}>
      ${field.choices.map(([v, label]) => html`<option key=${v} value=${v}>${label}</option>`)}
    </select></label>`;
  }
  const number = field.type === "number";
  if (field.multiline) {
    return html`<label>${field.label}<br /><textarea rows="3" value=${draft} onInput=${(e) => setDraft(e.target.value)}
      onBlur=${() => { if (draft !== value) onSave(draft); }}></textarea></label>`;
  }
  return html`<label>${field.label}<br /><input type=${number ? "number" : "text"} value=${draft}
    min=${field.min} max=${field.max} onInput=${(e) => setDraft(e.target.value)}
    onBlur=${() => { const v = number ? Number(draft) : draft; if (v !== value) onSave(v); }} /></label>`;
}

function Access({ plugin, settings, onSave }) {
  const a = plugin.access;
  const code = settings && settings.currency.code ? settings.currency.code : "$";
  const num = (v) => Math.max(0, parseFloat(String(v).replace(",", ".")) || 0);
  return html`<div class="plugin-access" data-slot="plugin-access">
    <label><input type="checkbox" checked=${a.models} onChange=${(e) => onSave({ models: e.target.checked })} />
      может обращаться к моделям</label>
    ${a.models && html`
      <label><input type="checkbox" checked=${a.ask} onChange=${(e) => onSave({ ask: e.target.checked })} />
        спрашивать меня перед каждым запросом</label>
      <div class="row">
        <label>Лимит в день, ${code} <input type="number" min="0" step="0.01" value=${a.day}
          onChange=${(e) => onSave({ day: num(e.target.value) })} /></label>
        <label>в месяц, ${code} <input type="number" min="0" step="0.01" value=${a.month}
          onChange=${(e) => onSave({ month: num(e.target.value) })} /></label>
        <label>запросов на один ответ <input type="number" min="0" step="1" value=${a.per_reply}
          onChange=${(e) => onSave({ per_reply: Math.round(num(e.target.value)) })} /></label>
      </div>
      <p class="muted">0 — без лимита. Лимиты в день и месяц считают только то, что потратило это дополнение.</p>`}
    ${settings && html`<p class="muted" data-slot="plugin-spent">${`Потрачено сегодня: ${money(plugin.spent.day.cost,
      settings)} (запросов ${plugin.spent.day.requests}), за месяц: ${money(plugin.spent.month.cost, settings)} ` +
      `(запросов ${plugin.spent.month.requests})`}</p>`}
  </div>`;
}

function PluginCard({ plugin, spending, onChanged, onError }) {
  const [open, setOpen] = useState(false);
  const [approving, setApproving] = useState(false);
  const [update, setUpdate] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn) => {
    setBusy(true);
    try { await fn(); } catch (e) { onError(e.message); } finally { setBusy(false); onChanged(); }
  };
  const toggle = (on) => {
    if (!on) return run(() => api.post(`/plugins/${plugin.id}/disable`));
    if (!plugin.builtin) { setApproving(true); return null; }
    return run(() => api.post(`/plugins/${plugin.id}/enable`, {}));
  };
  const approve = () => { setApproving(false); run(() => api.post(`/plugins/${plugin.id}/enable`, { approve: true })); };
  const saveSetting = (key, value) => run(() => api.put(`/plugins/${plugin.id}/settings`, { [key]: value }));
  const saveAccess = (patch) => run(() => api.put(`/plugins/${plugin.id}/access`, patch));
  const checkUpdate = () => run(async () => setUpdate(await api.get(`/plugins/${plugin.id}/update`)));
  const doUpdate = () => run(async () => { await api.post(`/plugins/${plugin.id}/update`); setUpdate(null); });
  const remove = async () => {
    if (!(await ask(`Удалить дополнение «${plugin.name}»?`, { ok: "Удалить", danger: true }))) return;
    const data = plugin.data_count > 0
      && await ask(`Удалить и его данные в чатах (записей: ${plugin.data_count})?`,
        { ok: "Удалить данные", cancel: "Оставить данные", danger: true });
    run(() => api.del(`/plugins/${plugin.id}${data ? "?data=1" : ""}`));
  };
  const rollback = async () => {
    if (!(await ask(`Вернуть прошлую версию «${plugin.name}»?`,
      { ok: "Вернуть", hint: "Нынешняя сохранится, к ней можно будет вернуться так же." }))) return;
    run(() => api.post(`/plugins/${plugin.id}/rollback`, {}));
  };
  const github = plugin.source.kind === "github";
  return html`<li class="plugin-card" data-slot="plugin-card" data-plugin=${plugin.id}
    data-enabled=${plugin.enabled ? "true" : "false"}>
    <div class="row">
      <label class="plugin-switch"><input type="checkbox" checked=${plugin.enabled} disabled=${busy}
        aria-label=${"Включить " + plugin.name} onChange=${(e) => toggle(e.target.checked)} />
        <strong>${plugin.name}</strong>
        <span class="muted">${plugin.enabled ? "Включено" : "Выключено"}</span></label>
      <span class="muted">${plugin.version} · ${SOURCE[plugin.source.kind] || plugin.source.kind}
        ${plugin.author ? ` · ${plugin.author}` : ""}</span>
      <span class="spacer"></span>
      <button aria-expanded=${open} onClick=${() => setOpen(!open)}>${open ? "Свернуть" : "Подробнее"}</button>
    </div>
    ${plugin.error && html`<p class="error" data-slot="plugin-error">${plugin.error}</p>`}
    ${plugin.server_off && plugin.enabled && html`<p class="muted">White House запущен без дополнений: серверная часть не работает.</p>`}
    ${open && html`<div class="plugin-more">
      ${plugin.description && html`<p>${plugin.description}</p>`}
      ${plugin.uses.length > 0 && html`<p class="muted">Заявляет: ${plugin.uses.map((u) => USES[u] || u).join("; ")}</p>`}
      ${plugin.commands.length > 0 && html`<p class="muted">Команды: ${plugin.commands.map((c) => "/" + c).join(", ")}</p>`}
      ${plugin.settings.length > 0 && html`<h3>Настройки</h3>
        <div class="plugin-settings">${plugin.settings.map((f) => html`<div key=${f.key}>
          <${SettingField} field=${f} value=${plugin.values[f.key]} onSave=${(v) => saveSetting(f.key, v)} />
          ${f.help && html`<div class="muted">${f.help}</div>`}</div>`)}</div>`}
      <h3>Модели</h3>
      <${Access} plugin=${plugin} settings=${spending} onSave=${saveAccess} />
      <div class="row">
        ${github && html`<button disabled=${busy} onClick=${checkUpdate}>Проверить обновления</button>`}
        ${update && (update.update
          ? html`<button class="primary" disabled=${busy} onClick=${doUpdate}>Обновить</button>
              <span class="muted">После обновления дополнение выключится до вашего подтверждения.</span>`
          : html`<span class="muted">Новее нет</span>`)}
        ${plugin.versions > 0 && html`<button disabled=${busy} onClick=${rollback}>
          Вернуть прошлую версию (${plugin.versions})</button>`}
        <span class="spacer"></span>
        ${!plugin.builtin && html`<button class="danger" disabled=${busy} onClick=${remove}><${Icon} name="trash-2" /> Удалить</button>`}
      </div>
    </div>`}
    ${approving && html`<${ApproveDialog} plugin=${plugin} onYes=${approve} onNo=${() => setApproving(false)} />`}
  </li>`;
}

export function Plugins({ onError }) {
  const list = useLoad(() => api.get("/plugins"), []);
  const data = list.data;
  const [spending, setSpending] = useState(null);
  const [url, setUrl] = useState("");
  const [installing, setInstalling] = useState(false);
  const picker = useRef(null);

  const loadSpending = () => api.get("/spending/settings").then(setSpending).catch((e) => onError(e.message));
  const load = () => { list.reload(); loadSpending(); };
  useEffect(() => { loadSpending(); }, []);

  // Installing over an installed plugin asks first, then repeats with replace.
  const install = async (send) => {
    setInstalling(true);
    try {
      try {
        await send(false);
      } catch (e) {
        if (e.code !== "plugin_exists") throw e;
        const d = e.details || {};
        const replace = await ask(`Дополнение уже установлено (версия ${d.version}, новая ${d.new_version}). Заменить?`,
          { ok: "Заменить", hint: "Оно выключится до вашего подтверждения." });
        if (!replace) return;
        await send(true);
      }
      setUrl("");
    } catch (e) {
      onError(e.message);
    } finally {
      setInstalling(false);
      load();
    }
  };
  const fromFile = (file) => install((replace) =>
    api.postBinary(`/plugins/install${replace ? "?replace=1" : ""}`, file, "application/zip"));
  const fromUrl = (e) => {
    e.preventDefault();
    if (url.trim()) install((replace) => api.post("/plugins/install-url", { url: url.trim(), replace }));
  };

  if (!data) return html`<${Pending} error=${list.error} onRetry=${list.reload} />`;
  return html`<section class="plugins" data-slot="plugins">
    <p class="muted">Дополнения добавляют в White House новое: команды, кнопки, свои способы писать ответы.
      Новое дополнение появляется выключенным. Положить его можно и вручную, в папку дополнений рядом с вашими данными.</p>
    ${data.server_off && html`<p class="warn">White House запущен без дополнений: они не работают.</p>`}
    <div class="row">
      <button disabled=${installing} onClick=${() => picker.current && picker.current.click()}>Установить из файла</button>
      <input ref=${picker} type="file" accept=".zip,.tavern-plugin" hidden
        onChange=${(e) => { if (e.target.files[0]) fromFile(e.target.files[0]); e.target.value = ""; }} />
      <form class="row grow" onSubmit=${fromUrl}>
        <label class="stack">Ссылка на GitHub
          <input value=${url} placeholder="https://github.com/автор/дополнение"
            onInput=${(e) => setUrl(e.target.value)} /></label>
        <button type="submit" disabled=${installing || !url.trim()}>${installing ? "Устанавливаю…" : "Установить по ссылке"}</button>
      </form>
    </div>
    ${data.plugins.length === 0 && html`<${EmptyNote} title="Дополнений пока нет"
      hint="Установите первое из файла или по ссылке на GitHub." />`}
    <ul class="list plugin-list">
      ${data.plugins.map((p) => html`<${PluginCard} key=${p.id} plugin=${p} spending=${spending}
        onChanged=${load} onError=${onError} />`)}
    </ul>
  </section>`;
}

export function PluginAsks({ asks, onError }) {
  const first = asks[0];
  const answer = (allow) => api.post(`/plugins/asks/${first.id}`, { allow }).catch((e) => onError(e.message));
  return html`<div class="overlay">
    <div class="dialog" data-slot="plugin-ask" role="alertdialog" aria-modal="true" aria-label="Запрос дополнения">
      <p class="ask-text">Дополнение «${first.name}» хочет обратиться к модели</p>
      <p class="muted">Это один запрос за ваш счёт. Спрашивать каждый раз можно перестать в настройках → «Дополнения».
        ${asks.length > 1 ? ` Ждут ответа: ${asks.length}.` : ""}</p>
      <div class="row">
        <button class="primary" onClick=${() => answer(true)}>Разрешить</button>
        <button onClick=${() => answer(false)}>Запретить</button>
      </div>
    </div>
  </div>`;
}
