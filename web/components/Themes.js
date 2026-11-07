// "Оформление" tab: pick a theme, copy, share as a file, delete, roll back to an earlier version,
// allow a theme's effects script, and give characters their own theme.
import { Select } from "./Select.js";
import { html, useState, useRef, useEffect } from "../vendor/preact-htm.mjs";
import { api, ApiError } from "../core/api.js";
import { IconButton } from "./Icon.js";
import { ask, askText } from "./Dialogs.js";
import { notify } from "../core/notify.js";

export function scriptAllowed(theme, settings) {
  if (!theme || !theme.script) return false;
  return theme.builtin || (settings.trusted_theme_scripts || {})[theme.id] === theme.script_sha256;
}

function ThemeCard({ theme, active, onPick, onCopy, onDelete, onVersions }) {
  const preview = theme.preview && `/themes/${theme.id}/${theme.preview}`;
  return html`
    <div class=${"theme-card" + (active ? " active" : "")} data-slot="theme-card">
      ${preview
        ? html`<img class="preview" src=${preview} alt="" loading="lazy" />`
        : html`<div class="preview preview-sample" aria-hidden="true"><span>${theme.name}</span></div>`}
      <strong>${theme.name}</strong>
      <span class="muted">${[theme.author, theme.version && "v" + theme.version].filter(Boolean).join(" · ")}</span>
      <span class="row">
        ${theme.builtin && html`<span class="badge">встроенная</span>`}
        ${theme.script && html`<span class="badge">эффекты</span>`}
        ${theme.error && html`<span class="badge">сломана</span>`}
      </span>
      ${theme.script && html`<span class="hint">Эффекты: тема запускает свой скрипт, например анимации</span>`}
      ${theme.error && html`<span class="hint danger-text">Не работает: ${theme.error}</span>`}
      <div class="row">
        <button class=${active ? "primary" : ""} disabled=${!!theme.error} onClick=${() => onPick(theme)}>
          ${active ? "Выбрана" : "Выбрать"}</button>
        <button title="Сделать свою копию темы, чтобы менять" disabled=${!!theme.error} onClick=${() => onCopy(theme)}>Дублировать</button>
        ${!theme.error && html`<a class="button" title="Сохранить тему одним файлом, чтобы поделиться"
          href=${`/api/themes/${theme.id}/export`}>Скачать</a>`}
        ${theme.versions > 0 && html`<button title="Вернуть прошлую версию" onClick=${() => onVersions(theme)}>Версии</button>`}
        ${!theme.builtin && html`<${IconButton} icon="trash-2" label="Удалить тему" title="Удалить (можно вернуть через версии)"
          onClick=${() => onDelete(theme)} />`}
      </div>
    </div>`;
}

export function Themes({ themes, deleted, settings, characters, onChanged, onError }) {
  const [versions, setVersions] = useState(null); // { theme, list }
  const fileInput = useRef(null);
  const versionsBox = useRef(null);
  const current = settings.theme || "default";
  const byId = Object.fromEntries(themes.map((t) => [t.id, t]));
  const [parked, setParked] = useState(null);
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
    api.get("/look").then((d) => setParked(d.parked)).catch(() => setParked(null));
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
    </div>
    <input ref=${fileInput} type="file" accept=".tavern-theme,.zip,application/zip" hidden
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
