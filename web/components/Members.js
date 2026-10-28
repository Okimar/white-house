// "Участники" panel of a chat: who takes part, where each card goes (depth) and who is dropped first
// when the prompt does not fit (importance), which one is "me", and the chat's preset.
// Every change goes to the server at once; agents change the same things through the same API.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";

const DEPTHS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20];
const IMPORTANCE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

export function Members({ chat, characters, settings, onClose, onChanged, onEditCharacter, onError }) {
  const [members, setMembers] = useState([]);
  const [presets, setPresets] = useState([]);
  const [adding, setAdding] = useState("");

  useEffect(() => {
    api.get(`/chats/${chat.id}/members`).then((d) => setMembers(d.members)).catch((e) => onError(e.message));
    api.get("/presets").then((d) => setPresets(d.presets)).catch((e) => onError(e.message));
  }, [chat.id]);

  const apply = (promise) => promise.then((d) => { setMembers(d.members); onChanged(); })
    .catch((e) => onError(e.message));
  const change = (m, patch) => apply(api.put(`/chats/${chat.id}/members/${m.character_id}`, patch));
  const remove = (m) => confirm(`Убрать «${m.name}» из чата? Сам персонаж останется.`)
    && apply(api.del(`/chats/${chat.id}/members/${m.character_id}`));
  const add = () => {
    if (!adding) return;
    apply(api.post(`/chats/${chat.id}/members`, { character_id: adding }));
    setAdding("");
  };
  const choosePreset = (id) => api.put(`/chats/${chat.id}/meta`, { preset_id: id })
    .then(onChanged).catch((e) => onError(e.message));

  const outside = characters.filter((c) => !members.some((m) => m.character_id === c.id));
  const globalPreset = presets.find((p) => p.id === (settings.active_preset || "default"));

  return html`
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="dialog" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Участники чата">
        <div class="side-head"><strong>Участники чата</strong><span class="spacer"></span>
          <button onClick=${onClose} title="Закрыть">✕</button></div>
        <p class="muted">Глубина — куда вставить карточку: «в начале» или за сколько сообщений до конца чата
          (ближе к концу — сильнее влияет). Важность — 1 самый важный; при нехватке места первыми
          убираются карточки с большим числом.</p>
        <ul class="members">
          ${members.length === 0 && html`<li class="muted">В чате пока нет персонажей.</li>`}
          ${members.map((m) => html`
            <li key=${m.character_id} class=${"member" + (m.is_me ? " me" : "")} data-slot="member">
              <div class="row">
                ${m.has_avatar && html`<img class="msg-avatar" src=${`/api/characters/${m.character_id}/avatar`} alt="" />`}
                <strong>${m.name}</strong>
                ${m.is_me && html`<span class="badge">это я</span>`}
                ${m.character_id === chat.character_id && html`<span class="badge">главный</span>`}
                <span class="spacer"></span>
                <button title="Карточка персонажа" onClick=${() => onEditCharacter(m.character_id)}>✎</button>
                <button title="Убрать из чата" onClick=${() => remove(m)}>✕</button>
              </div>
              <div class="row">
                <label>Глубина
                  <select value=${m.depth === null ? "top" : String(m.depth)}
                    onChange=${(e) => change(m, { depth: e.target.value === "top" ? null : Number(e.target.value) })}>
                    <option value="top">в начале</option>
                    ${[...new Set([...DEPTHS, ...(m.depth === null ? [] : [m.depth])])].sort((a, b) => a - b)
                      .map((d) => html`<option key=${d} value=${String(d)}>${d}</option>`)}
                  </select></label>
                <label>Важность
                  <select value=${String(m.importance)} onChange=${(e) => change(m, { importance: Number(e.target.value) })}>
                    ${IMPORTANCE.map((i) => html`<option key=${i} value=${String(i)}>${i}</option>`)}
                  </select></label>
                <label><input type="checkbox" checked=${m.is_me}
                  onChange=${(e) => change(m, { is_me: e.target.checked })} /> это я</label>
                ${m.is_me && html`<label title="Если выключено, модель получит запрет писать за этого персонажа (кроме случая, когда вы сами просите её написать за вас)">
                  <input type="checkbox" checked=${m.model_may_write}
                    onChange=${(e) => change(m, { model_may_write: e.target.checked })} /> модель может писать за него</label>`}
              </div>
            </li>`)}
        </ul>
        <div class="row">
          <select value=${adding} onChange=${(e) => setAdding(e.target.value)} aria-label="Персонаж">
            <option value="">— выбрать персонажа —</option>
            ${outside.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
          </select>
          <button class="primary" disabled=${!adding} onClick=${add}>Добавить</button>
          <button onClick=${() => onEditCharacter(null)}>Создать персонажа</button>
        </div>

        <h2>Пресет промпта этого чата</h2>
        <select value=${chat.meta.preset_id || ""} onChange=${(e) => choosePreset(e.target.value)}>
          <option value="">Общий${globalPreset ? ` (${globalPreset.name})` : ""}</option>
          ${presets.map((p) => html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
        </select>
      </div>
    </div>
  `;
}
