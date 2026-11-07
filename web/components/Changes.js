// Settings tab «Изменения»: everything the assistant changed, across all its conversations, newest first,
// by day, and inside a day by request (one request = one "commit"). Each commit shows its time, the
// conversation it was made in, the request, what changed and a «Откатить» button. Before a rollback the
// window reads the plan from the server (GET /changes/actions/{id}/plan) and says what comes back, what
// else goes with it (later work of that conversation) and which other conversations touched the same things.
// The list is paged (the server cuts it by commits), so thousands of changes stay light.
// onOpenConversation(id) opens that conversation in the assistant window (Settings passes it on from main.js).
import { Select } from "./Select.js";
import { html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { notify } from "../core/notify.js";
import { Pending, EmptyNote } from "./State.js";

const STATE_WORDS = { created: "создано", changed: "изменено", deleted: "удалено" };
const STATUS_WORDS = { undone: "откачено", partly: "откачено частично" };

function dayStart(text, plusDays = 0) {
  if (!text) return "";
  const d = new Date(`${text}T00:00:00`);
  if (Number.isNaN(d.getTime())) return "";
  d.setDate(d.getDate() + plusDays);
  return d.toISOString();
}
const dayKey = (iso) => {
  const text = new Date(iso).toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const timeOf = (iso) => new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });

function Details({ id, onError }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { api.get(`/changes/actions/${id}`).then(setData).catch((e) => setError(e.message)); }, [id]);
  if (!data) return html`<${Pending} error=${error} />`;
  if (!data.entries.length) return html`<p class="muted">Подробностей не сохранилось (или менялось не то, что хранится в записях).</p>`;
  return html`<ul class="changes-entries">
    ${data.entries.map((e, i) => html`<li key=${i}>
      <strong>${STATE_WORDS[e.state] || e.state}</strong>: ${e.kind}${e.name ? ` «${e.name}»` : ""}${e.gone ? " (потом удалено)" : ""}
      ${e.state === "changed" && e.before && html`<ul class="changes-fields">
        ${Object.keys(e.before).map((k) => html`<li key=${k}><span class="muted">${k}:</span>
          <span class="changes-was">${e.before[k]}</span> → <span class="changes-now">${e.after && e.after[k] !== undefined ? e.after[k] : "—"}</span></li>`)}
      </ul>`}
      ${e.state === "deleted" && e.before && html`<details><summary class="muted">Что было</summary>
        <ul class="changes-fields">${Object.keys(e.before).map((k) => html`<li key=${k}><span class="muted">${k}:</span> ${e.before[k]}</li>`)}</ul></details>`}
      ${e.state === "created" && e.after && html`<details><summary class="muted">Что сделано</summary>
        <ul class="changes-fields">${Object.keys(e.after).map((k) => html`<li key=${k}><span class="muted">${k}:</span> ${e.after[k]}</li>`)}</ul></details>`}
    </li>`)}
    ${data.more > 0 && html`<li class="muted">…и ещё ${data.more}</li>`}
  </ul>`;
}

function PlanBox({ plan, busy, onGo, onCancel }) {
  if (!plan.can) {
    return html`<div class="changes-plan" role="alert"><p>${plan.reason}</p>
      <div class="row"><button type="button" onClick=${onCancel}>Понятно</button></div></div>`;
  }
  return html`<div class="changes-plan" role="alertdialog">
    <p><strong>Что вернётся:</strong></p>
    <ul>${plan.this.map((w, i) => html`<li key=${i}>${w}</li>`)}</ul>
    ${plan.later.length > 0 && html`<p class="changes-warn">Вместе с этим откатятся и более поздние изменения этой беседы
      (откат идёт по порядку, назад до этого места):</p>
      <ul>${plan.later.map((g, i) => html`<li key=${i}><span class="muted">${g.request ? `«${g.request}»` : "запрос"}:</span>
        ${g.whats.slice(0, 5).join("; ")}${g.whats.length > 5 ? ` …и ещё ${g.whats.length - 5}` : ""}</li>`)}</ul>`}
    ${plan.clashes.length > 0 && html`<p class="changes-warn">Позже другие беседы меняли те же самые вещи. Если откатить,
      их правки пропадут:</p>
      <ul>${plan.clashes.map((c) => html`<li key=${c.id}><span class="muted">${c.conversation}, ${timeOf(c.at)}:</span> ${c.what}</li>`)}</ul>
      ${!plan.scanned_all && html`<p class="muted">Проверены не все изменения: их слишком много.</p>`}`}
    <p class="muted">Если вы сами позже меняли те же вещи руками, эти правки тоже вернутся к прежнему.</p>
    <div class="row">
      <button type="button" class="danger" disabled=${busy} onClick=${onGo}>${busy ? "Откатываю…" : "Откатить"}</button>
      <button type="button" disabled=${busy} onClick=${onCancel}>Отмена</button>
    </div>
  </div>`;
}

