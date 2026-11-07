// The assistant over the chat (stage 8б, engine of 10а): a window that floats over the app, dragged by
// its header (full width on a phone), folded to a strip. Conversations are a list like chats;
// what the assistant changed is shown as action lines with "Отменить" (that one and every later one);
// deleting waits for "Разрешить". Big work comes as cards: questions with ready answers, a plan that
// waits for «Начать» and ticks its items off as they are done, progress lines, the critic's remarks and
// the report. Writing while it works adds to the plan. The «Настройки» item of the header menu holds
// its model, the model of each role, the critic, how much of the work to show, its limits and
// «Что я о вас знаю». The saved position is clamped to the screen whenever it is applied.
import { Select } from "./Select.js";
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api, followRun } from "../core/api.js";
import { money } from "../core/spending.js";
import { notify } from "../core/notify.js";
import { Icon, IconButton } from "./Icon.js";
import { ask, askText } from "./Dialogs.js";
import { MenuButton, openMenu } from "./Menu.js";
import { Pending, EmptyNote } from "./State.js";
import { OPEN_SKILL_KEY } from "./Skills.js";

const POS_KEY = "tavern.assistant.pos";
const CHAT_KEY = "tavern.assistant.chat";
const ROLE_LABELS = [
  ["planner", "Думает и планирует", "вопросы и план"],
  ["writer", "Пишет", "делает пункты плана"],
  ["critic", "Критик", "проверяет результат"],
  ["small", "Мелкая работа", "вносит ваши слова в план, пишет итог"],
];
const SHOWN_STEP = 40; // conversations drawn at once: hundreds stay quick
const TEXT_LIMIT = "200 КБ"; // services/assistant_files.MAX_TEXT_BYTES
const MAX_FILES = 6; // services/assistant_files.MAX_ATTACHMENTS
const PHASES = { questions: "Готовит вопросы…", plan: "Составляет план…", critic: "Критик читает…", report: "Пишет итог…" };

function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not remembered */ }
}

// "5 мин назад", "вчера", "3 ноя": when the conversation was last touched.
function ago(iso) {
  const then = Date.parse(iso);
  if (!then) return "";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.round(hours / 24);
  if (days === 1) return "вчера";
  if (days < 7) return `${days} дн. назад`;
  return new Date(then).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

// The list of conversations: newest first, a search by name, a row per conversation with its time, the
// «работает» mark, rename and delete. Only the first rows are drawn; «Показать ещё» adds more.
function ConversationList({ chats, current, onPick, onNew, onRename, onDelete, onImport }) {
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(SHOWN_STEP);
  const needle = query.trim().toLowerCase();
  const found = needle ? chats.filter((c) => (c.title || "Без названия").toLowerCase().includes(needle)) : chats;
  const sorted = [...found].sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
  return html`<div class="assistant-convs" data-slot="assistant-convs">
    <div class="assistant-convs-top">
      <input type="search" value=${query} placeholder="Найти беседу" aria-label="Найти беседу"
        onInput=${(e) => { setQuery(e.target.value); setShown(SHOWN_STEP); }} />
      <button class="primary" onClick=${onNew}><${Icon} name="plus" /> Новая</button>
    </div>
    <div class="assistant-conv-rows" role="list">
      ${sorted.slice(0, shown).map((c) => html`<div key=${c.id} role="listitem"
          class=${"assistant-conv" + (c.id === current ? " current" : "")} data-slot="assistant-conv">
        <button class="assistant-conv-open" onClick=${() => onPick(c.id)}>
          <span class="assistant-conv-title">${c.running ? html`<span class="assistant-running" title="Работает">● работает: </span>` : ""}${c.title || "Без названия"}</span>
          <small class="muted">${ago(c.updated_at)}${c.imported ? " · из файла" : ""}</small>
        </button>
        <${IconButton} icon="pencil" label="Переименовать" onClick=${() => onRename(c)} />
        <${IconButton} icon="trash-2" label="Удалить беседу" disabled=${c.running} onClick=${() => onDelete(c)} />
      </div>`)}
      ${sorted.length > shown && html`<button class="link" onClick=${() => setShown(shown + SHOWN_STEP)}>Показать ещё (${sorted.length - shown})</button>`}
      ${sorted.length === 0 && html`<p class="muted">${needle ? "Ничего не нашлось." : "Бесед пока нет."}</p>`}
    </div>
    <button class="link" onClick=${onImport}><${Icon} name="file-up" /> Загрузить беседу из файла</button>
  </div>`;
}

// Pictures of the image library to attach to a request.
function LibraryPicker({ taken, onPick, onClose, onError }) {
  const [assets, setAssets] = useState(null);
  useEffect(() => { api.get("/assets").then((r) => setAssets(r.assets)).catch((e) => { onError(e.message); onClose(); }); }, []);
  return html`<div class="assistant-library" data-slot="assistant-library">
    <div class="row"><strong>Картинки из библиотеки</strong><span class="spacer"></span>
      <${IconButton} icon="x" label="Закрыть выбор" onClick=${onClose} /></div>
    ${assets === null ? html`<p class="muted">Загрузка…</p>`
      : assets.length === 0 ? html`<p class="muted">В библиотеке пока нет картинок.</p>`
      : html`<div class="assistant-library-grid">${assets.filter((a) => !a.name.toLowerCase().endsWith(".svg")).map((a) => html`
        <button key=${a.name} type="button" disabled=${taken.includes(a.name)} title=${a.name}
          onClick=${() => onPick(a.name)}><img src=${"/assets/" + encodeURIComponent(a.name)} alt=${a.name} loading="lazy" /></button>`)}</div>`}
  </div>`;
}

function Memory({ memory, onChange, onError }) {
  const [text, setText] = useState("");
  const add = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    try { await api.post("/assistant/memory", { text }); setText(""); onChange(); } catch (err) { onError(err.message); }
  };
  const edit = async (fact, value) => {
    if (value.trim() === fact.text) return;
    try {
      if (!value.trim()) {
        if (!await ask(`Забыть запись «${fact.text}»?`, { ok: "Забыть", danger: true })) return false;
        await api.del(`/assistant/memory/${fact.id}`);
      } else await api.put(`/assistant/memory/${fact.id}`, { text: value });
      onChange();
    } catch (err) { onError(err.message); }
    return true;
  };
  const forget = (fact) => api.del(`/assistant/memory/${fact.id}`).then(onChange).catch((e) => onError(e.message));
  return html`<div class="assistant-memory" data-slot="assistant-memory">
    <strong>Что я о вас знаю</strong>
    <p class="hint">Это видит помощник в любой работе. Он сам записывает сюда ваши вкусы; правьте и удаляйте.</p>
    ${memory.map((fact) => html`<div class="row" key=${fact.id}>
      <input value=${fact.text} aria-label="Запись памяти" onChange=${async (e) => { const input = e.target; if (await edit(fact, input.value) === false) input.value = fact.text; }} />
      <${IconButton} icon="trash-2" label="Забыть" onClick=${() => forget(fact)} />
    </div>`)}
    <form class="row" onSubmit=${add}>
      <input value=${text} placeholder="Например: люблю мрачное фэнтези" onInput=${(e) => setText(e.target.value)} />
      <${IconButton} type="submit" icon="plus" label="Добавить запись" disabled=${!text.trim()} />
    </form>
  </div>`;
}

