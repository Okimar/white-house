// Search across all chats (stage 11): one window, a switch at the top between ordinary chats and the
// assistant's conversations. «Найти» finds the words as you type (Russian endings and letter case do not
// matter; "в кавычках" is an exact phrase); «Спросить нейронку» describes what is wanted in plain words and
// the model picks from the candidates (a few requests, told before). A tap on a result opens that chat at
// that message (main.js does the jump) or the assistant's conversation at its message.
import { Select } from "./Select.js";
import { html, useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { backdrop, useEscape } from "../core/keys.js";
import { Icon, IconButton } from "./Icon.js";
import { useGroups } from "./Groups.js";
import { EmptyNote } from "./State.js";
import { roleOf } from "./roles.js";

const DEBOUNCE_MS = 300;
const SCOPE_KEY = "tavern.searchScope";
const HELPER_ROLES = { user: "Вы", assistant: "Помощник" };

const read = (key, fallback) => { try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; } };
const write = (key, value) => { try { localStorage.setItem(key, value); } catch (e) { /* storage off */ } };

// The piece of text with the found words marked (positions come from the server in the page's own units).
function Snippet({ snippet }) {
  if (!snippet || !snippet.text) return null;
  const text = String(snippet.text);
  const marks = Array.isArray(snippet.marks) ? snippet.marks : [];
  const parts = [];
  let at = 0;
  marks.forEach(([rawFrom, rawTo], n) => {
    const from = Math.max(at, Math.min(text.length, Math.max(0, rawFrom)));
    const to = Math.max(from, Math.min(text.length, Math.max(0, rawTo)));
    if (from > at) parts.push(text.slice(at, from));
    if (to > from) parts.push(html`<mark key=${n}>${text.slice(from, to)}</mark>`);
    at = to;
  });
  if (at < text.length) parts.push(text.slice(at));
  return html`<span class="search-snippet" data-slot="search-snippet">${snippet.before ? "… " : ""}${parts}${snippet.after ? " …" : ""}</span>`;
}

function dateText(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
}

function Hit({ hit, onPick }) {
  const chats = hit.scope === "chats";
  const who = chats ? (hit.name || roleOf(hit.role)?.label || hit.role || "Сообщение") : (HELPER_ROLES[hit.role] || hit.role || "Помощник");
  const onKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const hits = [...document.querySelectorAll(".search-results-list .search-hit")];
      const idx = hits.indexOf(e.currentTarget);
      if (e.key === "ArrowDown" && idx < hits.length - 1) hits[idx + 1].focus();
      else if (e.key === "ArrowUp") {
        if (idx > 0) hits[idx - 1].focus();
        else document.querySelector(".search-form input")?.focus();
      }
    }
  };
  const title = (hit.title || "").trim() || (chats ? "Чат без названия" : "Разговор с помощником");
  return html`<button class="search-hit" data-slot="search-hit" onClick=${() => onPick(hit)}
    onKeyDown=${onKeyDown}
    title=${chats && !hit.on_path ? "Сообщение в другой ветке чата: откроется она" : "Открыть на этом сообщении"}>
    <span class="search-hit-head">
      <${Icon} name=${chats ? "message-square" : "wand"} />
      <strong class="search-hit-title">${title}</strong>
      <span class="search-hit-date muted">${dateText(hit.date)}</span>
    </span>
    <span class="search-hit-meta muted">
      ${chats && hit.character ? html`<span class="search-hit-who">${hit.character} · </span>` : ""}${who}
      ${chats && !hit.on_path ? html` · <${Icon} name="git-branch" /> другая ветка` : ""}
      ${chats ? html` · сообщение ${hit.depth + 1}` : ""}
    </span>
    <${Snippet} snippet=${hit.snippet} />
    ${hit.reason && html`<span class="search-reason" data-slot="search-reason"><${Icon} name="bot" /> ${hit.reason}</span>`}
  </button>`;
}

