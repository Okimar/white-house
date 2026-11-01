// Settings dialog, in tabs: your name, API connections (Connections.js), templates, spending (Spending.js),
// prompt, lorebooks, themes, pictures, plugins (Plugins.js).
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { Themes } from "./Themes.js";
import { Gallery } from "./Gallery.js";
import { Presets } from "./Presets.js";
import { LorebookSettings } from "./Lorebooks.js";
import { Connections } from "./Connections.js";
import { Templates } from "./Templates.js";
import { SpendingSettings } from "./Spending.js";
import { Plugins } from "./Plugins.js";

const TABS = [["main", "Основное"], ["connections", "Подключения"], ["templates", "Шаблоны"], ["spending", "Расход"], ["prompt", "Промпт"],
  ["lore", "Лорбуки"], ["look", "Оформление"], ["pictures", "Картинки"], ["plugins", "Дополнения"]];

export function Settings({ settings, themes, deletedThemes, characters, initialTab, onClose, onChanged, onError }) {
  const [tab, setTab] = useState(initialTab || "main");
  const [connections, setConnections] = useState([]); // for the pictures tab
  const [userName, setUserName] = useState(settings.user_name || "");

  const reload = () => api.get("/connections").then((d) => setConnections(d.connections))
    .catch((e) => onError(e.message));
  useEffect(() => { reload(); }, [tab]);

  const saveName = async () => {
    await api.put("/settings", { user_name: userName.trim() }).catch((e) => onError(e.message));
    onChanged();
  };

  const saveDefaultMe = async (id) => {
    await api.put("/settings", { default_me: id }).catch((e) => onError(e.message));
    onChanged();
  };

  const saveSetting = async (patch) => {
    await api.put("/settings", patch).catch((e) => onError(e.message));
    onChanged();
  };

  return html`
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class=${"dialog" + (["connections", "templates", "spending", "plugins"].includes(tab) ? " wide" : "")} data-slot="dialog" role="dialog" aria-modal="true"
        aria-label="Настройки">
        <div class="side-head"><strong>Настройки</strong><span class="spacer"></span>
          <button onClick=${onClose} title="Закрыть">✕</button></div>
        <div class="tabs" role="tablist">
          ${TABS.map(([id, label]) => html`<button key=${id} role="tab" aria-selected=${tab === id}
            onClick=${() => setTab(id)}>${label}</button>`)}
        </div>

        ${tab === "main" && html`
        <h2>Мой персонаж по умолчанию</h2>
        <p class="muted">Входит в каждый новый чат с пометкой «это я». Его имя подставляется вместо {{user}}.</p>
        <select value=${settings.default_me || ""} onChange=${(e) => saveDefaultMe(e.target.value)}>
          <option value="">— нет —</option>
          ${characters.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
        </select>
        <h2>Имя, если в чате нет «моего» персонажа</h2>
        <div class="row">
          <input value=${userName} placeholder="User" onInput=${(e) => setUserName(e.target.value)} />
          <button onClick=${saveName}>Сохранить</button>
        </div>
        <h2>Заметки возле сообщения</h2>
        <select value=${settings.notes_display || "few"} aria-label="Сколько заметок показывать возле сообщения"
          onChange=${(e) => saveSetting({ notes_display: e.target.value })}>
          <option value="few">Две последние и «ещё N»</option>
          <option value="all">Все подряд</option>
        </select>
        <h2>Удаление</h2>
        <label><input type="checkbox" checked=${Boolean(settings.skip_delete_confirm)}
          onChange=${(e) => saveSetting({ skip_delete_confirm: e.target.checked })} />
          не спрашивать подтверждение при удалении пузырей</label>
        <h2>Новые пузыри</h2>
        <label><input type="checkbox" checked=${Boolean(settings.open_new_bubble)}
          onChange=${(e) => saveSetting({ open_new_bubble: e.target.checked })} />
          сразу открывать созданный пузырь (иначе остаётся открытым тот, где вы были)</label>
        <h2>История персонажей</h2>
        <label><input type="checkbox" checked=${settings.character_history !== false}
          onChange=${(e) => saveSetting({ character_history: e.target.checked })} />
          сохранять историю изменений (кнопка «↶ Отменить» и версии)</label>`}

        ${tab === "prompt" && html`<${Presets} settings=${settings} onChanged=${onChanged} onError=${onError} />`}
        ${tab === "lore" && html`<${LorebookSettings} onChanged=${onChanged} onError=${onError} />`}

        ${tab === "look" && html`<${Themes} themes=${themes} deleted=${deletedThemes} settings=${settings}
          characters=${characters} onChanged=${onChanged} onError=${onError} />`}

        ${tab === "pictures" && html`<${Gallery} settings=${settings} connections=${connections}
          onChanged=${onChanged} onError=${onError} />`}

        ${tab === "connections" && html`<${Connections} onChanged=${onChanged} onError=${onError} />`}
        ${tab === "templates" && html`<${Templates} onError=${onError} />`}
        ${tab === "spending" && html`<${SpendingSettings} onError=${onError} />`}
        ${tab === "plugins" && html`<${Plugins} onError=${onError} />`}
      </div>
    </div>
  `;
}
