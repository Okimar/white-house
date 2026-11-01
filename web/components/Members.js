// "Участники" panel of a chat: who takes part, where each card goes (depth) and who is dropped first
// when the prompt does not fit (importance), which one is "me", and the chat's preset.
// Who speaks (any chat may have many characters, that is normal): who answers (order), what the model
// knows about the others, and per character talkativeness, "молчит", a short description and "ответь ты".
// Every change goes to the server at once; agents change the same things through the same API.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { BookPicker } from "./Lorebooks.js";

const DEPTHS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20];
const IMPORTANCE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const ORDERS = [
  ["natural", "Сам: кого назвали по имени, иначе по разговорчивости"],
  ["turns", "По очереди, по списку участников"],
  ["model", "Модель решает (ещё один короткий запрос)"],
  ["manual", "Только вручную: «ответь ты»"],
  ["all", "Все сразу, не видя ответов друг друга"],
];
const TOGETHER = [
  ["finished", "Отдельными сообщениями: кто закончил первым — тот выше"],
  ["list", "Отдельными сообщениями: по порядку участников"],
  ["one", "Одним сообщением с именами"],
];
const OTHERS = [
  ["none", "Только карточка говорящего"],
  ["names", "Говорящий + имена остальных"],
  ["blurbs", "Говорящий + имена и короткие описания"],
  ["full", "Полные карточки всех (с глубиной и важностью)"],
];

