// Settings dialog, in tabs: your name, API connections (Connections.js; instruct templates live inside, for text mode), spending (Spending.js),
// prompt, lorebooks, themes, pictures, plugins (Plugins.js), backups and the doctor (Backups.js).
import { Select } from "./Select.js";
import { TextColors } from "./TextColors.js";
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { Themes } from "./Themes.js";
import { Gallery } from "./Gallery.js";
import { Presets } from "./Presets.js";
import { LorebookSettings } from "./Lorebooks.js";
import { Connections } from "./Connections.js";
import { Skills } from "./Skills.js";
import { RegexTab } from "./Regex.js";
import { SpendingSettings } from "./Spending.js";
import { Plugins } from "./Plugins.js";
import { BackupsTab } from "./Backups.js";
import { Changes } from "./Changes.js";
import { Updates } from "./Updates.js";
import { IconButton } from "./Icon.js";
import { backdrop, useEscape } from "../core/keys.js";
import { notify } from "../core/notify.js";
import { SKIP_ASKS, setSkip, syncSkips } from "./Dialogs.js";
import { MAX_SHOWN, setNotesShown, syncNotesShown } from "../core/notesShown.js";
import { useLayout, setMenuMode } from "../core/layout.js";
import { DEFAULT_PINNED, MESSAGE_ACTIONS } from "../core/messageActions.js";

const TABS = [["main", "Основное"], ["connections", "Подключения"], ["spending", "Расход"], ["prompt", "Промпт"],
  ["lore", "Лорбуки"], ["look", "Оформление"], ["regex", "Регекс"], ["pictures", "Картинки"], ["plugins", "Дополнения"], ["skills", "Инструкции"], ["changes", "Изменения"], ["backups", "Копии"]];

// Where Settings was closed (tab and scroll of each tab) is kept on this device, only as a convenience.
const VIEW_KEY = "tavern.settingsView";
function loadView() {
  try {
    const v = JSON.parse(localStorage.getItem(VIEW_KEY) || "{}");
    return { tab: TABS.some(([id]) => id === v.tab) ? v.tab : null, scroll: v.scroll && typeof v.scroll === "object" ? v.scroll : {} };
  } catch (e) { return { tab: null, scroll: {} }; }
}
function saveView(view) {
  try { localStorage.setItem(VIEW_KEY, JSON.stringify(view)); } catch (e) { /* storage off */ }
}