function AssistantSettings({ overview, onSaved, onMemory, onOpenSkills, onError }) {
  const s = overview.settings;
  const cur = overview.money.currency.code || "$";
  const [models, setModels] = useState([]);
  useEffect(() => {
    // Model names the connections offer, as hints for the roles' model fields.
    const ids = overview.sources.filter((o) => o.id.startsWith("connection:")).map((o) => o.id.slice(11));
    Promise.all(ids.map((id) => api.get(`/connections/${id}/models`).then((r) => r.models).catch(() => [])))
      .then((lists) => setModels([...new Set(lists.flat().map((m) => (typeof m === "string" ? m : m.id)))].sort()));
  }, []);
  const save = async (changes) => {
    try { onSaved(await api.put("/assistant/settings", { ...s, ...changes })); } catch (e) { onError(e.message); }
  };
  const number = (key) => (e) => { const v = Number(e.target.value); if (v !== s[key] && v >= 0) save({ [key]: v }); };
  const role = (name, changes) => save({ roles: { ...s.roles, [name]: { ...s.roles[name], ...changes } } });
  return html`<div class="assistant-settings" data-slot="assistant-settings">
    <label>Модель помощника<br /><${Select} value=${s.source} onChange=${(e) => save({ source: e.target.value })}>
      ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.label}</option>`)}
    </${Select}></label>
    <details>
      <summary>Модели для большой работы</summary>
      <p class="hint">Пусто — модель помощника.</p>
      ${ROLE_LABELS.map(([name, label, hint]) => html`<div class="assistant-role" key=${name}>
        <span><strong>${label}</strong><br /><small class="muted">${hint}</small></span>
        <${Select} aria-label=${label + ": подключение"} value=${s.roles[name].source}
          onChange=${(e) => role(name, { source: e.target.value })}>
          ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.id ? o.label : "Как у помощника"}</option>`)}
        </${Select}>
        <input list="assistant-models" aria-label=${label + ": модель"} placeholder="как у подключения"
          value=${s.roles[name].model} onChange=${(e) => role(name, { model: e.target.value.trim() })} />
      </div>`)}
      <datalist id="assistant-models">${models.map((m) => html`<option key=${m} value=${m} />`)}</datalist>
    </details>
    <div class="row">
      <label><input type="checkbox" checked=${s.critic} onChange=${(e) => save({ critic: e.target.checked })} />
        Проверять результат критиком</label>
      <label>Сколько раз проверять<br /><input type="number" min="1" max="5" value=${s.critic_rounds}
        disabled=${!s.critic} onChange=${number("critic_rounds")} /></label>
    </div>
    <label class="assistant-auto"><input type="checkbox" checked=${Boolean(s.auto)} onChange=${(e) => save({ auto: e.target.checked })} />
      Режим авто: не спрашивать разрешения</label>
    <p class="hint">Включено: помощник сам удаляет и меняет, не спрашивая «Разрешить?». Остаются «Отменить» у каждого
      изменения и копия данных перед рискованным действием (если копии у вас включены). Ключи, восстановление копии и
      установка дополнений для него по-прежнему закрыты.</p>
    <label class="assistant-web"><input type="checkbox" checked=${s.web !== false} onChange=${(e) => save({ web: e.target.checked })} />
      Интернет для помощника</label>
    <p class="hint">Включено: помощник может искать в интернете и читать страницы, а ещё скачивать инструкции по ссылке
      (это он делает только с вашего «Разрешить», кроме режима авто). Слова поиска уходят поисковому сервису, адреса страниц —
      этим сайтам; больше ничего из приложения наружу не отправляется. Адреса вашей сети и этого устройства для него закрыты.</p>
    ${s.web !== false && html`<div class="row">
      <label>Поиск<br /><${Select} value=${s.web_engine || "duckduckgo"} onChange=${(e) => save({ web_engine: e.target.value })}>
        <option value="duckduckgo">DuckDuckGo (без ключа)</option>
        <option value="searxng">Свой SearXNG</option>
      </${Select}></label>
      ${s.web_engine === "searxng" && html`<label>Адрес SearXNG<br /><input defaultValue=${s.web_url || ""}
        placeholder="http://192.168.1.5:8080" onChange=${(e) => save({ web_url: e.target.value.trim() })} /></label>`}
    </div>`}
    <label>Ход работы<br /><${Select} value=${s.progress} onChange=${(e) => save({ progress: e.target.value })}>
      <option value="full">Показывать всё</option>
      <option value="minimal">Только короткие строки</option>
    </${Select}></label>
    <div class="row">
      <label>Шагов на одну просьбу<br /><input type="number" min="1" max="50" value=${s.max_steps}
        onChange=${number("max_steps")} /></label>
      <label>Сколько сообщений читать<br /><input type="number" min="0" value=${s.read_messages}
        onChange=${number("read_messages")} /></label>
    </div>
    <p class="hint">Последние сообщения чата помощник читает сразу, остальное находит поиском. 0 — читать весь чат.</p>
    <div class="row">
      <label>Лимит в день, ${cur}<br /><input type="number" min="0" step="any" value=${s.day} onChange=${number("day")} /></label>
      <label>Лимит в месяц, ${cur}<br /><input type="number" min="0" step="any" value=${s.month} onChange=${number("month")} /></label>
    </div>
    <p class="hint">${`Потрачено сегодня: ${money(overview.spent.day.cost, overview.money)} (запросов: ${overview.spent.day.requests}), ` +
      `за месяц: ${money(overview.spent.month.cost, overview.money)}. 0 — без лимита; общие лимиты тоже действуют.`}</p>
    <${Memory} memory=${overview.memory || []} onChange=${onMemory} onError=${onError} />
    <button class="link" onClick=${() => onOpenSkills(null)}><${Icon} name="book-open" /> Инструкции помощника</button>
  </div>`;
}

