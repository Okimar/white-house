// "Картинки" tab: the image library. Upload, generate with the picture connection, copy the
// address for a theme (url(/assets/NAME)), delete.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";

const SIZES = ["1024x1024", "1024x1536", "1536x1024", "512x512"];

export function Gallery({ settings, connections, onChanged, onError }) {
  const [assets, setAssets] = useState([]);
  const [request, setRequest] = useState("");
  const [size, setSize] = useState(SIZES[0]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const fileInput = useRef(null);

  const reload = () => api.get("/assets").then((d) => setAssets(d.assets)).catch((e) => onError(e.message));
  useEffect(() => { reload(); }, []);

  const upload = async (files) => {
    for (const file of files) {
      try {
        await api.postBinary(`/assets?name=${encodeURIComponent(file.name)}`, file, file.type || "application/octet-stream");
      } catch (e) { onError(`${file.name}: ${e.message}`); }
    }
    reload();
  };

  const generate = async (e) => {
    e.preventDefault();
    if (!request.trim() || busy) return;
    setBusy(true);
    setStatus("Рисую… это может занять минуту");
    try {
      await api.post("/assets/generate", { prompt: request.trim(), size });
      setStatus("Готово");
      reload();
    } catch (err) {
      setStatus("");
      onError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const copy = async (asset) => {
    const text = `url(${asset.url})`;
    try {
      await navigator.clipboard.writeText(text);
      setStatus(`Скопировано: ${text}`);
    } catch (e) {
      window.prompt("Скопируйте адрес", text); // clipboard needs https or localhost
    }
  };

  const remove = async (asset) => {
    if (!confirm(`Удалить картинку ${asset.name}?`)) return;
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
      <span class="muted">Подключение для картинок (OpenAI-совместимое, адрес …/v1, модель картинок)</span>
      <select value=${settings.image_connection || ""} onChange=${(e) => chooseConnection(e.target.value)}>
        <option value="">Не выбрано</option>
        ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name} · ${c.model || "модель не указана"}</option>`)}
      </select>
    </label>
    <form class="stack" onSubmit=${generate}>
      <textarea rows="3" placeholder="Что нарисовать: «ночное небо с сакурой, аниме, без людей»"
        value=${request} onInput=${(e) => setRequest(e.target.value)} disabled=${!settings.image_connection}></textarea>
      <div class="row">
        <select value=${size} aria-label="Размер" onChange=${(e) => setSize(e.target.value)}>
          ${SIZES.map((s) => html`<option key=${s} value=${s}>${s}</option>`)}
        </select>
        <button class="primary" type="submit" disabled=${busy || !settings.image_connection || !request.trim()}>
          ${busy ? "Рисую…" : "Сгенерировать"}</button>
      </div>
    </form>
    <p class="muted" role="status">${status}</p>

    <h2>Библиотека</h2>
    <input ref=${fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml" multiple hidden
      onChange=${(e) => { const files = [...e.target.files]; e.target.value = ""; upload(files); }} />
    <div class="row"><button onClick=${() => fileInput.current.click()}>Загрузить картинки</button></div>
    ${assets.length === 0 && html`<p class="muted">Картинок пока нет.</p>`}
    <div class="asset-grid">
      ${assets.map((a) => html`<div class="asset-card" key=${a.name}>
        <img src=${a.url} alt=${a.name} loading="lazy" />
        <span class="item" title=${a.name}>${a.name}</span>
        <div class="row">
          <button title="Скопировать адрес для темы" onClick=${() => copy(a)}>Адрес</button>
          <button title="Удалить" onClick=${() => remove(a)}>🗑</button>
        </div>
      </div>`)}
    </div>
  `;
}
