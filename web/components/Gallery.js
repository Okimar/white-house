// "Картинки" tab: the image library. Upload, generate with the picture connection, copy the
// address for a theme (url(/assets/NAME)), delete.
import { html, useState, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { Icon, IconButton } from "./Icon.js";
import { ask, askText } from "./Dialogs.js";
import { EmptyNote, Pending, useLoad } from "./State.js";
import { notify } from "../core/notify.js";

const SIZES = ["1024x1024", "1024x1536", "1536x1024", "512x512"];

export function Gallery({ settings, connections, onChanged, onError }) {
  const { data, error, reload } = useLoad(() => api.get("/assets"), []);
  const assets = data ? data.assets : null;
  const [request, setRequest] = useState("");
  const [size, setSize] = useState(SIZES[0]);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState("");
  const fileInput = useRef(null);

  const upload = async (files) => {
    setUploading(true);
    let done = 0;
    for (const file of files) {
      try {
        await api.postBinary(`/assets?name=${encodeURIComponent(file.name)}`, file, file.type || "application/octet-stream");
        done += 1;
      } catch (e) { onError(`${file.name}: ${e.message}`); }
    }
    setUploading(false);
    if (done) notify(done === 1 ? "Картинка загружена" : `Загружено картинок: ${done}`, "ok");
    reload();
  };

  const generate = async (e) => {
    e.preventDefault();
    if (!request.trim() || busy) return;
    setBusy(true);
    setStatus("Рисую, это может занять минуту");
    try {
      await api.post("/assets/generate", { prompt: request.trim(), size });
      notify("Картинка готова", "ok");
      reload();
    } catch (err) {
      onError(err.message);
    } finally {
      setStatus("");
      setBusy(false);
    }
  };

  const copy = async (asset) => {
    const text = `url(${asset.url})`;
    try {
      await navigator.clipboard.writeText(text);
      notify(`Адрес скопирован: ${text}`, "ok");
    } catch (e) {
      await askText("Скопируйте адрес", { value: text, ok: "Готово", cancel: "" }); // clipboard needs https or localhost
    }
  };

  const remove = async (asset) => {
    if (!await ask(`Удалить картинку ${asset.name}?`, { ok: "Удалить", danger: true })) return;
    try {
      await api.del(`/assets/${encodeURIComponent(asset.name)}`);
      reload();
    } catch (e) { onError(e.message); }
  };

  const chooseConnection = async (id) => {
    try {
      await api.put("/settings", { image_connection: id });
      await onChanged();
    } catch (e) { onError(e.message); }
  };

  return html`
    <h2>Генерация картинок</h2>
    <label class="stack">
      <span>Подключение для картинок</span>
      <span class="hint">OpenAI-совместимое: адрес вида …/v1 и модель, которая умеет рисовать</span>
      <select value=${settings.image_connection || ""} onChange=${(e) => chooseConnection(e.target.value)}>
        <option value="">Не выбрано</option>
        ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name} · ${c.model || "модель не указана"}</option>`)}
      </select>
    </label>
    <form class="stack" onSubmit=${generate}>
      <textarea rows="3" placeholder="Что нарисовать: «ночное небо с сакурой, аниме, без людей»" aria-label="Что нарисовать"
        value=${request} onInput=${(e) => setRequest(e.target.value)} disabled=${!settings.image_connection}></textarea>
      ${!settings.image_connection && html`<p class="hint">Сначала выберите подключение для картинок выше, без него рисовать нельзя</p>`}
      <div class="row">
        <select value=${size} aria-label="Размер" onChange=${(e) => setSize(e.target.value)}>
          ${SIZES.map((s) => html`<option key=${s} value=${s}>${s.replace("x", "×")}</option>`)}
        </select>
        <button class="primary" type="submit" disabled=${busy || !settings.image_connection || !request.trim()}>
          ${busy ? "Рисую…" : "Сгенерировать"}</button>
      </div>
    </form>
    <p class="muted" role="status">${status}</p>

    <h2>Библиотека</h2>
    <input ref=${fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml" multiple hidden
      onChange=${(e) => { const files = [...e.target.files]; e.target.value = ""; upload(files); }} />
    <div class="row"><button disabled=${uploading} onClick=${() => fileInput.current.click()}>
      <${Icon} name="upload" /> ${uploading ? "Загружаю…" : "Загрузить картинки"}</button></div>
    ${assets === null && html`<${Pending} error=${error} onRetry=${reload} />`}
    ${assets && assets.length === 0 && html`<${EmptyNote} title="Картинок пока нет"
      hint="Загрузите свои или сгенерируйте выше. Адрес картинки можно вставить в тему" />`}
    ${assets && assets.length > 0 && html`<p class="hint">«Скопировать адрес» кладёт в буфер url(/assets/имя): его вставляют в CSS темы</p>`}
    <div class="asset-grid" data-slot="asset-grid">
      ${(assets || []).map((a) => html`<div class="asset-card" data-slot="asset-card" key=${a.name}>
        <img src=${a.url} alt=${a.name} loading="lazy" />
        <span class="item" title=${a.name}>${a.name}</span>
        <div class="row">
          <${IconButton} icon="copy" label="Скопировать адрес" onClick=${() => copy(a)} />
          <${IconButton} icon="trash-2" label="Удалить картинку" onClick=${() => remove(a)} />
        </div>
      </div>`)}
    </div>
  `;
}
