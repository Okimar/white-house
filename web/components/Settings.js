// Settings dialog, in tabs: your name, API connections (Connections.js), templates, spending (Spending.js),
// prompt, lorebooks, themes, pictures, plugins (Plugins.js), backups and the doctor (Backups.js).
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { Themes } from "./Themes.js";
import { Gallery } from "./Gallery.js";
import { Presets } from "./Presets.js";
import { LorebookSettings } from "./Lorebooks.js";
import { Connections } from "./Connections.js";
import { Templates } from "./Templates.js";
import { Skills } from "./Skills.js";
import { SpendingSettings } from "./Spending.js";
import { Plugins } from "./Plugins.js";
import { BackupsTab } from "./Backups.js";
import { IconButton } from "./Icon.js";
import { backdrop, useEscape } from "../core/keys.js";
import { notify } from "../core/notify.js";
import { DEFAULT_PINNED, MESSAGE_ACTIONS } from "../core/messageActions.js";

const TABS = [["main", "Основное"], ["connections", "Подключения"], ["templates", "Шаблоны"], ["spending", "Расход"], ["prompt", "Промпт"],
  ["lore", "Лорбуки"], ["look", "Оформление"], ["pictures", "Картинки"], ["plugins", "Дополнения"], ["skills", "Инструкции"], ["backups", "Копии"]];

export function Settings({ settings, themes, deletedThemes, characters, initialTab, onClose, onChanged, onError }) {
  const [tab, setTab] = useState(initialTab || "main");
  const [connections, setConnections] = useState([]); // for the pictures tab
  const [userName, setUserName] = useState(settings.user_name || "");

  const reload = () => api.get("/connections").then((d) => setConnections(d.connections))
    .catch((e) => onError(e.message));
  useEffect(() => { reload(); }, [tab]);

  const saveSetting = async (patch) => {
    try {
      await api.put("/settings", patch);
      notify("Сохранено", "ok");
    } catch (e) {
      onError(e.message);
    }
    onChanged();
  };
  const saveName = () => saveSetting({ user_name: userName.trim() });
  const saveDefaultMe = (id) => saveSetting({ default_me: id });

  const pinned = settings.message_pinned || DEFAULT_PINNED;
  const togglePinned = (id, on) => saveSetting({
    message_pinned: on ? [...pinned, id] : pinned.filter((x) => x !== id),
  });

  useEscape(onClose);
  // The chosen tab stays in view: on a phone the strip scrolls sideways.
  const strip = useRef(null);
  useEffect(() => {
    const active = strip.current && strip.current.querySelector("[aria-selected='true']");
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest", inline: "center" });
  }, [tab]);

  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog settings-dialog" data-slot="dialog" role="dialog" aria-modal="true"
        aria-label="Настройки">
        <div class="side-head"><strong class="dialog-title">Настройки</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        <div class="tabs" role="tablist" ref=${strip}>
          ${TABS.map(([id, label]) => html`<button key=${id} role="tab" aria-selected=${tab === id}
            onClick=${() => setTab(id)}>${label}</button>`)}
        </div>
        <div class="settings-body">

        ${tab === "main" && html`
        <h2>Кнопки сообщения</h2>
        <select value=${settings.message_actions || "menu"} aria-label="Как показывать кнопки сообщения"
          onChange=${(e) => saveSetting({ message_actions: e.target.value })}>
          <option value="menu">В выпадающем списке сбоку</option>
          <option value="always">Все всегда на панели</option>
        </select>
        <p class="hint">Отмеченные кнопки всегда на панели сообщения, остальные в списке.</p>
        <div class="check-grid">
          ${MESSAGE_ACTIONS.map((a) => html`<label key=${a.id}><input type="checkbox" checked=${pinned.includes(a.id)}
            onChange=${(e) => togglePinned(a.id, e.target.checked)} /> ${a.label}</label>`)}
        </div>
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
        <h2>Размышления модели</h2>
        <select value=${settings.thinking_display || "folded"} aria-label="Как показывать размышления модели"
          onChange=${(e) => saveSetting({ thinking_display: e.target.value })}>
          <option value="folded">Свёрнуты, открываются по нажатию</option>
          <option value="open">Развёрнуты</option>
          <option value="hidden">Скрыты</option>
        </select>
        <p class="hint">Отправлять ли прошлые размышления модели, настраивается в пресете: блок «История чата».</p>
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
          сохранять историю изменений (кнопка «Отменить» и версии)</label>`}

        ${tab === "prompt" && html`<${Presets} settings=${settings} onChanged=${onChanged} onError=${onError} />`}
        ${tab === "lore" && html`<${LorebookSettings} onChanged=${onChanged} onError=${onError} />`}

        ${tab === "look" && html`<${Themes} themes=${themes} deleted=${deletedThemes} settings=${settings}
          characters=${characters} onChanged=${onChanged} onError=${onError} />`}

        ${tab === "pictures" && html`<${Gallery} settings=${settings} connections=${connections}
          onChanged=${onChanged} onError=${onError} />`}

        ${tab === "connections" && html`<${Connections} onChanged=${onChanged} onError=${onError} />`}
        ${tab === "templates" && html`<${Templates} onError=${onError} />`}
        ${tab === "skills" && html`<${Skills} onError=${onError} />`}
        ${tab === "spending" && html`<${SpendingSettings} onError=${onError} />`}
        ${tab === "plugins" && html`<${Plugins} onError=${onError} />`}
        ${tab === "backups" && html`<${BackupsTab} onError=${onError} />`}
        </div>
      </div>
    </div>
  `;
}