function Commit({ commit, onOpenConversation, onRolledBack, onError }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const [shown, setShown] = useState(null); // id of the action whose details are open
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const status = commit.status;
  const askPlan = async () => {
    try { setPlan(await api.get(`/changes/actions/${commit.rollback_id}/plan`)); } catch (e) { onError(e.message); }
  };
  const go = async () => {
    setBusy(true);
    try {
      await api.post(`/changes/actions/${commit.rollback_id}/rollback`, { accepted: true });
      notify("Откат сделан");
    } catch (e) {
      onError(e.message); // the half that was done is shown by the reload below
    }
    setBusy(false);
    setPlan(null);
    onRolledBack();
  };
  const few = commit.actions.length <= 4 || all;
  const actions = few ? commit.actions : commit.actions.slice(0, 3);
  const first = commit.actions[0];
  const summary = (first ? first.what : commit.request || "Изменения") + (commit.count > 1 ? ` и ещё ${commit.count - 1}` : "");
  const convName = commit.conversation || "Беседа";
  return html`<article class=${"changes-commit" + (status === "done" ? "" : " undone") + (open ? " open" : "")} data-slot="changes-commit">
    <button type="button" class="changes-head" aria-expanded=${open} onClick=${() => setOpen(!open)}>
      <span class="changes-summary">${summary}</span>
      <span class="changes-meta muted">${timeOf(commit.at)} · ${convName}${STATUS_WORDS[status] ? " · " + STATUS_WORDS[status] : ""}</span>
    </button>
    ${open && html`<div class="changes-body">
      ${commit.request && html`<p class="changes-request"><span class="muted">Вы просили:</span> ${commit.request}</p>`}
      <ul class="changes-actions">
        ${actions.map((a) => html`<li key=${a.id} class=${a.status === "undone" ? "undone" : ""}>
          <span>${a.what}</span>
          ${a.status === "undone" && html`<span class="muted"> (откачено)</span>`}
          ${a.undoable && html` <button type="button" class="link" onClick=${() => setShown(shown === a.id ? null : a.id)}>
            ${shown === a.id ? "скрыть" : "что именно"}</button>`}
          ${shown === a.id && html`<${Details} id=${a.id} onError=${onError} />`}
        </li>`)}
      </ul>
      ${!few && html`<button type="button" onClick=${() => setAll(true)}>Показать все (${commit.count})</button>`}
      ${commit.more_actions > 0 && all && html`<p class="muted">…и ещё ${commit.more_actions}</p>`}
      ${plan && html`<${PlanBox} plan=${plan} busy=${busy} onGo=${go} onCancel=${() => setPlan(null)} />`}
      <div class="row">
        ${commit.can_rollback && !plan && html`<button type="button" onClick=${askPlan}>Откатить это</button>`}
        ${!commit.can_rollback && status === "done" && html`<span class="muted">Откатить нельзя: для этих изменений ничего не сохранилось.</span>`}
        ${commit.conversation_deleted
          ? html`<span class="muted">Беседу удалили, а изменения остались.</span>`
          : onOpenConversation && html`<button type="button" onClick=${() => onOpenConversation(commit.chat_id)}>Открыть беседу</button>`}
      </div>
    </div>`}
  </article>`;
}

