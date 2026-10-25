// Settings dialog: your name and API connections. API keys are write-only: the server only says
// whether a key is stored, so the field stays empty and an empty value means "keep the key".
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";

const BLANK = { name: "", base_url: "http://127.0.0.1:5001/v1", model: "", api_key: "" };

export function Settings({ settings, onClose, onChanged, onError }) {
  const [connections, setConnections] = useState([]);
  const [active, setActive] = useState(settings.active_connection || null);
  const [userName, setUserName] = useState(settings.user_name || "");
  const [form, setForm] = useState(BLANK);
  const [models, setModels] = useState([]);
  const [status, setStatus] = useState("");

  const reload = () => api.get("/connections").then((d) => {
    setConnections(d.connections);
    setActive(d.active);
  }).catch((e) => onError(e.message));
  useEffect(() => { reload(); }, []);

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value });

  const saveName = async () => {
    await api.put("/settings", { user_name: userName.trim() }).catch((e) => onError(e.message));
    onChanged();
  };

  const add = async (e) => {
    e.preventDefault();
    try {
      await api.post("/connections", { ...form, kind: "openai" });
      setForm(BLANK);
      setModels([]);
      await reload();
      onChanged();
    } catch (err) {
      onError(err.message);
    }
  };

  const choose = async (id) => {
    await api.put("/settings", { active_connection: id }).catch((e) => onError(e.message));
    setActive(id);
    onChanged();
  };

  const test = async (id) => {
    setStatus("Проверка…");
    try {
      const { models: list } = await api.get(`/connections/${id}/models`);
      setStatus(`Подключение работает, моделей: ${list.length}`);
      setModels(list.map((m) => m.id || m));
    } catch (err) {
      setStatus("Ошибка: " + err.message);
    }
  };

  const remove = async (c) => {
    if (!confirm(`Удалить подключение «${c.name}»?`)) return;
    await api.del(`/connections/${c.id}`).catch((e) => onError(e.message));
    await reload();
    onChanged();
  };

  const setModel = async (c, model) => {
    await api.put(`/connections/${c.id}`, { model }).catch((e) => onError(e.message));
    await reload();
  };

  return html`
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="dialog" role="dialog" aria-modal="true" aria-label="Настройки">
        <div class="side-head"><strong>Настройки</strong><span class="spacer"></span>
          <button onClick=${onClose} title="Закрыть">✕</button></div>

        <h2>Ваше имя</h2>
        <div class="row">
          <input value=${userName} placeholder="User" onInput=${(e) => setUserName(e.target.value)} />
          <button onClick=${saveName}>Сохранить</button>
        </div>

        <h2>Подключения к API</h2>
        <ul class="list">
          ${connections.length === 0 && html`<li class="muted">Подключений нет. Добавьте первое ниже.</li>`}
          ${connections.map((c) => html`
            <li key=${c.id} class="conn">
              <label>
                <input type="radio" name="active" checked=${c.id === active} onChange=${() => choose(c.id)} />
                <strong>${c.name}</strong> <span class="muted">${c.base_url}${c.has_key ? " · ключ задан" : ""}</span>
              </label>
              <div class="row">
                <input value=${c.model} placeholder="модель" list="models"
                  onChange=${(e) => setModel(c, e.target.value)} />
                <button onClick=${() => test(c.id)}>Проверить</button>
                <button onClick=${() => remove(c)}>Удалить</button>
              </div>
            </li>`)}
        </ul>
        <datalist id="models">${models.map((m) => html`<option value=${m}></option>`)}</datalist>
        <p class="muted" role="status">${status}</p>

        <h3>Новое подключение (OpenAI-совместимое)</h3>
        <form class="stack" onSubmit=${add}>
          <input required placeholder="Название" value=${form.name} onInput=${set("name")} />
          <input required placeholder="Адрес API, например https://api.openai.com/v1" value=${form.base_url}
            onInput=${set("base_url")} />
          <input placeholder="Модель" value=${form.model} onInput=${set("model")} />
          <input type="password" autocomplete="off" placeholder="API-ключ (необязательно для локальных)"
            value=${form.api_key} onInput=${set("api_key")} />
          <button class="primary" type="submit">Добавить</button>
        </form>
        <p class="muted">Для проверки без настоящей модели: <code>python -m tavern.devtools.fake_llm</code>
          и адрес <code>http://127.0.0.1:5001/v1</code>.</p>
      </div>
    </div>
  `;
}
