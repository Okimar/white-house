// Backups and the data check (stage 9а).
// - BackupsTab: the «Копии» tab of Settings: when copies are taken, how many of each kind are kept,
//   an own folder; the list (download, restore, delete), «Сделать сейчас», «Загрузить из файла»;
//   the data check's findings with «Исправить».
// - GoogleDrive (stage 9б): the guide to the Google app data, the data itself, «Войти через Google» (or pasting the
//   address Google opened when White House runs on another device), auto sending, the copies on Drive
//   with «Скачать сюда» / «Восстановить». main.js finishes a sign-in that came back to this page.
// - BackupWarning: the line at the top of the page when the startup check found something, the
//   last copy failed or Google Drive has a problem. A restore reloads the page (notice "restored").
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { notify } from "../core/notify.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, EmptyNote, useLoad } from "./State.js";

const KINDS = {
  start: "при запуске", daily: "ежедневная", hourly: "по часам", risky: "перед опасным",
  manual: "вручную", upload: "загружена", migrate: "перед обновлением базы",
};
const REASONS = {
  "delete-character": "удаление персонажа", "delete-chat": "удаление чата", "delete-lorebook": "удаление лорбука",
  "import-character": "импорт персонажа", "import-chat": "импорт чата", "import-lorebook": "импорт лорбука",
  "plugin-update": "обновление дополнения", "plugin-rollback": "откат дополнения", "doctor-fix": "исправление при проверке данных",
  "pre-restore": "восстановление",
};
const KEEP = [["start", "при запуске"], ["daily", "ежедневных"], ["hourly", "по часам"], ["risky", "перед опасным"]];
const LEVEL = {
  error: ["circle-x", "ошибка"], warn: ["triangle-alert", "предупреждение"], info: ["info", "сведения"],
};

