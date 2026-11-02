// The assistant over the chat (stage 8б, engine of 10а): a window that floats over the app, dragged by
// its header (full width on a phone), folded to a strip with "▁". Conversations are a list like chats;
// what the assistant changed is shown as action lines with "Отменить" (that one and every later one);
// deleting waits for "Разрешить". Big work comes as cards: questions with ready answers, a plan that
// waits for «Давай» and ticks its items off as they are done, progress lines, the critic's remarks and
// the report. Writing while it works adds to the plan. ⚙ holds its model, the model of each role,
// the critic, how much of the work to show, its limits and «Что я о вас знаю».
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api, followRun } from "../core/api.js";
import { money } from "../core/spending.js";

const POS_KEY = "tavern.assistant.pos";
const CHAT_KEY = "tavern.assistant.chat";
const ROLE_LABELS = [
  ["planner", "Думает и планирует", "вопросы и план"],
  ["writer", "Пишет", "делает пункты плана"],
  ["critic", "Критик", "проверяет результат"],
  ["small", "Мелкая работа", "вносит ваши слова в план, пишет итог"],
];
const PHASES = { questions: "Готовит вопросы…", plan: "Составляет план…", critic: "Критик читает…", report: "Пишет итог…" };

function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not remembered */ }
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
      await (value.trim() ? api.put(`/assistant/memory/${fact.id}`, { text: value }) : api.del(`/assistant/memory/${fact.id}`));
      onChange();
    } catch (err) { onError(err.message); }
  };
  const forget = (fact) => api.del(`/assistant/memory/${fact.id}`).then(onChange).catch((e) => onError(e.message));
  return html`<div class="assistant-memory" data-slot="assistant-memory">
    <strong>Что я о вас знаю</strong>
    <p class="muted">Это видят все роли помощника. Он сам добавляет сюда ваши вкусы («запомнил: …»); правьте и удаляйте.</p>
    ${memory.map((fact) => html`<div class="row" key=${fact.id}>
      <input value=${fact.text} aria-label="Запись памяти" onChange=${(e) => edit(fact, e.target.value)} />
      <button title="Забыть" onClick=${() => forget(fact)}>✕</button>
    </div>`)}
    <form class="row" onSubmit=${add}>
      <input value=${text} placeholder="Например: люблю мрачное фэнтези" onInput=${(e) => setText(e.target.value)} />
      <button type="submit" disabled=${!text.trim()}>＋</button>
    </form>
  </div>`;
}

