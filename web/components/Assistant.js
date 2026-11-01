// The assistant over the chat (stage 8б): a window that floats over the app, dragged by its header
// (full width on a phone), folded to a strip with "▁". Conversations are a list like chats; what
// the assistant changed is shown as action lines with "Отменить" (that one and every later one);
// deleting waits for "Разрешить". ⚙ holds its model, step limit and own spending limits.
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api, followRun } from "../core/api.js";
import { money } from "../core/spending.js";

const POS_KEY = "tavern.assistant.pos";
const CHAT_KEY = "tavern.assistant.chat";

function stored(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function store(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode: not remembered */ }
}

function AssistantSettings({ overview, onSaved, onError }) {
  const s = overview.settings;
  const cur = overview.money.currency.code || "$";
  const save = async (changes) => {
    try { onSaved(await api.put("/assistant/settings", { ...s, ...changes })); } catch (e) { onError(e.message); }
  };
  const number = (key) => (e) => { const v = Number(e.target.value); if (v !== s[key] && v >= 0) save({ [key]: v }); };
  return html`<div class="assistant-settings" data-slot="assistant-settings">
    <label>Модель<br /><select value=${s.source} onChange=${(e) => save({ source: e.target.value })}>
      ${overview.sources.map((o) => html`<option key=${o.id} value=${o.id}>${o.label}</option>`)}
    </select></label>
    <div class="row">
      <label>Шагов на одну просьбу<br /><input type="number" min="1" max="50" value=${s.max_steps}
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

// Messages and journal lines in the order they happened (an action comes after the step that asked for it).
function timeline(messages, actions) {
  const items = [
    ...messages.map((m) => ({ key: "m" + m.id, at: m.created_at, order: m.role === "tool" ? 2 : 0, m })),
    ...actions.map((a) => ({ key: "a" + a.id, at: a.created_at, order: 1, a })),
  ];
  return items.sort((x, y) => (x.at < y.at ? -1 : x.at > y.at ? 1 : x.order - y.order));
}

export function Assistant({ chatId, onClose, onChanged, onError }) {
  const [overview, setOverview] = useState(null);
  const [convId, setConvId] = useState(stored(CHAT_KEY, ""));
  const [data, setData] = useState(null);
  const [runId, setRunId] = useState(null);
  const [step, setStep] = useState(0);
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
  const loadChat = (id) => {
    if (!id) { setData(null); return Promise.resolve(); }
    return api.get(`/assistant/chats/${id}`).then((d) => {
      setData(d);
      if (d.run_id) setRunId(d.run_id);
    }).catch(() => { setConvId(""); setData(null); });
  };
  useEffect(() => {
    loadOverview().then((o) => {
      if (o && convId && !o.chats.some((c) => c.id === convId)) setConvId("");
    });
  }, []);
  useEffect(() => { store(CHAT_KEY, convId); setRunId(null); loadChat(convId); }, [convId]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [data, step]);

  // Follow a running request: new messages, journal lines and questions appear as they come.
  useEffect(() => {
    if (!runId) return undefined;
    return followRun(runId, (event) => {
      if (event.type === "thinking") setStep(event.step);
      else if (event.type === "message") {
        setData((d) => d && ({ ...d, messages: [...d.messages.filter((m) => m.id !== event.message.id), event.message] }));
      } else if (event.type === "action") {
        setData((d) => d && ({ ...d, actions: [...d.actions.filter((a) => a.id !== event.action.id), event.action] }));
        if (event.action.status === "done") onChanged();
      } else if (event.type === "confirm") setData((d) => d && ({ ...d, ask: event.ask }));
      else if (event.type === "confirmed") setData((d) => d && ({ ...d, ask: null }));
      else if (["done", "stopped", "error"].includes(event.type)) {
        setRunId(null);
        setStep(0);
        loadChat(convId);
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
  const send = async (e) => {
    e?.preventDefault();
    const request = text.trim();
    if (!request || runId) return;
    const id = convId || await newChat();
    if (!id) return;
    try {
      const sent = await api.post(`/assistant/chats/${id}/send`, { text: request, chat_id: chatId || null });
      setText("");
      await loadChat(id);
      setRunId(sent.run_id);
      setStep(1);
      loadOverview();
    } catch (err) { onError(err.message); }
  };
  const stop = () => api.post(`/assistant/chats/${convId}/stop`).catch((e) => onError(e.message));
  const answer = (allow) => api.post(`/assistant/chats/${convId}/confirm`, { ask_id: data.ask.id, allow })
    .then(() => setData((d) => ({ ...d, ask: null }))).catch((e) => onError(e.message));
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
        onSaved=${(s) => setOverview({ ...overview, settings: s })} />`}
      <div class="assistant-body" data-slot="assistant-body">
        ${items.length === 0 && html`<p class="muted">Помощник умеет всё, что и вы на этой странице. Например:
          «сделай персонажа — лесную ведьму», «напиши лорбук про город и включи его в этом чате»,
          «сделай тему потемнее с фиолетовым», «спрячь от модели сообщения 3–5», «почему Аня так ответила?».
          Каждое изменение можно отменить.</p>`}
        ${items.map((item) => {
          if (item.a) return html`<${ActionLine} key=${item.key} action=${item.a} onUndo=${undo} />`;
          const m = item.m;
          if (m.role === "user") return html`<div key=${item.key} class="assistant-msg user" data-slot="assistant-user">${m.text}</div>`;
          if (m.role === "tool") {
            return m.data.undone ? html`<div key=${item.key} class="assistant-note">↩ Вы отменили: ${m.data.undone.join("; ")}</div>` : null;
          }
          if (!m.text) return null;
          return html`<div key=${item.key} class=${"assistant-msg" + (m.data.error ? " error" : "")}
            data-slot="assistant-reply">${m.text}</div>`;
        })}
        ${data?.ask && html`<div class="assistant-ask" data-slot="assistant-ask" role="alert">
          <p>Помощник просит разрешения: <strong>${data.ask.text}</strong></p>
          <div class="row">
            <button class="primary" onClick=${() => answer(true)}>Разрешить</button>
            <button onClick=${() => answer(false)}>Нет</button>
          </div>
        </div>`}
        ${runId && !data?.ask && html`<p class="muted assistant-thinking">Думает… шаг ${step || 1}</p>`}
        <div ref=${bottom}></div>
      </div>
      <form class="assistant-input" data-slot="assistant-input" onSubmit=${send}>
        <textarea rows="2" value=${text} placeholder="Что сделать?" aria-label="Просьба помощнику"
          onInput=${(e) => setText(e.target.value)}
          onKeyDown=${(e) => { if (e.key === "Enter" && !e.shiftKey) send(e); }}></textarea>
        ${runId
          ? html`<button type="button" onClick=${stop}>■ Стоп</button>`
          : html`<button class="primary" type="submit" disabled=${!text.trim()}>➤</button>`}
      </form>`}
  </section>`;
}