function size(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} МБ` : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}

function gigabytes(bytes) {
  return `${(bytes / 1024 ** 3).toFixed(bytes < 10 * 1024 ** 3 ? 1 : 0)} ГБ`;
}

function when(iso) {
  return new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
}

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

// A whole-number field that can be emptied while typing: the raw text is kept, the number is clamped when
// the field is left (and again when saving).
function NumField({ value, lo, hi, onValue, ...rest }) {
  const [text, setText] = useState(String(value));
  useEffect(() => { if (parseInt(text, 10) !== value) setText(String(value)); }, [value]);
  return html`<input class="num" type="number" min=${lo} max=${hi} value=${text} ...${rest}
    onInput=${(e) => {
      setText(e.target.value);
      const n = parseInt(e.target.value, 10);
      if (!Number.isNaN(n)) onValue(n);
    }}
    onBlur=${() => {
      const n = parseInt(text, 10);
      const fixed = clamp(Number.isNaN(n) ? value : n, lo, hi);
      setText(String(fixed));
      if (fixed !== value) onValue(fixed);
    }} />`;
}

export function BackupWarning({ status, onOpen, onClose }) {
  if (!status) return null;
  const items = status.warnings.filter((w) => w.level !== "info").map((w) => w.title);
  if (status.last_error) items.push(status.last_error);
  if (status.gdrive && status.gdrive.problem) items.push(status.gdrive.problem);
  if (!items.length) return null;
  return html`<div class="backup-warning" data-slot="backup-warning" role="status">
    <span><${Icon} name="triangle-alert" /> ${items.join(" · ")}</span>
    <button onClick=${onOpen}>Подробнее</button>
    <${IconButton} icon="x" label="Скрыть" onClick=${onClose} />
  </div>`;
}

function Doctor({ onError }) {
  const [findings, setFindings] = useState(null);
  const [busy, setBusy] = useState("");
  const [notes, setNotes] = useState([]);
  const check = async () => {
    setBusy("check");
    setNotes([]);
    try { setFindings((await api.get("/doctor")).findings); } catch (e) { onError(e.message); } finally { setBusy(""); }
  };
  const fix = async () => {
    setBusy("fix");
    try {
      const done = await api.post("/doctor/fix", { ids: findings.filter((f) => f.fix).map((f) => f.id) });
      setFindings(done.findings);
      setNotes(done.notes || []);
      notify("Исправлено", "ok");
    } catch (e) { onError(e.message); } finally { setBusy(""); }
  };
  const fixable = findings && findings.some((f) => f.fix);
  const byModel = findings && findings.some((f) => f.fix && f.id.startsWith("layers:"));
  return html`<section class="doctor stack" data-slot="doctor">
    <h3>Проверка данных</h3>
    <div class="row">
      <button disabled=${!!busy} onClick=${check}>${busy === "check" ? "Проверяю…" : "Проверить"}</button>
      ${fixable && html`<button disabled=${!!busy} onClick=${fix}
        title="Сначала делается копия">${busy === "fix" ? (byModel ? "Модель пишет ключевые слова…" : "Исправляю…") : "Исправить"}</button>`}
    </div>
    ${fixable && html`<p class="hint">Перед исправлением сначала делается копия.</p>`}
    ${notes.length > 0 && html`<ul class="list doctor-notes" role="status">
      ${notes.map((n, i) => html`<li key=${i}>${n}</li>`)}
    </ul>`}
    ${findings && !findings.length && html`<p><${Icon} name="check" /> Всё в порядке</p>`}
    ${findings && findings.length > 0 && html`<ul class="list doctor-list">
      ${findings.map((f) => html`<li key=${f.id} data-level=${f.level}>
        <strong><span role="img" aria-label=${(LEVEL[f.level] || LEVEL.info)[1]}><${Icon} name=${(LEVEL[f.level] || LEVEL.info)[0]} /></span> ${f.title}</strong>
        ${f.fix && html` <span class="muted">(можно исправить)</span>`}
        ${f.detail && html`<div class="muted">${f.detail}</div>`}
      </li>`)}
    </ul>`}
  </section>`;
}

const ON_THIS_DEVICE = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname);

// The steps come from the server (services/gdrive.py GUIDE): the assistant gives the same text.
function DriveGuide() {
  const guide = useLoad(() => api.get("/backups/gdrive/guide").then((d) => d.steps), []);
  if (!guide.data) return html`<${Pending} error=${guide.error} onRetry=${guide.reload} />`;
  return html`<ol class="drive-guide">
    ${guide.data.map((s, i) => html`<li key=${i}>
      ${s.link && html`<a class="button" href=${s.link} target="_blank" rel="noopener noreferrer">Открыть <${Icon} name="external-link" /></a> `}${s.text}
    </li>`)}
  </ol>`;
}

function GoogleDrive({ drive, onError, reload }) {
  const [clientId, setClientId] = useState(drive.client_id);
  const [secret, setSecret] = useState("");
  const [address, setAddress] = useState("");
  const [files, setFiles] = useState(null);
  const [busy, setBusy] = useState("");
  useEffect(() => setClientId(drive.client_id), [drive.client_id]);

  const act = async (label, fn) => {
    setBusy(label);
    try { await fn(); } catch (e) { onError(e.message); } finally { setBusy(""); reload(); }
  };
  const loadFiles = () => act("files", async () => setFiles((await api.get("/backups/gdrive/files")).files));
  const savePass = () => act("pass", async () => {
    await api.put("/backups/gdrive", { client_id: clientId.trim(), client_secret: secret.trim() });
    setSecret("");
    notify("Сохранено", "ok");
  });
  const login = () => act("login", async () => {
    // Opened before the request: a window opened after a wait is blocked as a pop-up.
    const tab = window.open("about:blank", "_blank");
    try {
      const { url } = await api.post("/backups/gdrive/login");
      if (tab) { tab.opener = null; tab.location.href = url; } else location.href = url;
    } catch (e) { if (tab) tab.close(); throw e; }
  });
  const finish = () => act("finish", async () => {
    await api.post("/backups/gdrive/finish", { address: address.trim() });
    setAddress("");
  });
  const logout = async () => {
    if (!(await ask("Выйти из Google?", { ok: "Выйти",
      hint: "Копии на Диске останутся, новые туда уходить перестанут." }))) return;
    act("logout", async () => { await api.post("/backups/gdrive/logout"); setFiles(null); });
  };
  const download = (f) => act("download", async () => {
    await api.post(`/backups/gdrive/files/${encodeURIComponent(f.id)}/download`);
    setFiles((await api.get("/backups/gdrive/files")).files);
  });
  const restore = async (f) => {
    if (!(await ask(`Вернуть всё к копии с Диска от ${when(f.created_at)}?`, { ok: "Восстановить", danger: true,
      hint: "Сначала сохранится копия того, что есть сейчас, потом страница перезагрузится." }))) return;
    act("restore", () => api.post(`/backups/gdrive/files/${encodeURIComponent(f.id)}/restore`));
  };

  if (drive.signed_in) {
    return html`<section class="gdrive stack" data-slot="gdrive">
      <h3>Google Диск</h3>
      <p><${Icon} name="check" /> Вход: <strong>${drive.account || "выполнен"}</strong>
        ${drive.free != null && html`<span class="muted"> · свободно ${gigabytes(drive.free)}</span>`}
        ${drive.last_ok && html`<span class="muted"> · последняя отправка ${when(drive.last_ok)}</span>`}</p>
      ${drive.busy && html`<p class="muted">Отправляю ${drive.busy}…</p>`}
      ${!drive.busy && drive.queue > 0 && html`<p class="muted">Ждут отправки: ${drive.queue}</p>`}
      ${drive.problem && html`<p class="error">${drive.problem}</p>`}
      <label><input type="checkbox" checked=${drive.auto}
        onChange=${(e) => act("auto", () => api.put("/backups/gdrive", { auto: e.target.checked }))} />
        Отправлять сами каждую новую копию</label>
      <p class="hint">Копии лежат в папке «Tavern — копии» и с Диска не удаляются. White House видит только свои файлы.</p>
      <div class="row">
        <button disabled=${Boolean(busy)} onClick=${loadFiles}>${busy === "files" ? "Загружаю…" : "Копии на Диске"}</button>
        <span class="spacer"></span>
        <button disabled=${Boolean(busy)} onClick=${logout}>Выйти</button>
      </div>
      ${busy === "restore" && html`<p class="muted">Скачиваю и восстанавливаю…</p>`}
      ${files && !files.length && html`<${EmptyNote} title="На Диске копий пока нет" />`}
      ${files && files.length > 0 && html`<ul class="list backup-list drive-list">
        ${files.map((f) => html`<li key=${f.id} class="row" data-kind=${f.kind}>
          <span class="grow"><${Icon} name="cloud" /> ${when(f.created_at)} · ${KINDS[f.kind] || f.kind}
            ${f.reason && html`<span class="muted"> (${REASONS[f.reason] || f.reason})</span>`}
            <span class="muted"> · ${size(f.size)}${f.here ? " · есть здесь" : ""}</span></span>
          ${!f.here && html`<button disabled=${Boolean(busy)} onClick=${() => download(f)}>Скачать сюда</button>`}
          <button disabled=${Boolean(busy)} onClick=${() => restore(f)}>Восстановить</button>
        </li>`)}
      </ul>`}
    </section>`;
  }

  const ready = drive.client_id && drive.has_secret;
  return html`<section class="gdrive stack" data-slot="gdrive">
    <h3>Google Диск</h3>
    <p class="muted">Копии могут сами уходить в папку на вашем Google Диске, а с Диска их можно вернуть,
      в том числе на новом телефоне. Нужны данные приложения Google: их получают один раз, минут за десять.</p>
    <details open=${!ready}>
      <summary>Как получить данные приложения</summary>
      <${DriveGuide} />
    </details>
    <label class="stack">Идентификатор клиента (Client ID)
      <input value=${clientId} onInput=${(e) => setClientId(e.target.value)} />
      <span class="hint">Заканчивается на apps.googleusercontent.com</span></label>
    <label class="stack">Секрет клиента (Client secret)
      <input type="password" value=${secret} autocomplete="off"
        placeholder=${drive.has_secret ? "сохранён; вставьте новый, чтобы заменить" : ""}
        onInput=${(e) => setSecret(e.target.value)} />
      ${!drive.has_secret && html`<span class="hint">Начинается с GOCSPX-</span>`}</label>
    <div class="row">
      <button disabled=${Boolean(busy) || (clientId.trim() === drive.client_id && !secret.trim())}
        onClick=${savePass}>Сохранить данные</button>
      <span class="spacer"></span>
      <button class="primary" disabled=${!ready || Boolean(busy)} onClick=${login}>Войти через Google</button>
    </div>
    ${ready && html`<div class="stack">
      <p class="muted">${ON_THIS_DEVICE ? "Если White House запущен на другом устройстве, после входа" : "White House запущен на другом устройстве: после входа"} Google откроет пустую страницу с адресом, который начинается с <code>http://127.0.0.1</code>.
        Скопируйте этот адрес целиком и вставьте сюда.</p>
      <div class="row">
        <input class="grow" value=${address} placeholder="http://127.0.0.1:…/?state=…&code=…"
          aria-label="Адрес после входа" onInput=${(e) => setAddress(e.target.value)} />
        <button disabled=${!address.trim() || Boolean(busy)} onClick=${finish}>Готово</button>
      </div>
    </div>`}
  </section>`;
}

export function BackupsTab({ onError }) {
  const [data, setData] = useState(null);
  const [settings, setSettings] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState("");
  const dirtyRef = useRef(false); // the event handler below is made once and must see the current value
  const mark = (value) => { dirtyRef.current = value; setDirty(value); };

  // A reload (also on the notice «a copy was made») never replaces settings the person is editing: only the list.
  const load = () => api.get("/backups").then((d) => {
    setData(d);
    setLoadError("");
    setSettings((cur) => (cur && dirtyRef.current ? cur : d.settings));
  }).catch((e) => { setLoadError(e.message); onError(e.message); });
  useEffect(() => {
    load();
    window.addEventListener("tavern:backups", load);
    return () => window.removeEventListener("tavern:backups", load);
  }, []);
  if (!data || !settings) return html`<${Pending} error=${loadError} onRetry=${load} />`;

  const change = (patch) => { setSettings({ ...settings, ...patch }); mark(true); };
  const act = async (label, fn) => {
    setBusy(label);
    try { await fn(); } catch (e) { onError(e.message); } finally { setBusy(""); load(); }
  };
  const save = () => act("save", async () => {
    const fixed = { ...settings,
      daily_hour: clamp(Number(settings.daily_hour) || 0, 0, 23),
      every_hours: clamp(Number(settings.every_hours) || 1, 1, 168),
      keep: Object.fromEntries(Object.entries(settings.keep).map(([k, v]) => [k, clamp(Number(v) || 1, 1, 1000)])) };
    await api.put("/backups/settings", fixed);
    setSettings(fixed);
    mark(false);
    notify("Сохранено", "ok");
  });
  const createNow = () => act("create", async () => {
    const made = await api.post("/backups");
    if (made.warning) onError(made.warning);
  });
  const restore = async (b) => {
    if (!(await ask(`Вернуть всё к копии от ${when(b.created_at)}?`, { ok: "Восстановить", danger: true,
      hint: "Сначала сохранится копия того, что есть сейчас, потом страница перезагрузится." }))) return;
    act("restore", () => api.post(`/backups/${encodeURIComponent(b.name)}/restore`));
  };
  const remove = async (b) => {
    if (!(await ask(`Удалить копию от ${when(b.created_at)}?`, { ok: "Удалить", danger: true }))) return;
    act("delete", () => api.del(`/backups/${encodeURIComponent(b.name)}`));
  };
  const upload = (file) => act("upload", () => api.postBinary("/backups/upload", file, "application/zip"));
  const toDrive = (b) => act("drive", () => api.post(`/backups/${encodeURIComponent(b.name)}/gdrive`));
  const drive = data.gdrive;

  return html`<section class="backups stack" data-slot="backups">
    <p class="muted">В копию входит всё: чаты, персонажи, лорбуки, настройки, картинки, темы, дополнения.
      Ключей API в ней нет, поэтому копию можно передать. Если с прошлой копии ничего не изменилось, новая не делается.</p>

    <h3>Когда делать</h3>
    <div class="stack">
      <label><input type="checkbox" checked=${settings.on_start} onChange=${(e) => change({ on_start: e.target.checked })} />
        при запуске</label>
      <label><input type="checkbox" checked=${settings.daily} onChange=${(e) => change({ daily: e.target.checked })} />
        раз в день, после <${NumField} value=${settings.daily_hour} lo=${0} hi=${23}
          aria-label="Час ежедневной копии" disabled=${!settings.daily}
          onValue=${(n) => change({ daily_hour: n })} /> ч</label>
      <label><input type="checkbox" checked=${settings.hourly} onChange=${(e) => change({ hourly: e.target.checked })} />
        каждые <${NumField} value=${settings.every_hours} lo=${1} hi=${168}
          aria-label="Через сколько часов" disabled=${!settings.hourly}
          onValue=${(n) => change({ every_hours: n })} /> ч</label>
      <label><input type="checkbox" checked=${settings.risky} onChange=${(e) => change({ risky: e.target.checked })} />
        перед опасным действием</label>
      <span class="hint">Опасное: удаление персонажа, чата или лорбука, импорт, обновление и откат дополнения,
        исправление при проверке данных, восстановление.</span>
    </div>

    <h3>Сколько хранить</h3>
    <div class="row">
      ${KEEP.map(([kind, label]) => html`<label key=${kind}>${label} <${NumField} value=${settings.keep[kind]} lo=${1} hi=${1000}
        onValue=${(n) => change({ keep: { ...settings.keep, [kind]: n } })} /></label>`)}
    </div>
    <p class="hint">Ручные и загруженные копии сами не удаляются.</p>

    <h3>Куда</h3>
    <label class="stack">Своя папка для копий
      <input value=${settings.dir} placeholder="папка данных (по умолчанию)"
        onInput=${(e) => change({ dir: e.target.value })} /></label>
    <p class="hint">Полный путь к папке, например к папке Google Диска на компьютере: тогда копии сами уйдут в облако.
      Если папка недоступна, копия ляжет в папку данных.</p>
    ${data.folder_problem && html`<p class="error">${data.folder_problem}</p>`}
    <div class="row"><span class="spacer"></span>
      ${dirty && html`<span class="muted">Есть несохранённые изменения</span>`}
      <button class="primary" disabled=${!dirty || Boolean(busy)} onClick=${save}>Сохранить</button></div>

    <h3>Копии</h3>
    ${data.last_error && html`<p class="error">${data.last_error}</p>`}
    <div class="row">
      <button disabled=${Boolean(busy)} onClick=${createNow}>${busy === "create" ? "Создаю…" : "Сделать сейчас"}</button>
      <label class="button">Загрузить из файла<input type="file" accept=".zip" hidden
        onChange=${(e) => { if (e.target.files[0]) upload(e.target.files[0]); e.target.value = ""; }} /></label>
      ${busy === "restore" && html`<span class="muted">Восстанавливаю…</span>`}
    </div>
    ${!data.backups.length && html`<${EmptyNote} title="Копий пока нет"
      hint="Нажмите «Сделать сейчас» или дождитесь автоматической копии." />`}
    <ul class="list backup-list">
      ${data.backups.map((b) => html`<li key=${b.name} class="row" data-kind=${b.kind}>
        <span class="grow">${when(b.created_at)} · ${KINDS[b.kind] || b.kind}
          ${b.reason && b.kind !== "migrate" && html`<span class="muted"> (${REASONS[b.reason] || b.reason})</span>`}
          <span class="muted"> · ${size(b.size)}${b.where === "own" ? " · своя папка" : ""}</span>
          ${b.drive && html`<span class="muted" title="Есть на Google Диске"> · <${Icon} name="cloud" /> на Диске</span>`}</span>
        ${drive && drive.signed_in && !b.drive && b.kind !== "migrate" && html`<button disabled=${Boolean(busy)}
          onClick=${() => toDrive(b)}>${busy === "drive" ? "Отправляю…" : "На Диск"}</button>`}
        <a class="button" href=${"/api/backups/" + encodeURIComponent(b.name)} download=${b.name}>Скачать</a>
        <button disabled=${Boolean(busy)} onClick=${() => restore(b)}>Восстановить</button>
        <${IconButton} icon="trash-2" label="Удалить копию" class="danger-text" disabled=${Boolean(busy)}
          onClick=${() => remove(b)} />
      </li>`)}
    </ul>

    ${drive && html`<${GoogleDrive} drive=${drive} onError=${onError} reload=${load} />`}

    <${Doctor} onError=${onError} />
  </section>`;
}