function AssistantSettings({ overview, onSaved, onMemory, onError }) {
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
    <label>Модель (простые просьбы)<br /><select value=${s.source} onChange=${(e) => save({ source: e.target.value })}>
      ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.label}</option>`)}
    </select></label>
    <details>
      <summary>Роли в большой работе</summary>
      <p class="muted">Пусто — как у помощника. Название модели можно взять из подсказок.</p>
      ${ROLE_LABELS.map(([name, label, hint]) => html`<div class="assistant-role" key=${name}>
        <span><strong>${label}</strong><br /><small class="muted">${hint}</small></span>
        <select aria-label=${label + ": подключение"} value=${s.roles[name].source}
          onChange=${(e) => role(name, { source: e.target.value })}>
          ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.id ? o.label : "Как у помощника"}</option>`)}
        </select>
        <input list="assistant-models" aria-label=${label + ": модель"} placeholder="модель как у подключения"
          value=${s.roles[name].model} onChange=${(e) => role(name, { model: e.target.value.trim() })} />
      </div>`)}
      <datalist id="assistant-models">${models.map((m) => html`<option key=${m} value=${m} />`)}</datalist>
    </details>
    <div class="row">
      <label><input type="checkbox" checked=${s.critic} onChange=${(e) => save({ critic: e.target.checked })} />
        Критик проверяет творческую работу</label>
      <label>Кругов критика<br /><input type="number" min="1" max="5" value=${s.critic_rounds}
        disabled=${!s.critic} onChange=${number("critic_rounds")} /></label>
    </div>
    <label>Ход работы<br /><select value=${s.progress} onChange=${(e) => save({ progress: e.target.value })}>
      <option value="full">Показывать всё</option>
      <option value="minimal">Минимум: только короткие строки</option>
    </select></label>
    <div class="row">
      <label>Шагов на просьбу и на пункт плана<br /><input type="number" min="1" max="50" value=${s.max_steps}
        onChange=${number("max_steps")} /></label>
      <label title="Сколько последних сообщений чата помощник читает сразу; остальное найдёт поиском или по номерам">
        Читать сообщений (0 — весь чат)<br /><input type="number" min="0" value=${s.read_messages}
        onChange=${number("read_messages")} /></label>
    </div>
    <div class="row">
      <label>Лимит в день, ${cur}<br /><input type="number" min="0" step="any" value=${s.day} onChange=${number("day")} /></label>
      <label>Лимит в месяц, ${cur}<br /><input type="number" min="0" step="any" value=${s.month} onChange=${number("month")} /></label>
    </div>
    <p class="muted">${`Потрачено сегодня: ${money(overview.spent.day.cost, overview.money)} (${overview.spent.day.requests} запр.), ` +
      `за месяц: ${money(overview.spent.month.cost, overview.money)}. 0 — без лимита; общие лимиты тоже действуют.`}</p>
    <${Memory} memory=${overview.memory || []} onChange=${onMemory} onError=${onError} />
  </div>`;
}

const MARKS = { done: "✓", undone: "↩", denied: "✋", failed: "✗" };
const AFTER = { undone: " — отменено", denied: " — не разрешено" };

function ActionLine({ action, onUndo }) {
  return html`<div class=${"assistant-action " + action.status} data-slot="assistant-action">
    <span>${MARKS[action.status] || "•"} ${action.what}${AFTER[action.status] || ""}</span>
    ${action.status === "done" && html`<button class="link" onClick=${() => onUndo(action)}>Отменить</button>`}
  </div>`;
}

// Questions before big work: each has ready options (its default chosen) and a field for your own.
function QuestionsCard({ message, active, onAnswer }) {
  const questions = message.data.questions || [];
  const [picks, setPicks] = useState(() => Object.fromEntries(questions.map((q) => [q.id, q.options[q.default || 0]])));
  const [own, setOwn] = useState({});
  const value = (q) => (own[q.id]?.trim() ? own[q.id].trim() : picks[q.id]);
  const changed = (q) => own[q.id]?.trim() || picks[q.id] !== q.options[q.default || 0];
  return html`<div class="assistant-card questions" data-slot="assistant-questions">
    <p>${message.text}</p>
    ${questions.map((q) => html`<fieldset key=${q.id} disabled=${!active}>
      <legend>${q.text}</legend>
      <div class="assistant-options">
        ${q.options.map((option) => {
          const chosen = picks[q.id] === option && !own[q.id]?.trim();
          return html`<button type="button" key=${option} class=${chosen ? "chosen" : ""} aria-pressed=${chosen}
            onClick=${() => { setPicks({ ...picks, [q.id]: option }); setOwn({ ...own, [q.id]: "" }); }}>${option}</button>`;
        })}
      </div>
      <input placeholder="Своё…" value=${own[q.id] || ""} aria-label=${"Свой ответ: " + q.text}
        onInput=${(e) => setOwn({ ...own, [q.id]: e.target.value })} />
    </fieldset>`)}
    ${active && html`<div class="row">
      <button class="primary" onClick=${() => onAnswer(Object.fromEntries(questions.map((q) => [q.id, value(q)])))}>Ответить</button>
      <button title="Всё, что вы не меняли, он выберет сам"
        onClick=${() => onAnswer(Object.fromEntries(questions.filter(changed).map((q) => [q.id, value(q)])))}>На твой вкус</button>
    </div>`}
  </div>`;
}

const ITEM_MARKS = { done: "✓", partial: "◐", todo: "○" };

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
        <span class="mark">${item.id === now ? "▶" : ITEM_MARKS[item.status] || "○"}</span> ${item.id}. ${item.title}
      </li>`)}
    </ol>
    ${active && html`<div class="row">
      <button class="primary" onClick=${onGo}>Давай</button>
      <button onClick=${onDrop}>Отменить задачу</button>
      <span class="muted">или напишите, что поменять</span>
    </div>`}
  </div>`;
}

function Line({ icon, text, detail, open }) {
  if (!detail) return html`<div class="assistant-line" data-slot="assistant-line">${icon} ${text}</div>`;
  return html`<details class="assistant-line" data-slot="assistant-line" open=${open}>
    <summary>${icon} ${text}</summary><div class="assistant-detail">${detail}</div></details>`;
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

export function Assistant({ chatId, openConv, onClose, onChanged, onError }) {
  const [overview, setOverview] = useState(null);
  const [convId, setConvId] = useState(openConv || stored(CHAT_KEY, ""));
  const [data, setData] = useState(null);
  const [runId, setRunId] = useState(null);
  const [thinking, setThinking] = useState(null);
  const [text, setText] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [folded, setFolded] = useState(false);
  const [pos, setPos] = useState(stored(POS_KEY, null));
  const box = useRef(null);
  const bottom = useRef(null);

  const loadOverview = () => api.get("/assistant").then((o) => {
    setOverview(o);
    return o;
  }).catch((e) => onError(e.message));
  // attach: follow a run the conversation has going (not when the caller follows the one it started:
  // a quick run could end between the two and the window would wait for it forever).
  const loadChat = (id, attach = true) => {
    if (!id) { setData(null); return Promise.resolve(); }
    return api.get(`/assistant/chats/${id}`).then((d) => {
      setData(d);
      if (attach && d.run_id) setRunId(d.run_id);
    }).catch(() => { setConvId(""); setData(null); });
  };
  useEffect(() => {
    loadOverview().then((o) => {
      if (o && convId && !o.chats.some((c) => c.id === convId)) setConvId("");
    });
  }, []);
  useEffect(() => { if (openConv) setConvId(openConv); }, [openConv]);
  useEffect(() => { store(CHAT_KEY, convId); setRunId(null); loadChat(convId); }, [convId]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [data, thinking]);

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
        loadChat(convId, false); // the server may still list this run for a moment: do not follow it again
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
  const deleteChat = async () => {
    if (!convId || !confirm("Удалить эту беседу с помощником? Сделанные им изменения останутся.")) return;
    try {
      await api.del(`/assistant/chats/${convId}`);
      setConvId("");
      loadOverview();
    } catch (e) { onError(e.message); }
  };
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
    if (!request || (runId && !working)) return;
    const id = convId || await newChat();
    if (!id) return;
    setText("");
    try {
      if (working) {  // goes into the plan of the running work
        await api.post(`/assistant/chats/${id}/send`, { text: request, chat_id: chatId || null });
        return;
      }
      await started(id, api.post(`/assistant/chats/${id}/send`, { text: request, chat_id: chatId || null }));
    } catch (err) { setText(request); onError(err.message); }
  };
  const sendAnswers = (answers) => started(convId, api.post(`/assistant/chats/${convId}/answers`, { answers, chat_id: chatId || null }))
    .catch((e) => onError(e.message));
  const go = () => started(convId, api.post(`/assistant/chats/${convId}/go`, { chat_id: chatId || null })).catch((e) => onError(e.message));
  const drop = () => api.post(`/assistant/chats/${convId}/drop`).then(() => loadChat(convId)).catch((e) => onError(e.message));
  const stop = () => api.post(`/assistant/chats/${convId}/stop`).catch((e) => onError(e.message));
  const answer = (allow) => api.post(`/assistant/chats/${convId}/confirm`, { ask_id: data.ask.id, allow })
    .then(() => setData((d) => ({ ...d, ask: null }))).catch((e) => onError(e.message));
  const forget = (factId) => api.del(`/assistant/memory/${factId}`).then(() => loadOverview()).catch((e) => onError(e.message));
  const undo = async (action) => {
    const later = data.actions.filter((a) => a.id > action.id && a.status === "done").length;
    if (later && !confirm(`Отменится и то, что помощник сделал после этого (${later}). Продолжить?`)) return;
    try {
      await api.post(`/assistant/actions/${action.id}/undo`);
      await loadChat(convId);
      onChanged();
    } catch (e) { onError(e.message); }
  };

  // Dragging by the header (not on a phone: there the window takes the full width).
  const drag = (e) => {
    if (e.target.closest("button, select, input") || window.innerWidth < 700) return;
    const rect = box.current.getBoundingClientRect();
    const dx = e.clientX - rect.left;
    const dy = e.clientY - rect.top;
    let last = null;
    const move = (ev) => {
      last = { x: Math.max(0, Math.min(window.innerWidth - 120, ev.clientX - dx)),
        y: Math.max(0, Math.min(window.innerHeight - 40, ev.clientY - dy)) };
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

  const full = (overview?.settings.progress || "full") === "full";
  const task = data?.task || {};
  const memoryIds = new Set((overview?.memory || []).map((f) => f.id));
  const lastOf = (kind) => (data ? [...data.messages].reverse().find((m) => m.data.kind === kind) : null);
  const liveQuestions = !runId && task.phase === "questions" ? lastOf("questions") : null;
  const livePlan = !runId && task.phase === "plan" ? lastOf("plan") : null;
  const renderMessage = (m, key) => {
    const kind = m.data.kind;
    if (m.role === "user") {
      return html`<div key=${key} class=${"assistant-msg user" + (m.data.inbox ? " inbox" : "")} data-slot="assistant-user">
        ${m.data.inbox && html`<small class="muted">➕ в план · </small>`}${m.text}</div>`;
    }
    if (m.role === "tool") {
      return m.data.undone ? html`<div key=${key} class="assistant-note">↩ Вы отменили: ${m.data.undone.join("; ")}</div>` : null;
    }
    if (kind === "questions") {
      return html`<${QuestionsCard} key=${key} message=${m} active=${liveQuestions?.id === m.id} onAnswer=${sendAnswers} />`;
    }
    if (kind === "plan") {
      return html`<${PlanCard} key=${key} message=${m} task=${task} running=${Boolean(runId)}
        active=${livePlan?.id === m.id} onGo=${go} onDrop=${drop} />`;
    }
    if (kind === "progress") return html`<${Line} key=${key} icon="▸" text=${m.text} detail=${full ? m.data.detail : ""} />`;
    if (kind === "step") return full && m.text ? html`<div key=${key} class="assistant-step" data-slot="assistant-step">· ${m.text}</div>` : null;
    if (kind === "critic") {
      return html`<${Line} key=${key} icon="🔍" text=${m.text} detail=${m.data.issues?.length ? issuesText(m.data.issues) : ""} open=${full} />`;
    }
    if (kind === "remembered") {
      const kept = memoryIds.has(m.data.fact_id);
      return html`<div key=${key} class=${"assistant-action" + (kept ? "" : " undone")} data-slot="assistant-remembered">
        <span>🧠 ${m.text}</span>${kept && html`<button class="link" onClick=${() => forget(m.data.fact_id)}>Отменить</button>`}</div>`;
    }
    if (!m.text) return null;
    return html`<div key=${key} class=${"assistant-msg" + (m.data.error ? " error" : "") + (kind === "report" ? " report" : "")}
      data-slot="assistant-reply">${m.text}${m.data.open_issues?.length > 0 && html`
      <details><summary>Критик не принял (${m.data.open_issues.length})</summary>
        <div class="assistant-detail">${issuesText(m.data.open_issues)}</div></details>`}</div>`;
  };
  const thinkingText = () => {
    if (!thinking) return "Думает…";
    if (thinking.phase === "work") return `Пункт ${thinking.item}… шаг ${thinking.step}`;
    return PHASES[thinking.phase] || `Думает… шаг ${thinking.step || 1}`;
  };

  const style = pos ? `left:${pos.x}px;top:${pos.y}px;right:auto;bottom:auto` : "";
  const chats = overview?.chats || [];
  const items = data ? timeline(data.messages, data.actions) : [];
  return html`<section class=${"assistant" + (folded ? " folded" : "")} ref=${box} style=${style}
      data-slot="assistant" role="dialog" aria-label="Помощник">
    <div class="assistant-head" data-slot="assistant-head" onPointerDown=${drag}>
      <strong>🪄 Помощник</strong>
      ${!folded && html`<select aria-label="Беседа" value=${convId} onChange=${(e) => setConvId(e.target.value)}>
        <option value="">— новая беседа —</option>
        ${chats.map((c) => html`<option key=${c.id} value=${c.id}>${c.title || "Без названия"}</option>`)}
      </select>
      <button title="Новая беседа" onClick=${newChat}>＋</button>
      <button title="Удалить беседу" disabled=${!convId || Boolean(runId)} onClick=${deleteChat}>🗑</button>
      <button title="Настройки помощника" aria-pressed=${showSettings} onClick=${() => setShowSettings(!showSettings)}>⚙</button>`}
      <span class="spacer"></span>
      <button title=${folded ? "Развернуть" : "Свернуть"} onClick=${() => setFolded(!folded)}>${folded ? "▢" : "▁"}</button>
      <button title="Закрыть" onClick=${onClose}>✕</button>
    </div>
    ${!folded && html`
      ${showSettings && overview && html`<${AssistantSettings} overview=${overview} onError=${onError}
        onSaved=${(s) => setOverview({ ...overview, settings: s })} onMemory=${loadOverview} />`}
      <div class="assistant-body" data-slot="assistant-body">
        ${items.length === 0 && html`<p class="muted">Помощник умеет всё, что и вы на этой странице. Например:
          «сделай персонажа — лесную ведьму», «напиши лорбук про город и включи его в этом чате»,
          «сделай тему потемнее с фиолетовым», «спрячь от модели сообщения 3–5», «почему Аня так ответила?».
          Перед большой работой он задаст пару вопросов и покажет план. Каждое изменение можно отменить.</p>`}
        ${items.map((item) => (item.a ? html`<${ActionLine} key=${item.key} action=${item.a} onUndo=${undo} />` : renderMessage(item.m, item.key)))}
        ${data?.ask && html`<div class="assistant-ask" data-slot="assistant-ask" role="alert">
          <p>Помощник спрашивает: <strong>${data.ask.text}</strong></p>
          <div class="row">
            <button class="primary" onClick=${() => answer(true)}>Разрешить</button>
            <button onClick=${() => answer(false)}>Нет</button>
          </div>
        </div>`}
        ${runId && !data?.ask && html`<p class="muted assistant-thinking">${thinkingText()}</p>`}
        <div ref=${bottom}></div>
      </div>
      <form class="assistant-input" data-slot="assistant-input" onSubmit=${send}>
        <textarea rows="2" value=${text} aria-label="Просьба помощнику"
          placeholder=${working ? "Дописать в план…" : liveQuestions ? "Или ответьте своими словами…" : "Что сделать?"}
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey) send(e); }}></textarea>
        ${working && text.trim() && html`<button class="primary" type="submit" title="Добавить в план">➤</button>`}
        ${runId
          ? html`<button type="button" onClick=${stop}>■ Стоп</button>`
          : html`<button class="primary" type="submit" disabled=${!text.trim()}>➤</button>`}
      </form>`}
  </section>`;
}