// chatId: the open chat. Then the window starts on «Этот чат» (owner, 2026-11-22: one magnifier for both); the
// server scope is still "chats", narrowed by `chat`.
export function Search({ characters, chatId = null, chatTitle = "", onOpen, onClose, onError }) {
  const [place, setPlace] = useState(() => (chatId ? "this" : (read(SCOPE_KEY, "chats") === "assistant" ? "assistant" : "chats")));
  const scope = place === "assistant" ? "assistant" : "chats";
  const inChat = place === "this" && Boolean(chatId);
  const [mode, setMode] = useState("plain"); // plain | ask
  const [query, setQuery] = useState("");
  const [question, setQuestion] = useState("");
  const [filters, setFilters] = useState({ character: "", since: "", until: "", group: "" });
  const [plain, setPlain] = useState({ hits: [], more: false, loading: false, done: false, error: "" });
  const [asked, setAsked] = useState(null); // { running, result, error }
  const [info, setInfo] = useState(null);
  const { groups } = useGroups("chat", onError);
  const token = useRef("");
  const controller = useRef(null);
  const winRef = useRef(null);
  const opener = useRef(document.activeElement);

  const close = () => {
    onClose();
    try { opener.current?.focus?.(); } catch (e) { /* opener gone */ }
  };
  useEscape(close);

  const onWinKeyDown = (e) => {
    if (e.key === "Tab" && winRef.current) {
      const focusable = [...winRef.current.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]')];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  useEffect(() => { api.get("/search/info").then(setInfo).catch(() => setInfo(null)); }, []);
  useEffect(() => () => { // closing the window ends a search that is still running
    if (controller.current) controller.current.abort();
    if (token.current) api.post("/search/stop", { token: token.current }).catch(() => {});
  }, []);

  const params = (extra = {}) => {
    const p = new URLSearchParams({ scope, ...extra });
    for (const [k, v] of Object.entries(filters)) if (v && (scope === "chats" || k === "since" || k === "until")) p.set(k, v);
    if (inChat) { p.set("chat", chatId); p.delete("character"); p.delete("group"); }
    return p;
  };

  // Plain search while typing: the previous request is cancelled as soon as the words change.
  const run = (append) => {
    if (controller.current) controller.current.abort();
    const text = query.trim();
    if (text.length < 2) {
      setPlain({ hits: [], more: false, loading: false, done: false, error: "" });
      return undefined;
    }
    const mine = new AbortController();
    controller.current = mine;
    setPlain((s) => ({ ...(append ? s : { hits: [], more: false, done: false }), loading: true, error: "" }));
    const offset = append ? plain.hits.length : 0;
    api.get("/search?" + params({ q: text, offset }).toString(), mine.signal).then((r) => {
      if (mine.signal.aborted) return;
      setPlain((s) => ({ hits: append ? [...s.hits, ...r.hits] : r.hits, more: r.has_more, loading: false, done: true, error: "" }));
    }).catch((e) => {
      if (e && e.name === "AbortError") return;
      setPlain((s) => ({ ...s, loading: false, error: e.message }));
    });
    return mine;
  };
  useEffect(() => {
    const timer = setTimeout(() => { if (mode === "plain") run(false); }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, place, filters, mode]);

  const chooseScope = (next) => { setPlace(next); if (next !== "this") write(SCOPE_KEY, next); setAsked(null); };
  const setFilter = (key, value) => setFilters((f) => ({ ...f, [key]: value }));

  const askModel = async () => {
    token.current = Math.random().toString(36).slice(2);
    setAsked({ running: true, result: null, error: "" });
    const body = { scope, question: question.trim(), token: token.current };
    for (const [k, v] of Object.entries(filters)) if (v && (scope === "chats" || k === "since" || k === "until")) body[k] = v;
    if (inChat) { body.chat = chatId; delete body.character; delete body.group; }
    try {
      const result = await api.post("/search/ask", body);
      setAsked({ running: false, result, error: "" });
    } catch (e) {
      setAsked({ running: false, result: null, error: e.message });
    }
    token.current = "";
  };
  const stopAsking = () => { if (token.current) api.post("/search/stop", { token: token.current }).catch(() => {}); };

  const filterBar = html`<div class="search-filters" data-slot="search-filters">
    ${scope === "chats" && !inChat && html`<label>Персонаж
      <${Select} value=${filters.character} onChange=${(e) => setFilter("character", e.target.value)}>
        <option value="">Любой</option>
        ${characters.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
      </${Select}></label>`}
    ${scope === "chats" && !inChat && html`<label>Группа чатов
      <${Select} value=${filters.group} onChange=${(e) => setFilter("group", e.target.value)}>
        <option value="">Любая</option>
        <option value="none">Без группы</option>
        ${groups.map((g) => html`<option key=${g.id} value=${g.id}>${g.name}</option>`)}
      </${Select}></label>`}
    <label>С <input type="date" value=${filters.since} max=${filters.until || undefined}
      onChange=${(e) => setFilter("since", e.target.value)} /></label>
    <label>По <input type="date" value=${filters.until} min=${filters.since || undefined}
      onChange=${(e) => setFilter("until", e.target.value)} /></label>
    ${(filters.character || filters.group || filters.since || filters.until) && html`<button type="button"
      onClick=${() => setFilters({ character: "", since: "", until: "", group: "" })}>Сбросить</button>`}
  </div>`;

  const result = asked && asked.result;
  const maxRequests = info ? info.max_requests : 4;
  return html`
    <div class="overlay" ...${backdrop(onClose)}>
      <div class="dialog wide search-window" data-slot="search" role="dialog" aria-modal="true" aria-label="Поиск по чатам">
        <div class="side-head"><strong>Поиск</strong><span class="spacer"></span>
          <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
        <div class="tabs" role="tablist" aria-label="Где искать">
          ${chatId && html`<button role="tab" aria-selected=${place === "this"} data-slot="search-this-chat"
            title=${chatTitle ? "Искать в чате «" + chatTitle + "»" : "Искать в открытом чате"}
            onClick=${() => chooseScope("this")}>Этот чат</button>`}
          <button role="tab" aria-selected=${place === "chats"} onClick=${() => chooseScope("chats")}>Все чаты</button>
          <button role="tab" aria-selected=${place === "assistant"} onClick=${() => chooseScope("assistant")}>Помощник</button>
        </div>
        <div class="tabs search-modes" role="tablist" aria-label="Как искать">
          <button role="tab" aria-selected=${mode === "plain"} onClick=${() => setMode("plain")}>
            <${Icon} name="search" /> По словам</button>
          <button role="tab" aria-selected=${mode === "ask"} onClick=${() => setMode("ask")}>
            <${Icon} name="bot" /> Спросить нейронку</button>
        </div>
        ${filterBar}

        ${mode === "plain" && html`
          <form class="search-form" role="search" onSubmit=${(e) => { e.preventDefault(); run(false); }}>
            <${Icon} name=${plain.loading ? "loader-circle" : "search"} class=${plain.loading ? "state-spinner" : ""} />
            <input type="search" value=${query} autofocus enterkeyhint="search" aria-label="Что искать"
              placeholder=${inChat ? "Слова из этого чата, «точная фраза» в кавычках" : scope === "chats" ? "Слова из сообщений, «точная фраза» в кавычках" : "Слова из разговоров с помощником"}
              onInput=${(e) => setQuery(e.target.value)}
              onKeyDown=${(e) => {
                if (e.key === "ArrowDown") {
                  const firstHit = document.querySelector(".search-results-list .search-hit");
                  if (firstHit) { e.preventDefault(); firstHit.focus(); }
                }
              }} />
          </form>
          <div class="search-results-list" data-slot="search-results-list" aria-live="polite">
            ${plain.error && html`<p class="state-error" role="alert">${plain.error}</p>`}
            ${query.trim().length < 2 && html`<${EmptyNote} icon="search" title="Введите слова для поиска"
              hint="Окончания и регистр не имеют значения: «эльфа» найдёт и «эльфы». Для поиска по смыслу переключитесь на «Спросить нейронку»."/>`}
            ${plain.done && !plain.hits.length && !plain.loading && !plain.error && html`<${EmptyNote}
              icon="search-x" title="Ничего не найдено"
              hint=${(filters.character || filters.group || filters.since || filters.until)
                ? "По вашему запросу с учётом установленных фильтров совпадений нет."
                : "Попробуйте другие слова или проверьте, нет ли опечатки."}>
              ${(filters.character || filters.group || filters.since || filters.until) && html`<div class="empty-actions">
                <button type="button" class="empty-btn" onClick=${() => setFilters({ character: "", since: "", until: "", group: "" })}>Сбросить фильтры</button>
              </div>`}
            <//>`}
            ${plain.hits.map((h) => html`<${Hit} key=${h.scope + h.id} hit=${h} onPick=${onOpen} />`)}
            ${plain.loading && html`<div class="muted state-note state-pending" role="status"><span class="state-spinner" aria-hidden="true"><${Icon} name="loader-circle" size="16" /></span> <span>Ищу…</span></div>`}
            ${plain.more && !plain.loading && html`<button class="search-more" onClick=${() => run(true)}>Показать ещё</button>`}
          </div>`}

        ${mode === "ask" && html`
          <div class="search-ask" data-slot="search-ask">
            <label class="stack">Опишите словами, что ищете
              <textarea rows="3" value=${question} maxlength="600" aria-label="Описание"
                placeholder=${scope === "chats" ? "Например: чат, где эльф предал короля" : "Например: где я просил сделать тему с цветами"}
                onInput=${(e) => setQuestion(e.target.value)}></textarea></label>
            <p class="muted search-cost" data-slot="search-cost">
              ${info && info.model
                ? html`Отвечает ваша основная модель: ${info.model.model || info.model.connection}. `
                : html`Подключение к модели не выбрано: добавьте его в настройках. `}
              Это будет до ${maxRequests} обращений к ней и расход токенов (он виден во вкладке «Расход»).
              Сначала поиск подберёт сообщения со словами из описания, модель оценит только их.
              Остановить можно в любой момент.</p>
            <div class="row">
              ${!asked?.running && html`<button class="primary" disabled=${question.trim().length < 5}
                onClick=${askModel}>Спросить нейронку</button>`}
              ${asked?.running && html`<button onClick=${stopAsking}><${Icon} name="square" /> Остановить</button>`}
              ${asked?.running && html`<span class="muted" role="status"><${Icon} name="loader-circle" /> Нейронка читает…</span>`}
            </div>
            <div class="search-results-list" data-slot="search-results-list" aria-live="polite">
              ${asked?.error && html`<p class="state-error" role="alert">${asked.error}</p>`}
              ${result && result.stopped && html`<p class="muted">Остановлено. Запросов к модели: ${result.requests}.
                ${result.hits.length ? "Уже проверенное показано ниже." : "Ничего проверенного пока нет."}</p>`}
              ${result && !result.stopped && !result.hits.length && html`<${EmptyNote} icon="bot" title="Нейронка ничего не нашла"
                hint=${result.candidates
                  ? `Она просмотрела ${result.candidates} подходящих сообщений, но ни одно не подошло под описание.`
                  : "Сообщений с похожими ключевыми словами не нашлось. Попробуйте назвать конкретные имена или предметы."}>
                ${(filters.character || filters.group || filters.since || filters.until) && html`<div class="empty-actions">
                  <button type="button" class="empty-btn" onClick=${() => setFilters({ character: "", since: "", until: "", group: "" })}>Сбросить фильтры</button>
                </div>`}
              <//>`}
              ${result && result.hits.length > 0 && html`<p class="muted">Нашлось: ${result.hits.length}.
                Запросов к модели: ${result.requests}. Показаны только настоящие сообщения: придуманное моделью отброшено.</p>`}
              ${result && result.hits.map((h) => html`<${Hit} key=${h.scope + h.id} hit=${h} onPick=${onOpen} />`)}
            </div>
          </div>`}
      </div>
    </div>`;
}
