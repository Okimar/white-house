// "Оформление" tab: pick a theme, copy, share as a file, delete, roll back to an earlier version,
// allow a theme's effects script, and give characters their own theme.
import { Select } from "./Select.js";
import { html, useState, useRef, useEffect } from "../vendor/preact-htm.mjs";
import { api, ApiError } from "../core/api.js";
import { IconButton } from "./Icon.js";
import { ask, askText } from "./Dialogs.js";
import { notify } from "../core/notify.js";
import { SAFE_MODE, reloadThemeCss } from "../core/theme.js";
import { toHex, contrast, isHex } from "../core/colors.js";

export function scriptAllowed(theme, settings) {
  if (!theme || !theme.script) return false;
  return theme.builtin || (settings.trusted_theme_scripts || {})[theme.id] === theme.script_sha256;
}

function ThemeCard({ theme, active, onPick, onCopy, onDelete, onVersions }) {
  const preview = theme.preview && `/themes/${theme.id}/${theme.preview}`;
  return html`
    <div class=${"theme-card" + (active ? " active" : "") + (theme.error ? " broken" : "")} data-slot="theme-card">
      ${preview
        ? html`<img class="preview" src=${preview} alt="" loading="lazy" />`
        : html`<div class="preview preview-sample" aria-hidden="true"><span>${theme.name}</span></div>`}
      <strong>${theme.name}</strong>
      <span class="muted">${[theme.author, theme.version && "v" + theme.version].filter(Boolean).join(" · ")}</span>
      <span class="row">
        ${active && html`<span class="badge active-badge">активна</span>`}
        ${theme.builtin && html`<span class="badge">встроенная</span>`}
        ${theme.script && html`<span class="badge">эффекты</span>`}
        ${theme.error && html`<span class="badge danger-badge">сломана</span>`}
      </span>
      ${theme.script && html`<span class="hint">Эффекты: тема запускает свой скрипт, например анимации</span>`}
      ${theme.error && html`<span class="hint danger-text">Не работает: ${theme.error}</span>`}
      <div class="row">
        <button class=${active ? "primary active-btn" : ""} disabled=${!!theme.error}
          aria-pressed=${active}
          aria-label=${(active ? "Текущая активная тема: " : "Выбрать тему ") + theme.name}
          onClick=${() => onPick(theme)}>
          ${active ? "✓ Выбрана" : "Выбрать"}</button>
        <button title="Сделать свою копию темы, чтобы менять"
          aria-label=${"Дублировать тему " + theme.name}
          disabled=${!!theme.error} onClick=${() => onCopy(theme)}>Дублировать</button>
        ${!theme.error && html`<a class="button" title="Сохранить тему одним файлом, чтобы поделиться"
          aria-label=${"Скачать тему " + theme.name}
          href=${`/api/themes/${theme.id}/export`}>Скачать</a>`}
        ${theme.versions > 0 && html`<button title="Вернуть прошлую версию"
          aria-label=${"История версий темы " + theme.name}
          onClick=${() => onVersions(theme)}>Версии</button>`}
        ${!theme.builtin && html`<${IconButton} icon="trash-2" label=${"Удалить тему " + theme.name}
          title="Удалить (можно вернуть через версии)"
          onClick=${() => onDelete(theme)} />`}
      </div>
    </div>`;
}

// The main colours of the look: [css variable, label, what it paints].
const COLORS = [
  ["bg", "Фон", "Фон приложения за чатом"],
  ["panel", "Панели", "Меню, окна, поле ввода, кнопки"],
  ["fg", "Текст", "Основной цвет текста"],
  ["muted", "Тихий текст", "Подсказки и подписи"],
  ["line", "Границы", "Тонкие линии между частями"],
  ["accent", "Акцент", "Главные кнопки и выделения"],
  ["accent-fg", "Текст на акценте", "Надписи на главных кнопках"],
  ["danger", "Опасное", "Удаление и ошибки"],
  ["success", "Успех", "Галочки и подтверждения"],
];
// Pairs that must stay readable: [text, background, what, minimum ratio].
const READABLE = [["fg", "bg", "Текст на фоне", 4.5], ["fg", "panel", "Текст на панелях", 4.5],
  ["muted", "bg", "Тихий текст на фоне", 3], ["accent-fg", "accent", "Надпись на главной кнопке", 4.5]];