const MARKS = { done: "check", undone: "undo-2", denied: "circle-x", failed: "x" };
const AFTER = { undone: " — отменено", denied: " — не разрешено" };

function ActionLine({ action, onUndo }) {
  return html`<div class=${"assistant-action " + action.status} data-slot="assistant-action">
    <span>${MARKS[action.status] && html`<${Icon} name=${MARKS[action.status]} /> `}${action.what}${AFTER[action.status] || ""}</span>
    ${action.status === "done" && html`<button class="link" onClick=${() => onUndo(action)}>Отменить</button>`}
  </div>`;
}

// Questions before big work: ready options and a field for your own. Nothing is chosen until you pick it
// (the suggested one is only marked): what you leave alone, the assistant decides itself.
function QuestionsCard({ message, active, onAnswer }) {
  const questions = message.data.questions || [];
  const [picks, setPicks] = useState({});
  const [own, setOwn] = useState({});
  const value = (q) => own[q.id]?.trim() || picks[q.id] || "";
  const answered = (q) => Boolean(value(q));
  const chosenOnes = () => Object.fromEntries(questions.filter(answered).map((q) => [q.id, value(q)]));
  return html`<div class="assistant-card questions" data-slot="assistant-questions">
    <p>${message.text}</p>
    ${questions.map((q) => html`<fieldset key=${q.id} disabled=${!active}>
      <legend>${q.text}</legend>
      <div class="assistant-options">
        ${q.options.map((option, n) => {
          const chosen = picks[q.id] === option && !own[q.id]?.trim();
          return html`<button type="button" key=${option} class=${chosen ? "chosen" : ""} aria-pressed=${chosen}
            title=${n === (q.default || 0) ? "Предложено помощником" : ""}
            onClick=${() => { setPicks({ ...picks, [q.id]: chosen ? "" : option }); setOwn({ ...own, [q.id]: "" }); }}>${option}</button>`;
        })}
      </div>
      <input placeholder="Свой вариант" value=${own[q.id] || ""} aria-label=${"Свой ответ: " + q.text}
        onInput=${(e) => setOwn({ ...own, [q.id]: e.target.value })} />
    </fieldset>`)}
    ${active && html`<div class="row">
      <button class="primary" disabled=${!questions.every(answered)} onClick=${() => onAnswer(chosenOnes())}>Ответить</button>
      <button onClick=${() => onAnswer(chosenOnes())}>На ваш вкус</button>
    </div>
    <p class="hint">«Ответить» — когда отвечено на всё. «На ваш вкус»: на что вы не ответили, помощник решит сам.</p>`}
  </div>`;
}

