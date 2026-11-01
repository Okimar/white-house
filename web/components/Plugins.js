// Settings tab "Дополнения": installed plugins with their switch, what each says it does, its
// settings, model access (allowed at all, its own limits, ask each time) and spending; install from
// a file or a GitHub link, check for updates, delete. A plugin that is not built in is switched on
// only after the full-access warning (ApproveDialog). `PluginAsks` is the "may it call a model?"
// window shown over the app while a plugin waits for an answer.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { money } from "../core/spending.js";

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
  return html`<div class="overlay" onClick=${(e) => e.target === e.currentTarget && onNo()}>
    <div class="dialog" data-slot="dialog" role="alertdialog" aria-modal="true" aria-label="Включить дополнение">
      <h2>Включить «${plugin.name}»?</h2>
      <p class="muted">${plugin.author ? `Автор: ${plugin.author}. ` : ""}Версия ${plugin.version}.
        ${plugin.source.url ? html` Источник: ${plugin.source.url}` : ""}</p>
      ${plugin.uses.length > 0 && html`<p>Оно заявляет о себе, что:</p>
        <ul>${plugin.uses.map((u) => html`<li key=${u}>${USES[u] || u}</li>`)}</ul>`}
      <p class="warn" data-slot="plugin-warning">⚠ Дополнение получит полный доступ к Tavern и к файлам на этом
        устройстве, в том числе к вашим ключам. Список выше — только его слова. Ставьте дополнения
        только из мест, которым доверяете.</p>
      <div class="row">
        <button class="primary" onClick=${onYes}>Включить</button>
        <button onClick=${onNo}>Отмена</button>
      </div>
    </div>
  </div>`;
}

function SettingField({ field, value, onSave }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
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
  const remove = () => {
    if (!confirm(`Удалить дополнение «${plugin.name}»?`)) return;
    const data = plugin.data_count > 0 && confirm(`Удалить и его данные в чатах (записей: ${plugin.data_count})?`);
    run(() => api.del(`/plugins/${plugin.id}${data ? "?data=1" : ""}`));
  };
  const github = plugin.source.kind === "github";
  return html`<li class="plugin-card" data-slot="plugin-card" data-plugin=${plugin.id}
    data-enabled=${plugin.enabled ? "true" : "false"}>
    <div class="row">
      <label class="plugin-switch"><input type="checkbox" checked=${plugin.enabled} disabled=${busy}
        aria-label=${"Включить " + plugin.name} onChange=${(e) => toggle(e.target.checked)} />
        <strong>${plugin.name}</strong></label>
      <span class="muted">${plugin.version} · ${SOURCE[plugin.source.kind] || plugin.source.kind}
        ${plugin.author ? ` · ${plugin.author}` : ""}</span>
      <span class="spacer"></span>
      <button aria-expanded=${open} onClick=${() => setOpen(!open)}>${open ? "Свернуть" : "Подробнее"}</button>
    </div>
    ${plugin.error && html`<p class="error" data-slot="plugin-error">${plugin.error}</p>`}
    ${plugin.server_off && plugin.enabled && html`<p class="muted">Запущено ключом --no-plugins: серверная часть не работает.</p>`}
    ${open && html`<div class="plugin-more">
      ${plugin.description && html`<p>${plugin.description}</p>`}
      ${plugin.uses.length > 0 && html`<p class="muted">Заявляет: ${plugin.uses.map((u) => USES[u] || u).join("; ")}.</p>`}
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
          : html`<span class="muted">Новее нет.</span>`)}
        <span class="spacer"></span>
        ${!plugin.builtin && html`<button class="danger" disabled=${busy} onClick=${remove}>Удалить</button>`}
      </div>
    </div>`}
    ${approving && html`<${ApproveDialog} plugin=${plugin} onYes=${approve} onNo=${() => setApproving(false)} />`}
  </li>`;
}

export function Plugins({ onError }) {
  const [data, setData] = useState(null);
  const [spending, setSpending] = useState(null);
  const [url, setUrl] = useState("");
  const [installing, setInstalling] = useState(false);
  const picker = useRef(null);

  const load = () => {
    api.get("/plugins").then(setData).catch((e) => onError(e.message));
    api.get("/spending/settings").then(setSpending).catch(() => {});
  };
  useEffect(load, []);

  // Installing over an installed plugin asks first, then repeats with replace.
  const install = async (send) => {
    setInstalling(true);
    try {
      try {
        await send(false);
      } catch (e) {
        if (e.code !== "plugin_exists") throw e;
        const d = e.details || {};
        if (!confirm(`Дополнение уже установлено (версия ${d.version} → ${d.new_version}). Заменить? ` +
          "Оно выключится до вашего подтверждения.")) return;
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

  if (!data) return html`<p class="muted">Загрузка…</p>`;
  return html`<section class="plugins" data-slot="plugins">
    <p class="muted">Дополнения добавляют в Tavern новое: команды, кнопки, свои способы писать ответы.
      Новое дополнение появляется выключенным. Положить его можно и вручную, папкой в data/plugins.</p>
    ${data.server_off && html`<p class="warn">Tavern запущен с ключом --no-plugins: дополнения не работают.</p>`}
    <div class="row">
      <button disabled=${installing} onClick=${() => picker.current && picker.current.click()}>Установить из файла</button>
      <input ref=${picker} type="file" accept=".zip,.tavern-plugin" hidden
        onChange=${(e) => { if (e.target.files[0]) fromFile(e.target.files[0]); e.target.value = ""; }} />
      <form class="row grow" onSubmit=${fromUrl}>
        <input value=${url} placeholder="https://github.com/автор/дополнение" aria-label="Ссылка на GitHub"
          onInput=${(e) => setUrl(e.target.value)} />
        <button type="submit" disabled=${installing || !url.trim()}>${installing ? "Ставлю…" : "Установить по ссылке"}</button>
      </form>
    </div>
    <ul class="list plugin-list">
      ${data.plugins.map((p) => html`<${PluginCard} key=${p.id} plugin=${p} spending=${spending}
        onChanged=${load} onError=${onError} />`)}
    </ul>
  </section>`;
}

export function PluginAsks({ asks, onError }) {
  const ask = asks[0];
  const answer = (allow) => api.post(`/plugins/asks/${ask.id}`, { allow }).catch((e) => onError(e.message));
  return html`<div class="overlay">
    <div class="dialog" data-slot="plugin-ask" role="alertdialog" aria-modal="true" aria-label="Запрос дополнения">
      <h2>Дополнение «${ask.name}» хочет обратиться к модели</h2>
      <p class="muted">Это один запрос за ваш счёт. Спрашивать каждый раз можно перестать в настройках → «Дополнения».
        ${asks.length > 1 ? ` Ждут ответа: ${asks.length}.` : ""}</p>
      <div class="row">
        <button class="primary" onClick=${() => answer(true)}>Разрешить</button>
        <button onClick=${() => answer(false)}>Запретить</button>
      </div>
    </div>
  </div>`;
}