const root = () => document.documentElement;
const readColors = () => Object.fromEntries(COLORS.map(([k]) => [k, toHex(getComputedStyle(root()).getPropertyValue("--" + k).trim())
  || toHex(getComputedStyle(root()).getPropertyValue("--" + k)) || "#000000"]));
const clearPreview = () => COLORS.forEach(([k]) => root().style.removeProperty("--" + k));

// «Цвета»: change the main colours at once (live), then keep them as your own theme. A built-in theme is
// never changed: saving makes a copy. Saving into your own theme keeps the old version («Версии» brings it back).
function ColorEditor({ theme, onSaved, onError }) {
  const [base, setBase] = useState(null); // colours of the look as it is saved
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  const mine = theme && !theme.builtin;
  const edited = useRef(false);
  useEffect(() => {
    edited.current = false;
    const read = () => {
      if (edited.current) return;
      clearPreview();
      const now = readColors();
      setBase(now); setDraft(now);
    };
    read();
    // the stylesheet of a just chosen or saved theme arrives a moment later: read again
    const link = document.getElementById("theme-css");
    const timer = setTimeout(read, 350);
    if (link) link.addEventListener("load", read);
    return () => { clearTimeout(timer); if (link) link.removeEventListener("load", read); clearPreview(); }; // leaving drops what was not saved
  }, [theme && theme.id, theme && JSON.stringify(theme.colors || {})]);
  if (!draft) return null;
  const changed = COLORS.filter(([k]) => draft[k] !== base[k]).map(([k]) => k);
  const set = (key, value) => {
    edited.current = true;
    setDraft((d) => ({ ...d, [key]: value }));
    if (isHex(value)) root().style.setProperty("--" + key, value);
  };
  const reset = () => { edited.current = false; clearPreview(); setDraft(base); };
  const bad = READABLE.filter(([a, b, , min]) => contrast(draft[a], draft[b]) < min);
  const save = async () => {
    setSaving(true);
    try {
      let id = theme.id;
      if (!mine) {
        const name = await askText("Название своей темы", { value: `${theme.name} (мои цвета)`, ok: "Создать",
          hint: "Встроенная тема не меняется: цвета сохранятся в её копии." });
        if (!name) { setSaving(false); return; }
        id = (await api.post("/themes", { name, from: theme.id })).id;
      }
      const colors = { ...(mine ? theme.colors : {}) };
      COLORS.forEach(([k]) => { if (!mine || draft[k] !== base[k]) colors[k] = draft[k]; });
      await api.put(`/themes/${id}/colors`, { colors });
      clearPreview();
      await onSaved(id, mine);
      notify("Цвета сохранены", "ok");
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  };
  const forget = async () => {
    if (!await ask("Убрать свои цвета темы?", { ok: "Убрать", hint: "Тема вернётся к своим прежним цветам. Прошлую версию можно вернуть кнопкой «Версии»." })) return;
    setSaving(true);
    try {
      await api.put(`/themes/${theme.id}/colors`, { colors: {} });
      clearPreview();
      await onSaved(theme.id, true);
      notify("Цвета сброшены к исходным", "info");
    } catch (e) {
      onError(e.message);
    } finally {
      setSaving(false);
    }
  };
  return html`
    <details class="colors-box" data-slot="theme-colors">
      <summary>Цвета</summary>
      <p class="hint">Измените основные цвета выбранной темы и сразу смотрите, как это выглядит. Сохранение создаёт вашу тему
        (встроенные не меняются). Если у темы есть картинка фона, цвет фона виден не везде.</p>
      <ul class="color-list">
        ${COLORS.map(([key, label, hint]) => {
          const valid = isHex(draft[key]);
          return html`<li key=${key} class=${"color-row" + (draft[key] !== base[key] ? " changed" : "")}>
            <input type="color" class="color-pick" value=${valid ? draft[key] : "#000000"} aria-label=${label}
              onInput=${(e) => set(key, e.target.value)} />
            <span class="color-name"><strong>${label}</strong><span class="hint">${hint}</span></span>
            <input class=${"color-hex mono" + (!valid ? " invalid" : "")} value=${draft[key]} maxlength="7"
              aria-label=${label + ", код цвета"} aria-invalid=${!valid} spellcheck="false" autocapitalize="off"
              onInput=${(e) => set(key, e.target.value.startsWith("#") ? e.target.value : "#" + e.target.value)} />
          </li>`;
        })}
      </ul>
      ${bad.length > 0 && html`<div class="notice" role="alert">
        <strong>Плохо читается</strong>
        <ul class="list-plain">${bad.map(([a, b, what]) => html`<li key=${a + b}>${what}: контраст ${contrast(draft[a], draft[b]).toFixed(1)}, нужен не меньше
          ${READABLE.find((r) => r[0] === a && r[1] === b)[3]}</li>`)}</ul>
        <span class="hint">Сохранить можно, но на телефоне текст будет трудно разобрать.</span></div>`}
      <div class="row">
        <button class="primary" disabled=${changed.length === 0 || saving} onClick=${save}>
          ${saving ? "Сохранение…" : (mine ? "Сохранить цвета" : "Сохранить как свою тему")}</button>
        <button disabled=${changed.length === 0 || saving} onClick=${reset}>Сбросить</button>
        ${mine && Object.keys(theme.colors || {}).length > 0 && html`<button class="danger-text" disabled=${saving} onClick=${forget}>Убрать сохранённые цвета</button>`}
      </div>
      ${changed.length > 0 && html`<span class="hint">Изменено параметров: ${changed.length}. Пока не сохранено, цвета видны только на этой странице.</span>`}
    </details>`;
}

// chatLook: the rest of how the chat looks (density, text size, coloured text, thinking, message buttons, notes,
// reading mode), given by Settings.js so the whole look is on this one tab, in the order the eye meets it.
export function Themes({ themes, deleted, settings, characters, chatLook = null, onChanged, onError }) {
  const [versions, setVersions] = useState(null); // { theme, list }
  const fileInput = useRef(null);
  const versionsBox = useRef(null);
  const current = settings.theme || "default";
  const byId = Object.fromEntries(themes.map((t) => [t.id, t]));
  const [parked, setParked] = useState(undefined); // undefined: not asked yet (the tab waits, so the notice does not push the themes down)
  const pending = byId[current];
  const needsApproval = pending && pending.script && !scriptAllowed(pending, settings);

  const save = async (patch) => {
    try {
      await api.put("/settings", patch);
      await onChanged();
    } catch (e) { onError(e.message); }
  };

  // What an update switched off (the old look): kept, and one action brings it back.
  useEffect(() => {
    api.get("/look").then((d) => setParked(d.parked ?? null)).catch(() => setParked(null));
  }, [settings.theme]);
  const restoreParked = async () => {
    if (!await ask("Вернуть прежнее оформление?", { ok: "Вернуть", danger: true,
      hint: "Оно писалось под старый вид приложения и может сломать меню. Если сломает: "
        + "откройте безопасный режим (/?safe или команда python -m tavern safe-mode on) и выберите стандартную тему." })) return;
    try {
      await api.post("/look/restore", {});
      setParked(null);
      await onChanged();
    } catch (e) { onError(e.message); }
  };

  // «Версии» opens below the whole grid: bring it into view so that the press visibly does something.
  useEffect(() => {
    if (versions && versionsBox.current) versionsBox.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [versions]);

  const pick = (theme) => save({ theme: theme.id === "default" ? "" : theme.id });

  const allowScript = (theme) => save({
    trusted_theme_scripts: { ...(settings.trusted_theme_scripts || {}), [theme.id]: theme.script_sha256 },
  });

  const copy = async (theme) => {
    const name = await askText("Название новой темы", { value: `${theme.name} (моя)`, ok: "Создать" });
    if (!name) return;
    try {
      const created = await api.post("/themes", { name, from: theme.id });
      await save({ theme: created.id });
    } catch (e) { onError(e.message); }
  };

  const remove = async (theme) => {
    if (!await ask(`Удалить тему «${theme.name}»?`, { ok: "Удалить", danger: true,
      hint: "Её можно будет вернуть в разделе «Удалённые темы»." })) return;
    try {
      await api.del(`/themes/${theme.id}`);
      await onChanged();
    } catch (e) { onError(e.message); }
  };

  const showVersions = async (theme) => {
    try {
      const { versions: list } = await api.get(`/themes/${theme.id}/versions`);
      setVersions({ theme, list });
    } catch (e) { onError(e.message); }
  };

  const restore = async (version) => {
    try {
      await api.post(`/themes/${versions.theme.id}/restore`, { version });
      setVersions(null);
      notify("Версия возвращена", "ok");
      await onChanged();
    } catch (e) { onError(e.message); }
  };

  const importFile = async (file, replace = false) => {
    try {
      const theme = await api.postBinary(`/themes/import${replace ? "?replace=1" : ""}`, file, "application/zip");
      if (await ask(`Тема «${theme.name}» установлена. Включить её сейчас?`, { ok: "Включить", cancel: "Не сейчас" })) await save({ theme: theme.id });
      else await onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.code === "theme_exists") {
        const { existing, incoming } = e.details || {};
        const old = existing && existing.version ? ` (версия ${existing.version})` : "";
        const neu = incoming && incoming.version ? ` версией ${incoming.version}` : " новой версией";
        if (await ask(`Тема «${existing ? existing.name : ""}»${old} уже установлена. Заменить её${neu}?`,
          { ok: "Заменить", hint: "Старая версия сохранится, её можно вернуть кнопкой «Версии»." })) {
          await importFile(file, true);
        }
        return;
      }
      onError(e.message);
    }
  };

  const setCharacterTheme = (characterId, themeId) => {
    const map = { ...(settings.character_themes || {}) };
    if (themeId) map[characterId] = themeId; else delete map[characterId];
    save({ character_themes: map });
  };

  if (parked === undefined) return html`<div class="settings-pending" aria-busy="true"></div>`;
  return html`
    ${needsApproval && html`<div class="notice" role="alert">
      <p>Тема «${pending.name}» хочет запустить свой скрипт эффектов (анимации, интерактив).
        Скрипт получает доступ ко всему приложению, включая чаты. Разрешайте только темам из надёжных источников.
        Без скрипта тема работает, но без эффектов.</p>
      <div class="row">
        <button class="primary" onClick=${() => allowScript(pending)}>Разрешить эффекты</button>
        <span class="muted">Если что-то пойдёт не так: откройте <a href="/?safe">безопасный режим</a>.</span>
      </div>
    </div>`}

    ${parked && html`<div class="notice" role="status">
      <p>После обновления приложения прежнее оформление${parked.theme ? ` «${parked.theme_name}»` : ""} отключено,
        чтобы оно не сломало меню${parked.plugin_css ? `; стили сторонних дополнений (${parked.plugin_css}) тоже отключены, сами дополнения работают` : ""}.
        Ничего не удалено.</p>
      <div class="row">
        <button onClick=${restoreParked}>Вернуть прежний вид…</button>
        <span class="muted">Можно и выбрать тему вручную в списке ниже.</span>
      </div>
    </div>`}

    <h2>Темы</h2>
    <div class="theme-grid" data-slot="theme-grid">
      ${themes.map((t) => html`<${ThemeCard} key=${t.id} theme=${t} active=${t.id === current}
        onPick=${pick} onCopy=${copy} onDelete=${remove} onVersions=${showVersions} />`)}
      ${themes.length === 0 && html`<div class="theme-empty"><p>Нет доступных тем. Установите тему из файла или создайте свою через раздел «Цвета».</p></div>`}
    </div>
    <input ref=${fileInput} type="file" hidden
      onChange=${(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importFile(f); }} />
    <div class="row">
      <button onClick=${() => fileInput.current.click()}>Установить тему из файла</button>
      <a href="/?safe" class="muted safe-link" title="Откроет страницу без тем и скриптов. Из консоли: python -m tavern safe-mode on">Безопасный режим</a>
    </div>

    ${deleted.length > 0 && html`
      <h2>Удалённые темы</h2>
      <ul class="list">
        ${deleted.map((d) => html`<li key=${d.id}>
          <span class="item">${d.name}</span>
          <button onClick=${() => showVersions(d)}>Вернуть…</button>
        </li>`)}
      </ul>`}

    ${versions && html`<div class="notice" ref=${versionsBox}>
      <strong>Версии темы «${versions.theme.name}»</strong>
      ${versions.list.length === 0 && html`<p class="muted">Сохранённых версий нет</p>`}
      <ul class="list">
        ${versions.list.map((v) => html`<li key=${v.version}>
          <span class="item">${new Date(v.saved_at).toLocaleString("ru-RU")} — ${v.name}${v.theme_version ? " v" + v.theme_version : ""}</span>
          <button onClick=${() => restore(v.version)}>Вернуть</button>
        </li>`)}
      </ul>
      <button onClick=${() => setVersions(null)}>Закрыть</button>
    </div>`}

    ${!SAFE_MODE && pending && !pending.error && html`<${ColorEditor} theme=${pending} onError=${onError}
      onSaved=${async (id, same) => { if (same) { reloadThemeCss(); await onChanged(); } else await save({ theme: id }); }} />`}

    <h2>Вид чата</h2>
    <${Select} value=${settings.chat_view || "bubbles"} aria-label="Как показывать сообщения чата"
      onChange=${(e) => save({ chat_view: e.target.value })}>
      <option value="bubbles">Пузыри: у каждого сообщения своя рамка</option>
      <option value="document">Документ: сплошной текст, над каждым сообщением тонкая строка с именем</option>
    </${Select}>
    <p class="hint">Действует во всех чатах. Одному чату вид меняется в его меню «⋯», пункт «Вид».
      «Документ» даёт тексту почти всю ширину экрана.</p>
    ${chatLook}

    ${characters.length > 0 && html`
      <h2>Тема персонажа</h2>
      <p class="muted">Чат с персонажем откроется в его теме. «Общая» — тема, выбранная выше.</p>
      <ul class="list">
        ${characters.map((c) => {
          const chosen = byId[(settings.character_themes || {})[c.id]];
          return html`<li key=${c.id}>
            <span class="item">${c.name}</span>
            <${Select} aria-label=${"Тема для " + c.name} value=${chosen ? chosen.id : ""}
              onChange=${(e) => setCharacterTheme(c.id, e.target.value)}>
              <option value="">Общая</option>
              ${themes.filter((t) => !t.error).map((t) => html`<option key=${t.id} value=${t.id}>${t.name}</option>`)}
            </${Select}>
            ${chosen && chosen.script && !scriptAllowed(chosen, settings) && html`<button
              title="Тема запускает свой скрипт: он получает доступ ко всему приложению"
              onClick=${() => allowScript(chosen)}>Разрешить эффекты</button>`}
          </li>`;
        })}
      </ul>`}
  `;
}