const ITEM_MARKS = { done: "check", partial: "circle-dot-dashed", todo: "circle" };

// The plan: goal, decisions (folded) and items; the live one ticks items off as they are done.
function PlanCard({ message, task, active, running, onGo, onDrop }) {
  const live = task && task.no === message.data.task && Array.isArray(task.items); // the card may come before the task
  const items = live ? task.items : message.data.items;
  const now = live && running && task.phase === "work" ? items.find((i) => i.status === "todo")?.id : null;
  return html`<div class="assistant-card plan" data-slot="assistant-plan">
    <p>${message.text}</p>
    ${message.data.goal && html`<p><strong>${message.data.goal}</strong></p>`}
    ${message.data.decisions?.length > 0 && html`<details><summary>Решения (${message.data.decisions.length})</summary>
      <ul>${message.data.decisions.map((d, i) => html`<li key=${i}>${d}</li>`)}</ul></details>`}
    <ol class="assistant-items">
      ${items.map((item) => html`<li key=${item.id} class=${(item.parent ? "sub " : "") + (item.id === now ? "now" : item.status)}
        title=${item.detail || ""}>
        <span class="mark"><${Icon} name=${item.id === now ? "play" : ITEM_MARKS[item.status] || "circle"} /></span> ${item.id}. ${item.title}
        ${item.steps?.length > 0 && item.status !== "done" && html`<ol class="assistant-substeps" data-slot="assistant-substeps">
          ${item.steps.map((st, n) => html`<li key=${n} class=${st.done ? "done" : ""}>
            <${Icon} name=${st.done ? "check" : "circle"} /> ${st.text}</li>`)}</ol>`}
      </li>`)}
    </ol>
    ${active && html`<div class="row">
      <button class="primary" onClick=${onGo}>Начать</button>
      <button onClick=${onDrop}>Отменить задачу</button>
      <span class="muted">или напишите, что поменять</span>
    </div>`}
  </div>`;
}

function Line({ icon, text, detail, open }) {
  if (!detail) return html`<div class="assistant-line" data-slot="assistant-line"><${Icon} name=${icon} /> ${text}</div>`;
  return html`<details class="assistant-line" data-slot="assistant-line" open=${open}>
    <summary><${Icon} name=${icon} /> ${text}</summary><div class="assistant-detail">${detail}</div></details>`;
}

function issuesText(issues) {
  return issues.map((i, n) => `${n + 1}. ${i.where ? i.where + ": " : ""}${i.problem}${i.fix ? " → " + i.fix : ""}`).join("\n");
}

// Messages and journal lines in the order they happened (an action comes after the step that asked for it).
function timeline(messages, actions) {
  const items = [
    ...messages.map((m) => ({ key: "m" + m.id, at: m.created_at, order: m.role === "tool" ? 2 : 0, m })),
    ...actions.map((a) => ({ key: "a" + a.id, at: a.created_at, order: 1, a })),
  ];
  return items.sort((x, y) => (x.at < y.at ? -1 : x.at > y.at ? 1 : x.order - y.order));
}