export function Members({ chat, characters, settings, generators = [], onClose, onChanged, onEditCharacter, onAnswer,
  onError }) {
  const [members, setMembers] = useState([]);
  const [speaking, setSpeaking] = useState({ order: "natural", others: "none", talk_limit: 4, together: "finished",
    narrator: { enabled: false, name: "Рассказчик", connection_id: "", model: "" }, ...(chat.meta.speakers || {}) });
  const [connections, setConnections] = useState([]);
  const [narratorName, setNarratorName] = useState(null); // typed, not saved yet
  const [narratorModel, setNarratorModel] = useState(null);
  const [presets, setPresets] = useState([]);
  const [adding, setAdding] = useState("");
  const [blurbs, setBlurbs] = useState({}); // typed, not saved yet: a re-render must not wipe them

  useEffect(() => {
    api.get(`/chats/${chat.id}/members`).then((d) => setMembers(d.members)).catch((e) => onError(e.message));
    api.get("/presets").then((d) => setPresets(d.presets)).catch((e) => onError(e.message));
    api.get("/connections").then((d) => setConnections(d.connections)).catch((e) => onError(e.message));
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
  const changeSpeaking = (patch) => api.put(`/chats/${chat.id}/speakers`, patch)
    .then((d) => { setSpeaking(d.speakers); onChanged(); }).catch((e) => onError(e.message));
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
                ${!m.is_me && html`<button title="Ответь ты: следующий ответ напишет этот персонаж"
                  onClick=${() => onAnswer(m.character_id)}>🗣</button>`}
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
              ${!m.is_me && html`<div class="row" data-slot="member-speaking">
                <label title="Насколько охотно вступает в разговор, когда никого не назвали по имени">Разговорчивость
                  <input type="range" min="0" max="100" step="10" value=${m.talkativeness}
                    onChange=${(e) => change(m, { talkativeness: Number(e.target.value) })} />
                  <span class="muted">${m.talkativeness}%</span></label>
                <label title="Не отвечает сам и не попадает в промпт, пока не включите обратно">
                  <input type="checkbox" checked=${m.muted} onChange=${(e) => change(m, { muted: e.target.checked })} /> молчит</label>
              </div>
              <label class="stack" data-slot="member-blurb">Коротко о нём для остальных (одна-две строки)
                <input value=${blurbs[m.character_id] ?? m.blurb} maxLength="500"
                  placeholder="например: старый кузнец, ворчит"
                  onInput=${(e) => setBlurbs((b) => ({ ...b, [m.character_id]: e.target.value }))}
                  onChange=${(e) => change(m, { blurb: e.target.value }).then(() => setBlurbs((b) => {
                    const { [m.character_id]: _, ...rest } = b;
                    return rest;
                  }))} /></label>`}
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

        <h2>Кто говорит</h2>
        <div class="stack" data-slot="speaker-settings">
          <label class="stack">Кто отвечает на ваше сообщение
            <select value=${speaking.order} onChange=${(e) => changeSpeaking({ order: e.target.value })}>
              ${ORDERS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
            </select></label>
          <label class="stack">Что модель знает об остальных, когда пишет за одного
            <select value=${speaking.others} onChange=${(e) => changeSpeaking({ others: e.target.value })}>
              ${OTHERS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
            </select></label>
          <label>«Пусть поговорят»: ответов подряд
            <input type="number" min="1" max="50" value=${speaking.talk_limit}
              onChange=${(e) => changeSpeaking({ talk_limit: Math.max(1, Math.min(50, Number(e.target.value) || 1)) })} /></label>
          ${speaking.order === "all" && html`<div class="stack" data-slot="together-settings">
            <label class="stack">Как показывать ответы всех сразу
              <select value=${speaking.together} onChange=${(e) => changeSpeaking({ together: e.target.value })}>
                ${TOGETHER.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
              </select></label>
            <label title="После хода всех сразу ещё один запрос сводит их действия в одну сцену">
              <input type="checkbox" checked=${speaking.narrator.enabled}
                onChange=${(e) => changeSpeaking({ narrator: { enabled: e.target.checked } })} /> Рассказчик сводит ход в одну сцену</label>
            ${speaking.narrator.enabled && html`<div class="row" data-slot="narrator-settings">
              <label>Имя <input value=${narratorName ?? speaking.narrator.name} maxLength="200"
                onInput=${(e) => setNarratorName(e.target.value)}
                onChange=${(e) => changeSpeaking({ narrator: { name: e.target.value } }).then(() => setNarratorName(null))} /></label>
              <label>Подключение <select value=${speaking.narrator.connection_id}
                onChange=${(e) => changeSpeaking({ narrator: { connection_id: e.target.value } })}>
                <option value="">как у чата</option>
                ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
              </select></label>
              <label>Модель <input value=${narratorModel ?? speaking.narrator.model} placeholder="как у подключения"
                onInput=${(e) => setNarratorModel(e.target.value)}
                onChange=${(e) => changeSpeaking({ narrator: { model: e.target.value } }).then(() => setNarratorModel(null))} /></label>
            </div>`}
            <p class="muted">Текст указания рассказчику — в пресете (⚙ → Промпт → Служебные тексты).</p>
          </div>`}
          <p class="muted">Ответить может любой: кнопка 🗣 здесь или имя под чатом. Молчащий не отвечает сам и
            не попадает в промпт.</p>
        </div>

        <h2>Пресет промпта этого чата</h2>
        <select value=${chat.meta.preset_id || ""} onChange=${(e) => choosePreset(e.target.value)}>
          <option value="">Общий${globalPreset ? ` (${globalPreset.name})` : ""}</option>
          ${presets.map((p) => html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
        </select>

        ${(generators.length > 1 || chat.meta.generator) && html`<h2>Ответ пишет</h2>
        <p class="muted">Дополнение может писать ответы по-своему (например, сначала «подумать» за каждого персонажа).
          Его запросы к модели идут по правилам из ⚙ → «Дополнения».</p>
        <select data-slot="generator-pick" aria-label="Ответ пишет" value=${chat.meta.generator || ""}
          onChange=${(e) => api.put(`/chats/${chat.id}/meta`, { generator: e.target.value }).then(onChanged)
            .catch((err) => onError(err.message))}>
          <option value="">Обычный способ</option>
          ${generators.filter((g) => g.id !== "default").map((g) => html`<option key=${g.id} value=${g.id}>${g.name}</option>`)}
          ${chat.meta.generator && !generators.some((g) => g.id === chat.meta.generator) && html`<option
            value=${chat.meta.generator}>${chat.meta.generator} (дополнение выключено — пишет обычный способ)</option>`}
        </select>`}

        <${BookPicker} scope="chat" target=${chat.id} title="Лорбуки этого чата" onError=${onError}
          hint="Вместе с ними работают общие лорбуки (⚙ → Лорбуки) и лорбуки участников (в их карточках)." />
      </div>
    </div>
  `;
}
