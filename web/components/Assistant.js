// The assistant over the chat (stage 8б, engine of 10а): a window that floats over the app, dragged by
// its header (full width on a phone), folded to a strip. Conversations are a list like chats;
// what the assistant changed is shown as action lines with "Отменить" (that one and every later one);
// deleting waits for "Разрешить". Big work comes as cards: questions with ready answers, a plan that
// waits for «Начать» and ticks its items off as they are done, progress lines, the critic's remarks and
// the report. Writing while it works adds to the plan. The «Настройки» item of the header menu holds
// its model, the model of each role, the critic, how much of the work to show, its limits and
// «Что я о вас знаю». The saved position is clamped to the screen whenever it is applied.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api, followRun } from "../core/api.js";
import { money } from "../core/spending.js";
import { notify } from "../core/notify.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { MenuButton } from "./Menu.js";
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
    <label>Модель помощника<br /><select value=${s.source} onChange=${(e) => save({ source: e.target.value })}>
      ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.label}</option>`)}
    </select></label>
    <details>
      <summary>Модели для большой работы</summary>
      <p class="hint">Пусто — модель помощника.</p>
      ${ROLE_LABELS.map(([name, label, hint]) => html`<div class="assistant-role" key=${name}>
        <span><strong>${label}</strong><br /><small class="muted">${hint}</small></span>
        <select aria-label=${label + ": подключение"} value=${s.roles[name].source}
          onChange=${(e) => role(name, { source: e.target.value })}>
          ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.id ? o.label : "Как у помощника"}</option>`)}
        </select>
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
    <label>Ход работы<br /><select value=${s.progress} onChange=${(e) => save({ progress: e.target.value })}>
      <option value="full">Показывать всё</option>
      <option value="minimal">Только короткие строки</option>
    </select></label>
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
      <input placeholder="Свой вариант" value=${own[q.id] || ""} aria-label=${"Свой ответ: " + q.text}
        onInput=${(e) => setOwn({ ...own, [q.id]: e.target.value })} />
    </fieldset>`)}
    ${active && html`<div class="row">
      <button class="primary" onClick=${() => onAnswer(Object.fromEntries(questions.map((q) => [q.id, value(q)])))}>Ответить</button>
      <button onClick=${() => onAnswer(Object.fromEntries(questions.filter(changed).map((q) => [q.id, value(q)])))}>На ваш вкус</button>
    </div>
    <p class="hint">«На ваш вкус»: то, что вы не меняли, помощник выберет сам.</p>`}
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

export function Assistant({ chatId, openConv, onClose, onChanged, onOpenSkills, onError }) {
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
  useEffect(() => { store(CHAT_KEY, convId); setRunId(null); loadChat(convId); }, [convId]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [data, thinking]);
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
    if (!convId || !await ask("Удалить эту беседу? Изменения, которые сделал помощник, останутся.", { ok: "Удалить", danger: true })) return;
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
    if (!request) return;
    if (runId && !working) { notify("Помощник ещё работает. Дождитесь конца или нажмите «Стоп»", "info"); return; }
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
    if (e.target.closest("button, select, input") || window.innerWidth < 700) return;
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
  const liveQuestions = !runId && task.phase === "questions" ? lastOf("questions") : null;
  const livePlan = !runId && task.phase === "plan" ? lastOf("plan") : null;
  const leftMidway = !runId && task.phase === "work"; // a stop, a failure or a restart: the plan is not finished
  const renderMessage = (m, key) => {
    const kind = m.data.kind;
    if (m.role === "user") {
      return html`<div key=${key} class=${"assistant-msg user" + (m.data.inbox ? " inbox" : "")} data-slot="assistant-user">
        ${m.data.inbox && html`<small class="muted"><${Icon} name="plus" /> в план · </small>`}${m.text}</div>`;
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
  const items = data ? timeline(data.messages, data.actions) : [];
  return html`<section class=${"assistant" + (folded ? " folded" : "")} ref=${box} style=${style}
      data-slot="assistant" role="dialog" aria-label="Помощник">
    <div class="assistant-head" data-slot="assistant-head" onPointerDown=${drag}>
      <strong>Помощник</strong>
      ${!folded && html`<select aria-label="Беседа" value=${convId} onChange=${(e) => setConvId(e.target.value)}>
        <option value="">Новая беседа</option>
        ${chats.map((c) => html`<option key=${c.id} value=${c.id}>${c.title || "Без названия"}</option>`)}
      </select>
      <${IconButton} icon="plus" label="Новая беседа" onClick=${newChat} />
      <${MenuButton} label="Ещё" items=${() => [
        { icon: "settings", label: showSettings ? "Скрыть настройки" : "Настройки", onClick: () => setShowSettings(!showSettings) },
        { icon: "trash-2", label: "Удалить беседу", danger: true, disabled: !convId || Boolean(runId), onClick: deleteChat },
      ]} />`}
      <span class="spacer"></span>
      <${IconButton} icon=${folded ? "maximize-2" : "minus"} label=${folded ? "Развернуть" : "Свернуть"} onClick=${() => setFolded(!folded)} />
      <${IconButton} icon="x" label="Закрыть" onClick=${onClose} />
    </div>
    ${!folded && html`
      ${showSettings && overview && html`<${AssistantSettings} overview=${overview} onError=${onError}
        onSaved=${(s) => setOverview({ ...overview, settings: s })} onMemory=${loadOverview} onOpenSkills=${openSkill} />`}
      <div class="assistant-body" data-slot="assistant-body">
        ${loadError && html`<${Pending} error=${loadError} onRetry=${() => loadChat(convId)} />`}
        ${items.length === 0 && !loadError && html`<${EmptyNote} title="Чем помочь?"
          hint="Например: создать персонажа или лорбук, сменить тему, скрыть сообщения от модели, объяснить ответ. Любое изменение можно отменить." />`}
        ${items.map((item) => (item.a ? html`<${ActionLine} key=${item.key} action=${item.a} onUndo=${undo} />` : renderMessage(item.m, item.key)))}
        ${data?.ask && html`<div class="assistant-ask" data-slot="assistant-ask" role="alert">
          <p>Помощник спрашивает: <strong>${data.ask.text}</strong></p>
          <div class="row">
            <button class="primary" onClick=${() => answer(true)}>Разрешить</button>
            <button onClick=${() => answer(false)}>Нет</button>
          </div>
        </div>`}
        ${leftMidway && html`<div class="assistant-ask" data-slot="assistant-resume" role="status">
          <p>Работа остановилась на середине плана. Сделанное на месте.</p>
          <div class="row">
            <button class="primary" onClick=${go}>Продолжить</button>
            <button onClick=${drop}>Отменить задачу</button>
            <span class="muted">или напишите, что поменять</span>
          </div>
        </div>`}
        ${runId && !data?.ask && html`<p class="muted assistant-thinking">${thinkingText()}</p>`}
        <div ref=${bottom}></div>
      </div>
      <form class="assistant-input" data-slot="assistant-input" onSubmit=${send}>
        <textarea rows="2" value=${text} aria-label="Просьба помощнику"
          placeholder=${working ? "Дописать в план…" : liveQuestions ? "Или ответьте своими словами…" : "Что сделать?"}
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) send(e); }}></textarea>
        ${working && text.trim() && html`<${IconButton} class="primary" type="submit" icon="send-horizontal" label="Добавить в план" />`}
        ${runId
          ? html`<button type="button" onClick=${stop}><${Icon} name="square" /> Стоп</button>`
          : html`<${IconButton} class="primary" type="submit" icon="send-horizontal" label="Отправить" disabled=${!text.trim()} />`}
      </form>`}
  </section>`;
}