// Filter chips: when (everything / today / week) and which conversation.
const localDay = (daysBack) => {
  const d = new Date();
  d.setDate(d.getDate() - daysBack);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const RANGES = [["all", "Все", 0], ["today", "Сегодня", 0], ["week", "Неделя", 6]];

export function Changes({ onError, onOpenConversation, onChanged }) {
  const [range, setRange] = useState("all");
  const [conv, setConv] = useState("");
  const [state, setState] = useState({ commits: [], next: null, conversations: [], total: 0, loading: true, error: "" });
  const ticket = useRef(0);
  const from = range === "all" ? "" : localDay(RANGES.find((r) => r[0] === range)[2]);

  const query = (before) => {
    const p = new URLSearchParams();
    if (from) p.set("since", dayStart(from));
    if (conv) p.set("conversation", conv);
    if (before) p.set("before", String(before));
    return `/changes?${p.toString()}`;
  };
  const load = (more) => {
    const mine = ++ticket.current;
    setState((s) => ({ ...s, loading: true, error: "" }));
    api.get(query(more ? state.next : 0)).then((d) => {
      if (mine !== ticket.current) return;
      setState((s) => ({
        commits: more ? [...s.commits, ...d.commits] : d.commits, next: d.next,
        conversations: d.conversations || s.conversations, total: d.total === undefined ? s.total : d.total,
        loading: false, error: "",
      }));
    }).catch((e) => { if (mine === ticket.current) setState((s) => ({ ...s, loading: false, error: e.message })); });
  };
  useEffect(() => { load(false); }, [range, conv]);

  const days = [];
  for (const c of state.commits) {
    const key = dayKey(c.at);
    if (!days.length || days[days.length - 1].key !== key) days.push({ key, commits: [] });
    days[days.length - 1].commits.push(c);
  }
  const filtered = range !== "all" || Boolean(conv);
  return html`<div class="changes">
    <h2>Изменения</h2>
    <p class="hint">Всё, что помощник менял или удалял, сначала новое. Нажмите на запись, чтобы увидеть подробности и откатить:
      прежнее вернётся. Список остаётся, даже если беседу удалить.</p>
    <div class="row changes-filters" role="group" aria-label="Фильтр">
      ${RANGES.map(([id, label]) => html`<button type="button" key=${id} class=${"filter-chip" + (range === id ? " on" : "")}
        aria-pressed=${range === id} onClick=${() => setRange(id)}>${label}</button>`)}
      <${Select} value=${conv} aria-label="Беседа" onChange=${(e) => setConv(e.target.value)}>
        <option value="">Все беседы</option>
        ${state.conversations.map((c) => html`<option key=${c.id} value=${c.id}>${c.title || "Без названия"}${c.deleted ? " (беседа удалена)" : ""} (${c.count})</option>`)}
      </${Select}>
    </div>
    ${state.error && html`<${Pending} error=${state.error} onRetry=${() => load(false)} />`}
    ${!state.error && state.loading && !state.commits.length && html`<${Pending} />`}
    ${!state.loading && !state.error && !state.commits.length && html`<${EmptyNote}
      title=${filtered ? "Ничего не найдено" : "Помощник пока ничего не менял"}
      hint=${filtered ? "Выберите «Все» или другую беседу." : "Когда он что-то создаст, поправит или удалит, это появится здесь."} />`}
    ${days.map((d) => html`<section key=${d.key} class="changes-day">
      <h3>${d.key}</h3>
      ${d.commits.map((c) => html`<${Commit} key=${c.key} commit=${c} onError=${onError}
        onOpenConversation=${onOpenConversation}
        onRolledBack=${() => { load(false); if (onChanged) onChanged(); }} />`)}
    </section>`)}
    ${state.next && html`<div class="row"><button type="button" disabled=${state.loading} onClick=${() => load(true)}>
      ${state.loading ? "Загружаю…" : "Показать более ранние"}</button></div>`}
  </div>`;
}