export function Settings({ settings, themes, deletedThemes, characters, initialTab, onClose, onChanged, onError, onOpenAssistant }) {
  const saved = useRef(null);
  if (saved.current === null) saved.current = loadView();
  const [tab, setTab] = useState(initialTab || saved.current.tab || "main");
  const body = useRef(null);
  const view = useRef({ tab, scroll: { ...saved.current.scroll } });
  const restoring = useRef(!initialTab); // a tab was just switched: scroll events are not the user's yet
  const chooseTab = (id) => { restoring.current = true; setTab(id); view.current.tab = id; saveView(view.current); };
  useEffect(() => { view.current.tab = tab; saveView(view.current); }, []);
  const layout = useLayout(); // menu on top / at the side: a choice of this device
  const [connections, setConnections] = useState([]); // for the pictures tab
  const [userName, setUserName] = useState(settings.user_name || "");

  const reload = () => api.get("/connections").then((d) => setConnections(d.connections))
    .catch((e) => onError(e.message));
  useEffect(() => { reload(); }, [tab]);

  syncSkips(settings.skip_confirms); // the delete questions turned off (Dialogs.js) follow the saved setting
  syncNotesShown(settings.notes_shown);
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
  // Back to the scroll place of this tab; content may arrive late, so try a few times until it fits.
  useEffect(() => {
    const want = view.current.scroll[tab] || 0;
    const el = body.current;
    if (!el || !want || (initialTab === tab && !restoring.current)) {
      if (el && !want) el.scrollTop = 0;
      restoring.current = false;
      return;
    }
    let tries = 0;
    const timer = setInterval(() => {
      el.scrollTop = want;
      if (Math.abs(el.scrollTop - want) < 2 || ++tries > 15) { clearInterval(timer); restoring.current = false; }
    }, 80);
    const stop = () => { clearInterval(timer); restoring.current = false; };
    el.addEventListener("wheel", stop, { once: true });
    el.addEventListener("touchstart", stop, { once: true });
    return () => { clearInterval(timer); el.removeEventListener("wheel", stop); el.removeEventListener("touchstart", stop); };
  }, [tab]);
  let scrollSave = 0;
  const onScroll = (e) => {
    if (restoring.current) return;
    const y = e.currentTarget.scrollTop;
    view.current.scroll[tab] = Math.round(y);
    clearTimeout(scrollSave);
    scrollSave = setTimeout(() => saveView(view.current), 150);
  };
  // The chosen tab stays in view: on a phone the strip scrolls sideways.
  const strip = useRef(null);
  useEffect(() => {
    const active = strip.current && strip.current.querySelector("[aria-selected='true']");
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest", inline: "center" });
  }, [tab]);

  // Keyboard navigation for tablist (WAI-ARIA tabs pattern: Left/Right, Home/End)
  const onKeyDownTabs = (e) => {
    const idx = TABS.findIndex(([id]) => id === tab);
    if (idx === -1) return;
    let nextIdx = -1;
    if (e.key === "ArrowRight") {
      nextIdx = (idx + 1) % TABS.length;
    } else if (e.key === "ArrowLeft") {
      nextIdx = (idx - 1 + TABS.length) % TABS.length;
    } else if (e.key === "Home") {
      nextIdx = 0;
    } else if (e.key === "End") {
      nextIdx = TABS.length - 1;
    }
    if (nextIdx !== -1) {
      e.preventDefault();
      const [nextId] = TABS[nextIdx];
      chooseTab(nextId);
      setTimeout(() => {
        const btn = strip.current && strip.current.querySelector(`[data-tab="${nextId}"]`);
        if (btn && btn.focus) btn.focus();
      }, 0);
    }
  };

  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog settings-dialog" data-slot="dialog" role="dialog" aria-modal="true"
        aria-label="Настройки">
        <div class="side-head"><strong class="dialog-title">Настройки</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        <div class="tabs settings-tabs" role="tablist" aria-label="Разделы настроек" ref=${strip} onKeyDown=${onKeyDownTabs}>
          ${TABS.map(([id, label]) => html`<button key=${id} role="tab" id=${"settings-tab-" + id}
            data-tab=${id} aria-selected=${tab === id} aria-controls=${"settings-panel-" + id}
            tabIndex=${tab === id ? 0 : -1}
            onClick=${() => chooseTab(id)}>${label}</button>`)}
        </div>
        <div class="settings-body" ref=${body} onScroll=${onScroll}
          role="tabpanel" id=${"settings-panel-" + tab} aria-labelledby=${"settings-tab-" + tab} tabIndex="0">

        ${tab === "main" && html`
        <section class="settings-section stack" data-slot="settings-section">
          <h2>Интерфейс</h2>
          <label class="stack">Где показывать меню
            <${Select} value=${layout.mode} aria-label="Где показывать меню" data-slot="menu-mode"
              aria-describedby="hint-menu-mode"
              onChange=${(e) => setMenuMode(e.target.value)}>
              <option value="top">Сверху: значки, списки выпадают</option>
              <option value="side">Сбоку: панель слева</option>
            </${Select}>
          </label>
          <p id="hint-menu-mode" class="hint">Выбор действует на этом устройстве. Меню можно свернуть кнопкой в левом верхнем углу, чтобы чату хватило места.</p>

          <label class="stack">Кнопки сообщения
            <${Select} value=${settings.message_actions || "menu"} aria-label="Как показывать кнопки сообщения"
              aria-describedby="hint-message-actions"
              onChange=${(e) => saveSetting({ message_actions: e.target.value })}>
              <option value="menu">В выпадающем списке сбоку</option>
              <option value="always">Все всегда на панели</option>
            </${Select}>
          </label>
          <p id="hint-message-actions" class="hint">Отмеченные кнопки всегда на панели сообщения, остальные в списке.</p>
          <div class="check-grid">
            ${MESSAGE_ACTIONS.map((a) => html`<label key=${a.id}><input type="checkbox" checked=${pinned.includes(a.id)}
              onChange=${(e) => togglePinned(a.id, e.target.checked)} /> ${a.label}</label>`)}
          </div>

          <label class="stack">Плотность чата
            <${Select} value=${settings.chat_density || "compact"} aria-label="Плотность текста в чате"
              aria-describedby="hint-chat-density" onChange=${(e) => saveSetting({ chat_density: e.target.value })}>
              <option value="compact">Плотно, как в SillyTavern</option>
              <option value="roomy">Просторно: крупный шрифт и отступы</option>
            </${Select}>
          </label>
          <p id="hint-chat-density" class="hint">Плотно — больше текста на экране: шрифт поменьше, строки ближе, между абзацами небольшой зазор.</p>

          <label class="stack">Размер текста в чате
            <${Select} value=${String(settings.chat_font_size || "")} aria-label="Размер текста сообщений"
              aria-describedby="hint-chat-font" onChange=${(e) => saveSetting({ chat_font_size: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Как у плотности (${settings.chat_density === "roomy" ? 17 : 16} px)</option>
              ${[13, 14, 15, 16, 17, 18, 19, 20, 22, 24, 26].map((n) => html`<option key=${n} value=${String(n)}>${n} px</option>`)}
            </${Select}>
          </label>
          <p id="hint-chat-font" class="hint">Только текст сообщений и размышлений. Кнопки и меню не меняются.</p>

          <label class="stack">Размышления модели
            <${Select} value=${settings.thinking_display || "folded"} aria-label="Как показывать размышления модели"
              aria-describedby="hint-thinking-display"
              onChange=${(e) => saveSetting({ thinking_display: e.target.value })}>
              <option value="folded">Свёрнуты, открываются по нажатию</option>
              <option value="open">Развёрнуты</option>
              <option value="hidden">Скрыты</option>
            </${Select}>
          </label>
          <p id="hint-thinking-display" class="hint">Отправлять ли прошлые размышления модели, настраивается в пресете: блок «История чата».</p>

          <h3 class="settings-sub">Цветной текст</h3>
          <${TextColors} settings=${settings} saveSetting=${saveSetting} />
          <p class="hint">Текст между метками красится в выбранный цвет во всех сообщениях. Например, кавычки " " красят прямую речь, звёздочки * * — действия. Модели уходит текст как есть.</p>

          <label class="stack">Заметки возле сообщения
            <${Select} value=${settings.notes_display || "few"} aria-label="Сколько заметок показывать возле сообщения"
              aria-describedby="hint-notes-display"
              onChange=${(e) => saveSetting({ notes_display: e.target.value })}>
              <option value="few">Только последние, остальные по кнопке «Все заметки»</option>
              <option value="all">Все подряд</option>
            </${Select}>
          </label>
          ${(settings.notes_display || "few") === "few" && html`
            <label class="stack">Сколько последних заметок показывать
              <input type="number" min="1" max=${MAX_SHOWN} value=${settings.notes_shown || 2}
                aria-label="Количество последних заметок возле сообщения"
                aria-describedby="hint-notes-display"
                onChange=${(e) => setNotesShown(e.target.value).then(onChanged).catch((err) => onError(err.message))} /></label>
          `}
          <p id="hint-notes-display" class="hint">Возле сообщения видны только последние заметки, под ними строка «Видны последние N из M». Число можно менять и прямо на этой строке.</p>
        </section>

        <section class="settings-section stack" data-slot="settings-section">
          <h2>Персонажи и вы</h2>
          <label class="stack">Мой персонаж по умолчанию
            <${Select} value=${settings.default_me || ""} aria-label="Мой персонаж по умолчанию"
              aria-describedby="hint-default-me"
              onChange=${(e) => saveDefaultMe(e.target.value)}>
              <option value="">— нет —</option>
              ${characters.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
            </${Select}>
          </label>
          <p id="hint-default-me" class="hint">Входит в каждый новый чат с пометкой «это я». Его имя подставляется вместо {{user}}.</p>

          <label class="stack">Имя, если в чате нет «моего» персонажа
            <div class="row">
              <input class="grow" value=${userName} placeholder="User" aria-label="Имя пользователя по умолчанию"
                aria-describedby="hint-default-user-name"
                onInput=${(e) => setUserName(e.target.value)}
                onKeyDown=${(e) => { if (e.key === "Enter" && userName.trim() !== (settings.user_name || "")) saveName(); }} />
              <button type="button" class=${userName.trim() !== (settings.user_name || "") ? "primary" : ""}
                disabled=${userName.trim() === (settings.user_name || "")}
                aria-label="Сохранить имя пользователя по умолчанию"
                onClick=${saveName}>Сохранить</button>
            </div>
          </label>
          <p id="hint-default-user-name" class="hint">Подставляется в промпт вместо {{user}}, если чат ведётся без карточки «это я».</p>

          <label class="check"><input type="checkbox" checked=${Boolean(settings.open_new_bubble)}
            onChange=${(e) => saveSetting({ open_new_bubble: e.target.checked })} />
            сразу открывать созданный пузырь (иначе остаётся открытым тот, где вы были)</label>

          <label class="check"><input type="checkbox" checked=${settings.character_history !== false}
            onChange=${(e) => saveSetting({ character_history: e.target.checked })} />
            сохранять историю изменений (кнопка «Отменить» и версии)</label>
        </section>

        <section class="settings-section stack" data-slot="settings-section">
          <h2>Удаление и подтверждения</h2>
          <p id="hint-delete-confirms" class="hint">Вопрос «Удалить?» с галочкой «Больше не спрашивать» можно отключить в самом окне. Здесь его можно вернуть. Персонажи, чаты, лорбуки и копии спрашивают всегда.</p>
          <label class="check"><input type="checkbox" checked=${!settings.skip_delete_confirm}
            onChange=${(e) => saveSetting({ skip_delete_confirm: !e.target.checked })} />
            спрашивать при удалении пузырей</label>
          <details class="settings-details">
            <summary>Другие подтверждения (${SKIP_ASKS.length})</summary>
            <div class="stack" style="margin-top: 8px;">
              ${SKIP_ASKS.map(([key, label]) => html`<label key=${key} class="check"><input type="checkbox"
                checked=${!(settings.skip_confirms || {})[key]}
                onChange=${(e) => setSkip(key, !e.target.checked).then(onChanged).catch((err) => onError(err.message))} />
                спрашивать: ${label[0].toLowerCase() + label.slice(1)}</label>`)}
            </div>
          </details>
        </section>

        <section class="settings-section stack" data-slot="settings-section">
          <${Updates} onError=${onError} />
        </section>`}

        ${tab === "prompt" && html`<${Presets} settings=${settings} onChanged=${onChanged} onError=${onError} />`}
        ${tab === "lore" && html`<${LorebookSettings} settings=${settings} onChanged=${onChanged} onError=${onError} />`}

        ${tab === "look" && html`<${Themes} themes=${themes} deleted=${deletedThemes} settings=${settings}
          characters=${characters} onChanged=${onChanged} onError=${onError} />`}

        ${tab === "pictures" && html`<${Gallery} settings=${settings} connections=${connections}
          onChanged=${onChanged} onError=${onError} />`}

        ${tab === "connections" && html`<${Connections} onChanged=${onChanged} onError=${onError} />`}
        ${tab === "regex" && html`<${RegexTab} onError=${onError} settings=${settings} onSave=${saveSetting} />`}
        ${tab === "skills" && html`<${Skills} onError=${onError} />`}
        ${tab === "spending" && html`<${SpendingSettings} onError=${onError} />`}
        ${tab === "plugins" && html`<${Plugins} onError=${onError} />`}
        ${tab === "changes" && html`<${Changes} onError=${onError} onOpenConversation=${onOpenAssistant} onChanged=${onChanged} />`}
        ${tab === "backups" && html`<${BackupsTab} onError=${onError} />`}
        </div>
      </div>
    </div>
  `;
}