export function Assistant({ chatId, openConv, openMsg, onClose, onChanged, onOpenSkills, onError }) {
  const [overview, setOverview] = useState(null);
  const [convId, setConvId] = useState(openConv || stored(CHAT_KEY, ""));
  const [data, setData] = useState(null);
  const [runId, setRunId] = useState(null);
  const [thinking, setThinking] = useState(null);
  const [text, setText] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [folded, setFolded] = useState(false);
  const [pos, setPos] = useState(stored(POS_KEY, null));
  const [loadError, setLoadError] = useState("");
  const [listOpen, setListOpen] = useState(false);
  const [files, setFiles] = useState([]); // attachments of the request being written
  const [uploading, setUploading] = useState(false);
  const [library, setLibrary] = useState(false);
  const fileInput = useRef(null);
  const importInput = useRef(null);
  const [, setScreen] = useState(0);
  const box = useRef(null);
  const bottom = useRef(null);

  const loadOverview = () => api.get("/assistant").then((o) => {
    setOverview(o);
    return o;
  }).catch((e) => onError(e.message));
  // attach: follow a run the conversation has going (not when the caller follows the one it started:
  // a quick run could end between the two and the window would wait for it forever).
  const loadChat = (id, attach = true) => {
    setLoadError("");
    if (!id) { setData(null); return Promise.resolve(); }
    return api.get(`/assistant/chats/${id}`).then((d) => {
      setData(d);
      if (attach && d.run_id) setRunId(d.run_id);
      return d;
    }).catch((e) => {
      // Only a conversation that is really gone is forgotten; a dropped connection keeps it and says so.
      if (e.code === "not_found") { setConvId(""); setData(null); } else setLoadError(e.message);
    });
  };
  useEffect(() => {
    loadOverview().then((o) => {
      if (o && convId && !o.chats.some((c) => c.id === convId)) setConvId("");
    });
  }, []);
  useEffect(() => { if (openConv) setConvId(openConv); }, [openConv]);
  // Work in another conversation goes on when you leave it (a new conversation, another chat): the list
  // marks it and keeps itself fresh while anything is running.
  const anyRunning = (overview?.chats || []).some((c) => c.running);
  useEffect(() => {
    if (!anyRunning) return undefined;
    const timer = setInterval(() => { loadOverview(); }, 4000);
    return () => clearInterval(timer);
  }, [anyRunning]);
  useEffect(() => { store(CHAT_KEY, convId); setRunId(null); loadChat(convId); }, [convId]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [data, thinking]);
  // Opened from the search at a message: scroll to it once its conversation is shown (it is marked for a moment).
  const foundBox = useRef(null);
  const scrolledTo = useRef(null);
  useEffect(() => {
    if (!openMsg || !data || scrolledTo.current === openMsg.stamp) return;
    if (foundBox.current) { scrolledTo.current = openMsg.stamp; foundBox.current.scrollIntoView({ block: "center" }); }
  }, [data, openMsg]);
  useEffect(() => { // a smaller screen may leave the saved position outside it
    const again = () => setScreen((n) => n + 1);
    window.addEventListener("resize", again);
    return () => window.removeEventListener("resize", again);
  }, []);

  // Follow a running request: new messages, journal lines, the task and questions appear as they come.
  useEffect(() => {
    if (!runId) return undefined;
    return followRun(runId, (event) => {
      if (event.type === "thinking") setThinking(event);
      else if (event.type === "message") {
        setData((d) => d && ({ ...d, messages: [...d.messages.filter((m) => m.id !== event.message.id), event.message] }));
      } else if (event.type === "task") setData((d) => d && ({ ...d, task: event.task }));
      else if (event.type === "action") {
        setData((d) => d && ({ ...d, actions: [...d.actions.filter((a) => a.id !== event.action.id), event.action] }));
        if (event.action.status === "done") onChanged();
      } else if (event.type === "confirm") setData((d) => d && ({ ...d, ask: event.ask }));
      else if (event.type === "confirmed") setData((d) => d && ({ ...d, ask: null }));
      else if (["done", "stopped", "error"].includes(event.type)) {
        setRunId(null);
        setThinking(null);
        // the server may still list this run for a moment: do not follow it again
        loadChat(convId, false);
        loadOverview();
      }
    });
  }, [runId]);

  const newChat = async () => {
    try {
      const made = await api.post("/assistant/chats");
      await loadOverview();
      setConvId(made.id);
      return made.id;
    } catch (e) { onError(e.message); return null; }
  };
  const deleteChat = async (chat) => {
    const id = chat?.id || convId;
    if (!id || !await ask(`Удалить беседу «${chat?.title || data?.chat?.title || "Без названия"}»? Изменения, которые сделал помощник, останутся.`,
      { ok: "Удалить", danger: true })) return;
    try {
      await api.del(`/assistant/chats/${id}`);
      if (id === convId) setConvId("");
      loadOverview();
    } catch (e) { onError(e.message); }
  };
  const renameChat = async (chat) => {
    const title = await askText("Название беседы", { value: chat.title || "" });
    if (title === null || title.trim() === (chat.title || "")) return;
    try { await api.put(`/assistant/chats/${chat.id}`, { title }); await loadOverview(); } catch (e) { onError(e.message); }
  };
  const pickChat = (id) => { setConvId(id); setListOpen(false); };
  const importChat = async (file) => {
    try {
      const made = await api.postBinary("/assistant/import", file, "application/json");
      await loadOverview();
      setConvId(made.id);
      setListOpen(false);
      notify("Беседа загружена. Изменения из неё отменить нельзя: они сделаны не здесь.", "info");
    } catch (e) { onError(e.message); }
  };
  // Files from the device: a picture goes to the library, text is read in, the rest is only described.
  const attachFiles = async (list) => {
    const room = MAX_FILES - files.length;
    if (room <= 0) { onError(`К одной просьбе можно приложить не больше ${MAX_FILES} файлов`); return; }
    setUploading(true);
    try {
      for (const file of [...list].slice(0, room)) {
        const att = await api.postBinary(`/assistant/files?name=${encodeURIComponent(file.name || "file")}`, file,
          file.type || "application/octet-stream");
        setFiles((current) => [...current, att]);
      }
    } catch (e) { onError(e.message); } finally { setUploading(false); }
  };
  const attachPicture = (name) => {
    if (files.length >= MAX_FILES) { onError(`К одной просьбе можно приложить не больше ${MAX_FILES} файлов`); return; }
    setFiles((current) => [...current, { kind: "image", name, asset: name }]);
    setLibrary(false);
  };
  const attachMenu = (e) => openMenu(e.currentTarget, [
    { icon: "upload", label: "Файл с устройства", onClick: () => fileInput.current?.click() },
    { label: "Картинка из библиотеки", onClick: () => setLibrary(true) },
  ]);
  const started = async (id, request) => {
    const sent = await request;
    await loadChat(id, false);
    setThinking({ step: 1 });
    setRunId(sent.run_id);
    loadOverview();
  };
  const working = Boolean(runId) && data?.task?.phase === "work";
  const send = async (e) => {
    e?.preventDefault();
    const request = text.trim();
    if (!request && files.length === 0) return;
    if (uploading) { notify("Файл ещё загружается", "info"); return; }
    if (runId && !working) { notify("Помощник ещё работает. Дождитесь конца или нажмите «Стоп»", "info"); return; }
    const id = convId || await newChat();
    if (!id) return;
    const sending = files;
    const body = { text: request, chat_id: chatId || null, attachments: sending.length ? sending : undefined };
    setText("");
    setFiles([]);
    try {
      if (working) {  // goes into the plan of the running work
        await api.post(`/assistant/chats/${id}/send`, body);
        return;
      }
      await started(id, api.post(`/assistant/chats/${id}/send`, body));
    } catch (err) { setText(request); setFiles(sending); onError(err.message); }
  };
  const sendAnswers = (answers) => started(convId, api.post(`/assistant/chats/${convId}/answers`, { answers, chat_id: chatId || null }))
    .catch((e) => onError(e.message));
  const go = () => started(convId, api.post(`/assistant/chats/${convId}/go`, { chat_id: chatId || null })).catch((e) => onError(e.message));
  const drop = async () => {
    if (!await ask("Отменить задачу? План сбросится, сделанное останется.", { ok: "Отменить задачу", cancel: "Не отменять", danger: true })) return;
    api.post(`/assistant/chats/${convId}/drop`).then(() => loadChat(convId)).catch((e) => onError(e.message));
  };
  const stop = () => api.post(`/assistant/chats/${convId}/stop`).catch((e) => onError(e.message));
  const answer = (allow) => api.post(`/assistant/chats/${convId}/confirm`, { ask_id: data.ask.id, allow })
    .then(() => setData((d) => ({ ...d, ask: null }))).catch((e) => onError(e.message));
  const forget = (factId) => api.del(`/assistant/memory/${factId}`).then(() => loadOverview()).catch((e) => onError(e.message));
  const undo = async (action) => {
    const later = data.actions.filter((a) => a.id > action.id && a.status === "done").length;
    if (later && !await ask(`Вместе с этим отменится и то, что помощник сделал после (изменений: ${later}).`,
      { ok: "Отменить", cancel: "Не отменять", danger: true })) return;
    try {
      await api.post(`/assistant/actions/${action.id}/undo`);
      await loadChat(convId);
      onChanged();
    } catch (e) { onError(e.message); }
  };

  // The window must always keep its header on the screen, whatever was saved before the screen got smaller.
  const clampPos = (p) => {
    const width = box.current?.offsetWidth || Math.min(420, window.innerWidth - 32);
    return { x: Math.max(0, Math.min(window.innerWidth - width, p.x)), y: Math.max(0, Math.min(window.innerHeight - 48, p.y)) };
  };

  // Dragging by the header (not on a phone: there the window takes the full width).
  const drag = (e) => {
    if (e.target.closest("button, select, input, a") || window.innerWidth < 700) return;
    const rect = box.current.getBoundingClientRect();
    const dx = e.clientX - rect.left;
    const dy = e.clientY - rect.top;
    let last = null;
    const move = (ev) => {
      last = clampPos({ x: ev.clientX - dx, y: ev.clientY - dy });
      setPos(last);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      if (last) store(POS_KEY, last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const openSkill = (skillId) => {
    try { if (skillId) localStorage.setItem(OPEN_SKILL_KEY, skillId); } catch { /* opens the tab without it */ }
    onOpenSkills();
  };
  const full = (overview?.settings.progress || "full") === "full";
  const task = data?.task || {};
  const memoryIds = new Set((overview?.memory || []).map((f) => f.id));
  const lastOf = (kind) => (data ? [...data.messages].reverse().find((m) => m.data.kind === kind) : null);
  const answeredAlready = Object.keys(task.answers || {}).length > 0; // answers are in, the plan was not made
  const liveQuestions = !runId && task.phase === "questions" && !answeredAlready ? lastOf("questions") : null;
  const livePlan = !runId && task.phase === "plan" ? lastOf("plan") : null;
  // A stop, a failure, a limit or a restart: the server says the last run did not finish.
  const leftMidway = !runId && Boolean(data?.resumable);
  const midPlan = ["work", "questions"].includes(task.phase);
  const renderMessage = (m, key) => {
    const kind = m.data.kind;
    if (m.role === "user") {
      return html`<div key=${key} class=${"assistant-msg user" + (m.data.inbox ? " inbox" : "")} data-slot="assistant-user">
        ${m.data.inbox && html`<small class="muted"><${Icon} name="plus" /> в план · </small>`}${m.text}
        ${m.data.attachments?.length > 0 && html`<div class="assistant-files">${m.data.attachments.map((a, n) => html`
          <span key=${n} class="assistant-file" title=${a.name}><${Icon} name="paperclip" /> ${a.name}${a.gone ? " (картинки нет)" : ""}</span>`)}</div>`}</div>`;
    }
    if (m.role === "tool") {
      return m.data.undone ? html`<div key=${key} class="assistant-note"><${Icon} name="undo-2" /> Вы отменили: ${m.data.undone.join("; ")}</div>` : null;
    }
    if (kind === "questions") {
      return html`<${QuestionsCard} key=${key} message=${m} active=${liveQuestions?.id === m.id} onAnswer=${sendAnswers} />`;
    }
    if (kind === "plan") {
      return html`<${PlanCard} key=${key} message=${m} task=${task} running=${Boolean(runId)}
        active=${livePlan?.id === m.id} onGo=${go} onDrop=${drop} />`;
    }
    if (kind === "skill") {
      return html`<div key=${key} class="assistant-line" data-slot="assistant-skill">
        <button class="link" onClick=${() => openSkill(m.data.skill_id)}><${Icon} name="book-open" /> ${m.text}</button></div>`;
    }
    if (kind === "progress") return html`<${Line} key=${key} icon="chevron-right" text=${m.text} detail=${full ? m.data.detail : ""} />`;
    if (kind === "step") return full && m.text ? html`<div key=${key} class="assistant-step" data-slot="assistant-step">${m.text}</div>` : null;
    if (kind === "critic") {
      return html`<${Line} key=${key} icon="scan-search" text=${m.text} detail=${m.data.issues?.length ? issuesText(m.data.issues) : ""} open=${full} />`;
    }
    if (kind === "remembered") {
      const kept = memoryIds.has(m.data.fact_id);
      return html`<div key=${key} class=${"assistant-action" + (kept ? "" : " undone")} data-slot="assistant-remembered">
        <span><${Icon} name="brain" /> ${m.text}</span>${kept && html`<button class="link" onClick=${() => forget(m.data.fact_id)}>Отменить</button>`}</div>`;
    }
    if (!m.text) return null;
    return html`<div key=${key} class=${"assistant-msg" + (m.data.error ? " error" : "") + (kind === "report" ? " report" : "")}
      data-slot="assistant-reply">${m.text}${m.data.open_issues?.length > 0 && html`
      <details><summary>Критик не принял (${m.data.open_issues.length})</summary>
        <div class="assistant-detail">${issuesText(m.data.open_issues)}</div></details>`}</div>`;
  };
  const thinkingText = () => {
    if (!thinking) return "Помощник думает…";
    if (thinking.phase === "work") return `Пункт ${thinking.item}… шаг ${thinking.step}`;
    return PHASES[thinking.phase] || `Думает… шаг ${thinking.step || 1}`;
  };

  const at = pos && clampPos(pos);
  const style = at ? `left:${at.x}px;top:${at.y}px;right:auto;bottom:auto` : "";
  const chats = overview?.chats || [];
  const currentChat = chats.find((c) => c.id === convId);
  const currentRunning = Boolean(currentChat?.running);
  const currentTitle = convId ? (currentChat?.title || data?.chat?.title || "Без названия") : "Новая беседа";
  const items = data ? timeline(data.messages, data.actions) : [];
  return html`<section class=${"assistant" + (folded ? " folded" : "")} ref=${box} style=${style}
      data-slot="assistant" role="dialog" aria-label="Помощник">
    <div class="assistant-head" data-slot="assistant-head" onPointerDown=${drag}>
      <strong>Помощник</strong>
      ${!folded && html`<button class="assistant-conv-btn" aria-label="Беседы" aria-expanded=${listOpen} data-slot="assistant-conv-btn"
          onClick=${() => { setShowSettings(false); setListOpen(!listOpen); }}>
        <span>${anyRunning && !currentRunning ? html`<span class="assistant-running">● </span>` : ""}${currentTitle}</span><${Icon} name="chevron-down" /></button>
      <${IconButton} icon="plus" label="Новая беседа" onClick=${() => { setListOpen(false); setConvId(""); }} />
      <${MenuButton} label="Ещё" items=${() => [
        { icon: "settings", label: showSettings ? "Скрыть настройки" : "Настройки", onClick: () => { setListOpen(false); setShowSettings(!showSettings); } },
        convId && { icon: "download", label: "Выгрузить беседу в файл", href: `/api/assistant/chats/${convId}/export` },
        { icon: "file-up", label: "Загрузить беседу из файла", onClick: () => importInput.current?.click() },
        { icon: "trash-2", label: "Удалить беседу", danger: true, disabled: !convId || Boolean(runId), onClick: () => deleteChat() },
      ]} />`}
      <span class="spacer"></span>
      <${IconButton} icon=${folded ? "maximize-2" : "minus"} label=${folded ? "Развернуть" : "Свернуть"} onClick=${() => setFolded(!folded)} />
      <${IconButton} icon="x" label="Закрыть" onClick=${onClose} />
    </div>
    ${!folded && html`
      ${showSettings && overview && html`<${AssistantSettings} overview=${overview} onError=${onError}
        onSaved=${(s) => setOverview({ ...overview, settings: s })} onMemory=${loadOverview} onOpenSkills=${openSkill} />`}
      ${listOpen && html`<${ConversationList} chats=${chats} current=${convId} onPick=${pickChat}
        onNew=${() => { setListOpen(false); setConvId(""); }} onRename=${renameChat} onDelete=${deleteChat}
        onImport=${() => importInput.current?.click()} />`}
      <input ref=${importInput} type="file" accept=".json,application/json" hidden
        onChange=${(e) => { const f = e.target.files[0]; e.target.value = ""; if (f) importChat(f); }} />
      <input ref=${fileInput} type="file" multiple hidden
        onChange=${(e) => { const list = e.target.files; if (list.length) attachFiles(list); e.target.value = ""; }} />
      <div class="assistant-body" data-slot="assistant-body" hidden=${listOpen}>
        ${loadError && html`<${Pending} error=${loadError} onRetry=${() => loadChat(convId)} />`}
        ${items.length === 0 && !loadError && html`<${EmptyNote} title="Чем помочь?"
          hint="Например: создать персонажа или лорбук, сменить тему, скрыть сообщения от модели, объяснить ответ. Любое изменение можно отменить." />`}
        ${items.map((item) => {
          if (item.a) return html`<${ActionLine} key=${item.key} action=${item.a} onUndo=${undo} />`;
          const shown = renderMessage(item.m, item.key);
          return openMsg && shown && item.m.id === openMsg.id && convId === openMsg.conv
            ? html`<div key=${item.key} ref=${foundBox} class="assistant-found" data-slot="assistant-found">${shown}</div>` : shown;
        })}
        ${data?.ask && html`<div class="assistant-ask" data-slot="assistant-ask" role="alert">
          <p>Помощник спрашивает: <strong>${data.ask.text}</strong></p>
          <div class="row">
            <button class="primary" onClick=${() => answer(true)}>Разрешить</button>
            <button onClick=${() => answer(false)}>Нет</button>
          </div>
        </div>`}
        ${leftMidway && html`<div class="assistant-ask" data-slot="assistant-resume" role="status">
          <p>${midPlan ? "Работа остановилась, не дойдя до конца. Сделанное на месте." : "Помощник остановился, не закончив просьбу."}</p>
          <div class="row">
            <button class="primary" onClick=${go}>Продолжить</button>
            ${midPlan && html`<button onClick=${drop}>Отменить задачу</button>`}
            <span class="muted">или напишите, что поменять</span>
          </div>
        </div>`}
        ${runId && !data?.ask && html`<p class="muted assistant-thinking">${thinkingText()}</p>`}
        <div ref=${bottom}></div>
      </div>
      ${library && html`<${LibraryPicker} taken=${files.map((f) => f.asset)} onPick=${attachPicture}
        onClose=${() => setLibrary(false)} onError=${onError} />`}
      ${(files.length > 0 || uploading) && html`<div class="assistant-attached" data-slot="assistant-attached">
        ${files.map((f, n) => html`<span key=${n} class="assistant-file" title=${f.name}>
          <${Icon} name="paperclip" /> ${f.name}
          ${f.kind === "binary" && html`<small class="muted"> · прочитать не смогу</small>`}
          <button type="button" aria-label=${"Убрать " + f.name} onClick=${() => setFiles(files.filter((_, i) => i !== n))}><${Icon} name="x" /></button></span>`)}
        ${uploading && html`<span class="muted">Загрузка…</span>`}
        <small class="muted">Текст читаю целиком, до ${TEXT_LIMIT}. Картинки видят не все модели.</small>
      </div>`}
      <form class="assistant-input" data-slot="assistant-input" onSubmit=${send}>
        <${IconButton} type="button" icon="paperclip" label="Приложить файл или картинку" disabled=${uploading} onClick=${attachMenu} />
        <textarea rows="2" value=${text} aria-label="Просьба помощнику"
          placeholder=${working ? "Дописать в план…" : liveQuestions ? "Или ответьте своими словами…" : "Что сделать?"}
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) send(e); }}></textarea>
        ${working && (text.trim() || files.length > 0) && html`<${IconButton} class="primary" type="submit" icon="send-horizontal" label="Добавить в план" />`}
        ${runId
          ? html`<button type="button" onClick=${stop}><${Icon} name="square" /> Стоп</button>`
          : html`<${IconButton} class="primary" type="submit" icon="send-horizontal" label="Отправить" disabled=${!text.trim() && files.length === 0} />`}
      </form>`}
  </section>`;
}
