// The chat window (👥, owner 2026-11-22: tabs «Участники · Кто говорит · Пресет и лорбуки», a member is one line,
// its settings unfold with ⌄, hints sit behind «?»). "Участники" panel of a chat: who takes part, where each card goes (depth) and who is dropped first
// when the prompt does not fit (importance), which one is "me", and the chat's preset.
// Who speaks (any chat may have many characters, that is normal): who answers (order), what the model
// knows about the others, and per character talkativeness, "молчит", a short description and «Пусть ответит».
// Every change goes to the server at once; agents change the same things through the same API.
import { Select } from "./Select.js";
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { BookPicker, LoreOrder } from "./Lorebooks.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, useLoad } from "./State.js";
import { backdrop, useEscape } from "../core/keys.js";

const DEPTHS = [0, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20];
const IMPORTANCE = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const ORDERS = [
  ["natural", "Сам: кого назвали по имени, иначе по разговорчивости"],
  ["turns", "По очереди, по списку участников"],
  ["model", "Модель решает (ещё один короткий запрос)"],
  ["manual", "Только вручную, кнопкой «Пусть ответит»"],
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

// «Заменить»: every character, with a search; the chosen one takes the member's place (settings stay, the
// messages the member wrote are re-pointed on the server).
function CharacterPicker({ member, characters, taken, onPick, onClose }) {
  const [query, setQuery] = useState("");
  useEscape(onClose);
  const found = characters.filter((c) => c.id !== member.character_id
    && (c.name || "").toLowerCase().includes(query.trim().toLowerCase()));
  return html`<div class="overlay ask-overlay" ...${backdrop(onClose)}>
    <div class="dialog stack group-picker" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Заменить персонажа">
      <strong class="dialog-name" title=${member.name}>Заменить: ${member.name}</strong>
      <input type="search" value=${query} placeholder="Поиск по имени" aria-label="Поиск персонажа"
        class="group-picker-search" onInput=${(e) => setQuery(e.target.value)} />
      <ul class="list">
        ${found.length === 0 && html`<li class="muted" style="padding: 12px 0; text-align: center">
          ${query ? `По запросу «${query}» никого не нашлось` : "Персонажей для выбора пока нет"}
        </li>`}
        ${found.map((c) => html`<li key=${c.id}><button class="item" title=${c.name} disabled=${taken.has(c.id)}
          onClick=${() => onPick(c.id)}>${c.name}${taken.has(c.id) ? " (уже в чате)" : ""}</button></li>`)}
      </ul>
      <div class="row"><span class="spacer"></span><button onClick=${onClose}>Закрыть</button></div>
    </div></div>`;
}

// A hint behind a «?» (owner, 2026-11-22: dense, as in SillyTavern): the text shows on a tap, not always.
function Q({ children, label = "Пояснение" }) {
  return html`<details class="hint-q" data-slot="hint-q"><summary aria-label=${label} title=${label}>?</summary>
    <p class="hint">${children}</p></details>`;
}

// «Уже работают» on the books tab: the books on everywhere and on the members' cards, by name.
function WorkingBooks({ members }) {
  const ids = (members || []).map((m) => m.character_id).filter(Boolean).join(",");
  const { data } = useLoad(() => Promise.all([api.get("/lorebooks"), api.get("/lorebook-uses?scope=global"),
    ...(members || []).filter((m) => m.character_id).map((m) => api.get(`/lorebook-uses?scope=character&target=${m.character_id}`)
      .then((d) => ({ name: m.name, ids: d.lorebook_ids })))]), [ids]);
  if (!data) return null;
  const [books, global, ...own] = data;
  const name = (id) => (books.lorebooks.find((b) => b.id === id) || {}).name || "?";
  const lines = [
    global.lorebook_ids.length ? `везде: ${global.lorebook_ids.map(name).join(", ")}` : "",
    ...own.filter((o) => o.ids.length).map((o) => `у ${o.name}: ${o.ids.map(name).join(", ")}`),
  ].filter(Boolean);
  return html`<p class="muted working-books" data-slot="working-books">Уже работают в этом чате: ${lines.length
    ? lines.join("; ") : "других книг нет"}. Где работает книга, меняется и в её окне: «Подключён: …».</p>`;
}

const TABS_KEY = "tavern.chatWindowTab";

export function Members({ chat, characters, settings, generators = [], onClose, onChanged, onEditCharacter, onAnswer,
  onError }) {
  const [tab, setTab] = useState(() => { try { return localStorage.getItem(TABS_KEY) || "members"; } catch (e) { return "members"; } });
  const chooseTab = (id) => { setTab(id); try { localStorage.setItem(TABS_KEY, id); } catch (e) { /* storage off */ } };
  const [open, setOpen] = useState({}); // member key -> details unfolded
  const [members, setMembers] = useState(null); // null until loaded: «пока нет персонажей» must not flash
  const [loadError, setLoadError] = useState("");
  const [speaking, setSpeaking] = useState({ order: "natural", others: "none", talk_limit: 4, together: "finished",
    narrator: { enabled: false, name: "Рассказчик", connection_id: "", model: "" }, ...(chat.meta.speakers || {}) });
  const [connections, setConnections] = useState([]);
  const [narratorName, setNarratorName] = useState(null); // typed, not saved yet
  const [narratorModel, setNarratorModel] = useState(null);
  const [presets, setPresets] = useState([]);
  const [adding, setAdding] = useState("");
  const [blurbs, setBlurbs] = useState({}); // typed, not saved yet: a re-render must not wipe them
  const [talk, setTalk] = useState({}); // talkativeness typed or dragged, not saved yet (by member key)
  const [replacing, setReplacing] = useState(null); // the member whose «Заменить» picker is open

  useEscape(onClose);
  const loadMembers = () => {
    setLoadError("");
    api.get(`/chats/${chat.id}/members`).then((d) => setMembers(d.members)).catch((e) => setLoadError(e.message));
  };
  useEffect(() => {
    loadMembers();
    api.get("/presets").then((d) => setPresets(d.presets)).catch((e) => onError(e.message));
    api.get("/connections").then((d) => setConnections(d.connections)).catch((e) => onError(e.message));
  }, [chat.id]);

  // A failed save puts the controls back to what the server has.
  const apply = (promise) => promise.then((d) => { setMembers(d.members); onChanged(); })
    .catch((e) => { setMembers((list) => list && [...list]); onError(e.message); });
  // A member is addressed by `slot_id`: its character id, or the id of an empty place (an imported character
  // this install does not have: no card, never speaks).
  const change = (m, patch) => apply(api.put(`/chats/${chat.id}/members/${m.slot_id}`, patch));
  const replace = (m, characterId) => {
    setReplacing(null);
    apply(api.post(`/chats/${chat.id}/members/${m.slot_id}/replace`, { character_id: characterId }));
  };
  const saveTalk = (m, raw) => {
    const value = Math.max(0, Math.min(100, Math.round(Number(raw))));
    setTalk((t) => { const { [m.slot_id]: _, ...rest } = t; return rest; });
    if (Number.isFinite(value) && value !== m.talkativeness) return change(m, { talkativeness: value });
    return undefined;
  };
  const remove = async (m) => {
    if (await ask(`Убрать «${m.name}» из этого чата?`, { ok: "Убрать", danger: true, skip: "member",
      hint: "Сам персонаж останется в библиотеке. Сообщения, уже написанные им в этом чате, сохранятся под его именем." })) {
      apply(api.del(`/chats/${chat.id}/members/${m.slot_id}`));
    }
  };
  const add = () => {
    if (!adding) return;
    apply(api.post(`/chats/${chat.id}/members`, { character_id: adding }));
    setAdding("");
  };
  const changeSpeaking = (patch) => api.put(`/chats/${chat.id}/speakers`, patch)
    .then((d) => { setSpeaking(d.speakers); onChanged(); }).catch((e) => onError(e.message));
  const choosePreset = (id) => api.put(`/chats/${chat.id}/meta`, { preset_id: id })
    .then(onChanged).catch((e) => onError(e.message));

  const outside = characters.filter((c) => !(members || []).some((m) => m.character_id === c.id));
  const taken = new Set((members || []).map((m) => m.character_id).filter(Boolean));
  const globalPreset = presets.find((p) => p.id === (settings.active_preset || "default"));

  const key = (m) => m.slot_id;
  const toggleOpen = (m) => setOpen((o) => ({ ...o, [key(m)]: !o[key(m)] }));
  const TABS = [["members", "Участники"], ["speak", "Кто говорит"], ["books", "Пресет и лорбуки"]];

  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog chat-window" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Чат: участники, кто говорит, пресет и лорбуки">
        <div class="side-head"><strong>Чат</strong>
          <${Q} label="Что здесь">Здесь — кто в этом чате, кто отвечает, пресет и лорбуки этого чата. Сами тексты указаний
            модели (не писать за вас, рассказчик, кто отвечает) лежат в пресете промпта: настройки, вкладка «Промпт».<//>
          <span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        <div class="tabs" role="tablist" aria-label="Разделы окна чата">
          ${TABS.map(([id, label]) => html`<button key=${id} role="tab" aria-selected=${tab === id}
            data-slot=${"chat-window-tab-" + id} onClick=${() => chooseTab(id)}>${label}</button>`)}
        </div>

        ${tab === "members" && html`
        ${!members && html`<${Pending} error=${loadError} onRetry=${loadMembers} />`}
        <ul class="members members-compact">
          ${members && members.length === 0 && html`<li class="empty-note" style="padding: 16px; margin-bottom: 12px">
            <p class="empty-title">В этом чате пока нет участников</p>
            <p class="muted empty-hint">Выберите персонажа в поле ниже и нажмите «Добавить», либо создайте нового персонажа для этой истории.</p>
          </li>`}
          ${(members || []).map((m) => html`
            <li key=${key(m)} class=${"member" + (m.is_me ? " me" : "") + (m.empty ? " empty" : "") + (open[key(m)] ? " open" : "")} data-slot="member">
              <div class="row member-head">
                <button type="button" class="member-fold" aria-expanded=${Boolean(open[key(m)])}
                  title=${open[key(m)] ? "Свернуть" : "Настройки участника"} onClick=${() => toggleOpen(m)}>
                  ${m.has_avatar && html`<img class="msg-avatar member-avatar" src=${`/api/characters/${m.character_id}/avatar`} alt="" />`}
                  <strong class=${"member-name" + (m.empty ? " muted" : "")}>${m.name}</strong>
                  ${m.empty && html`<span class="badge muted" data-slot="member-empty">нет карточки</span>`}
                  ${m.is_me && html`<span class="badge badge-accent">это я</span>`}
                  ${m.character_id && m.character_id === chat.character_id && html`<span class="badge">главный</span>`}
                  ${!m.is_me && !m.empty && html`<span class="muted member-facts">${m.muted ? "молчит" : `${m.talkativeness}%`}</span>`}
                  <${Icon} name="chevron-down" class="member-chevron" />
                </button>
                <span class="spacer"></span>
                <div class="member-actions">
                  ${!m.is_me && !m.empty && html`<${IconButton} icon="message-circle-reply" label="Пусть ответит"
                    onClick=${() => onAnswer(m.character_id)} />`}
                  ${!m.empty && html`<${IconButton} icon="pencil" label="Карточка персонажа"
                    onClick=${() => onEditCharacter(m.character_id)} />`}
                  <${IconButton} icon="x" label="Убрать из чата" onClick=${() => remove(m)} />
                </div>
              </div>
              ${m.empty && html`<p class="hint">Пустое место: в импортированном чате был персонаж «${m.name}», но его карточки нет.
                Нажмите «Заменить» и выберите своего — прежние реплики станут его репликами.</p>`}

              ${(open[key(m)] || m.empty) && html`<div class="member-body" data-slot="member-body">
                <div class="row member-line">
                  <button class="member-replace-btn" data-slot="member-replace" onClick=${() => setReplacing(m)}>Заменить</button>
                  <${Q}>Другой персонаж займёт это место: настройки места сохранятся, прежние реплики станут его репликами.<//>
                </div>
                ${!m.empty && html`<div class="row member-line">
                  <label class="check"><input type="checkbox" checked=${m.is_me}
                    onChange=${(e) => change(m, { is_me: e.target.checked })} /> это я (мой герой)</label>
                  <${Q}>Персонаж, за которого играете вы. Его карточка передаётся модели как описание вашего героя.<//>
                </div>`}
                ${m.is_me && html`<div class="row member-line">
                  <label class="check"><input type="checkbox" checked=${m.model_may_write}
                    onChange=${(e) => change(m, { model_may_write: e.target.checked })} /> модель может писать за него</label>
                  <${Q}>${m.model_may_write
                    ? "Модель может свободно создавать реплики и действия вашего героя."
                    : "Модель не пишет за вашего героя, пока вы не попросите напрямую. Формулировка — в пресете (блок «Не писать за меня»)."}<//>
                </div>`}

                ${!m.is_me && !m.empty && html`
                  <div class="row member-line" data-slot="member-talkativeness">
                    <span>Разговорчивость</span>
                    <input type="range" min="0" max="100" step="1" value=${talk[m.slot_id] ?? m.talkativeness}
                      aria-label=${`Разговорчивость ${m.name}`} class="member-talk-slider"
                      style="flex: 1; min-width: 100px; min-height: var(--tap)"
                      onInput=${(e) => setTalk((t) => ({ ...t, [m.slot_id]: e.target.value }))}
                      onChange=${(e) => saveTalk(m, e.target.value)} />
                    <input type="number" min="0" max="100" step="1" inputmode="numeric"
                      value=${talk[m.slot_id] ?? m.talkativeness} aria-label=${`Разговорчивость ${m.name}, число`}
                      class="num member-talk-num" style="width: 68px; font-size: 16px"
                      onInput=${(e) => setTalk((t) => ({ ...t, [m.slot_id]: e.target.value }))}
                      onChange=${(e) => saveTalk(m, e.target.value === "" ? m.talkativeness : e.target.value)} />
                    <${Q}>Насколько охотно персонаж отвечает сам, когда никто не назван по имени.<//>
                  </div>
                  <div class="row member-line">
                    <label class="check"><input type="checkbox" checked=${m.muted}
                      onChange=${(e) => change(m, { muted: e.target.checked })} /> молчит</label>
                    <${Q}>Временно не отвечает сам и не попадает в промпт, пока вы снова не включите.<//>
                  </div>
                  <div class="row member-line" data-slot="member-blurb">
                    <input class="grow" value=${blurbs[m.slot_id] ?? m.blurb} maxLength="500" aria-label="Коротко о нём для остальных"
                      placeholder="Коротко о нём для остальных: старый кузнец, ворчит"
                      onInput=${(e) => setBlurbs((b) => ({ ...b, [m.slot_id]: e.target.value }))}
                      onChange=${(e) => change(m, { blurb: e.target.value }).then(() => setBlurbs((b) => {
                        const { [m.slot_id]: _, ...rest } = b;
                        return rest;
                      }))} />
                    <${Q}>Справка, которую модель видит, когда пишет за других персонажей (режим «имена и короткие описания» на вкладке «Кто говорит»).<//>
                  </div>`}

                <div class="row member-line">
                  <label>Карточка в промпте: глубина
                    <${Select} value=${m.depth === null ? "top" : String(m.depth)}
                      onChange=${(e) => change(m, { depth: e.target.value === "top" ? null : Number(e.target.value) })}>
                      <option value="top">в начале</option>
                      ${[...new Set([...DEPTHS, ...(m.depth === null ? [] : [m.depth])])].sort((a, b) => a - b)
                        .map((d) => html`<option key=${d} value=${String(d)}>${d}</option>`)}
                    </${Select}></label>
                  <label>важность
                    <${Select} value=${String(m.importance)} onChange=${(e) => change(m, { importance: Number(e.target.value) })}>
                      ${IMPORTANCE.map((i) => html`<option key=${i} value=${String(i)}>${i}</option>`)}
                    </${Select}></label>
                  <${Q}>Глубина: за сколько сообщений до конца чата вставлять карточку (ближе к концу — модель сильнее её держится).
                    Важность: 1 — самый важный; когда не хватает места, первыми отсекаются карточки с большим числом.<//>
                </div>
              </div>`}
            </li>`)}
        </ul>
        ${replacing && html`<${CharacterPicker} member=${replacing} characters=${characters} taken=${taken}
          onPick=${(id) => replace(replacing, id)} onClose=${() => setReplacing(null)} />`}
        <div class="row member-add-row" data-slot="member-add-row">
          <${Select} value=${adding} onChange=${(e) => setAdding(e.target.value)} aria-label="Персонаж">
            <option value="">Выберите персонажа</option>
            ${outside.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
          </${Select}>
          <button class="primary" disabled=${!adding} onClick=${add}>Добавить</button>
          <button onClick=${() => onEditCharacter(null)}>Создать персонажа</button>
        </div>`}

        ${tab === "speak" && html`
        <div class="stack" data-slot="speaker-settings">
          <p class="hint">Общие правила для всего чата. Ответить может любой участник: «Ответит: имя» под чатом.</p>
          <label class="stack">Кто отвечает на ваше сообщение
            <${Select} value=${speaking.order} onChange=${(e) => changeSpeaking({ order: e.target.value })}>
              ${ORDERS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
            </${Select}></label>
          <div class="row member-line"><label class="stack grow">Что модель знает об остальных, когда пишет за одного
            <${Select} value=${speaking.others} onChange=${(e) => changeSpeaking({ others: e.target.value })}>
              ${OTHERS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
            </${Select}></label>
            <${Q}>Чем больше сведений, тем точнее модель учитывает других, но тем больше токенов. Фраза «за кого писать» — в пресете (блок «Кто отвечает сейчас»).<//></div>
          <div class="row member-line"><label>«Пусть поговорят»: ответов подряд
            <input type="number" min="1" max="50" value=${speaking.talk_limit} class="num" style="width: 68px"
              onChange=${(e) => changeSpeaking({ talk_limit: Math.max(1, Math.min(50, Number(e.target.value) || 1)) })} /></label>
            <${Q}>Сколько реплик подряд персонажи могут написать друг другу без вашего ответа.<//></div>
        </div>

        ${speaking.order === "all" && html`<div class="members-section-card" data-slot="together-card">
          <h2 class="members-section-title">Все сразу</h2>
          <div class="stack" data-slot="together-settings">
            <label class="stack">Как показывать ответы всех сразу
              <${Select} value=${speaking.together} onChange=${(e) => changeSpeaking({ together: e.target.value })}>
                ${TOGETHER.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
              </${Select}></label>
            <div class="row member-line"><label class="check">
              <input type="checkbox" checked=${speaking.narrator.enabled}
                onChange=${(e) => changeSpeaking({ narrator: { enabled: e.target.checked } })} />
              <span>Рассказчик сводит ход в одну сцену</span></label>
              <${Q}>После хода всех сразу ещё один запрос сводит их действия в одну сцену. Текст указания — в пресете,
                блок «Рассказчик после хода «все сразу»». Имя подписывает его сообщение; подключение и модель можно взять подешевле.<//></div>
            ${speaking.narrator.enabled && html`<div class="narrator-grid" data-slot="narrator-settings">
              <label>Имя <input value=${narratorName ?? speaking.narrator.name} maxLength="200"
                onInput=${(e) => setNarratorName(e.target.value)}
                onChange=${(e) => changeSpeaking({ narrator: { name: e.target.value } }).then(() => setNarratorName(null))} /></label>
              <label>Подключение <${Select} value=${speaking.narrator.connection_id}
                onChange=${(e) => changeSpeaking({ narrator: { connection_id: e.target.value } })}>
                <option value="">как у чата</option>
                ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
              </${Select}></label>
              <label>Модель <input value=${narratorModel ?? speaking.narrator.model} placeholder="как у подключения"
                onInput=${(e) => setNarratorModel(e.target.value)}
                onChange=${(e) => changeSpeaking({ narrator: { model: e.target.value } }).then(() => setNarratorModel(null))} /></label>
            </div>`}
          </div>
        </div>`}

        ${(generators.length > 1 || chat.meta.generator) && html`<div class="row member-line">
          <label class="stack grow">Ответ пишет
          <${Select} data-slot="generator-pick" aria-label="Ответ пишет" value=${chat.meta.generator || ""}
            onChange=${(e) => api.put(`/chats/${chat.id}/meta`, { generator: e.target.value }).then(onChanged)
              .catch((err) => onError(err.message))}>
            <option value="">Обычный способ</option>
            ${generators.filter((g) => g.id !== "default").map((g) => html`<option key=${g.id} value=${g.id}>${g.name}</option>`)}
            ${chat.meta.generator && !generators.some((g) => g.id === chat.meta.generator) && html`<option
              value=${chat.meta.generator}>${chat.meta.generator} (дополнение выключено — пишет обычный способ)</option>`}
          </${Select}></label>
          <${Q}>Дополнение может писать ответы по-своему (например, сначала «подумать» за каждого персонажа). Его запросы к модели идут по правилам вкладки «Дополнения».<//></div>`}`}

        ${tab === "books" && html`
        <div class="row member-line" data-slot="chat-preset-card">
          <label class="stack grow">Пресет промпта этого чата
            <${Select} value=${chat.meta.preset_id || ""} onChange=${(e) => choosePreset(e.target.value)}>
              <option value="">Общий${globalPreset ? ` (${globalPreset.name})` : ""}</option>
              ${presets.map((p) => html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
            </${Select}></label>
          <${Q}>Порядок и тексты всего, что уходит модели. «Общий» — пресет из настроек.<//>
        </div>
        <${BookPicker} scope="chat" target=${chat.id} title="Лорбуки этого чата" onError=${onError} />
        <${WorkingBooks} members=${members} />
        <${LoreOrder} chatId=${chat.id} onError=${onError} />`}
      </div>
    </div>
  `;
}
