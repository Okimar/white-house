// Settings tab "Подключения": the list of connections, a service picker for a new one, and the
// editor: address, keys (each with its own proxy), model with what the service told about it,
// and every setting in a console-like panel. Keys and proxies are write-only: the server only says
// whether they are set and shows a proxy's address without its login.
// The editor of the open connection sits right under its row; profiles and parallel requests are folded below.
import { Select } from "./Select.js";
import { html, useState, useEffect, useMemo, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { notify } from "../core/notify.js";
import { getChoice, setChoice } from "../core/choice.js";
import { Profiles, FallbackSteps, Parallel } from "./Profiles.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, EmptyNote, useLoad } from "./State.js";
import { Templates } from "./Templates.js";

const KIND_LABEL = { openai: "протокол OpenAI", gemini: "протокол Gemini", anthropic: "протокол Claude" };
const MARK = { yes: ["check", "принимает"], no: ["x", "не принимает"], unknown: ["circle-help", "неизвестно"] };
// Catalog groups in this order: the person's own address first, then models on their computer, then services.
const GROUP_ORDER = ["custom", "local", "cloud"];
const GROUP_HINT = {
  custom: "Любой сервер, который отвечает как OpenAI, Claude или Gemini.",
  local: "Программы, запущенные у вас: ключ обычно не нужен.",
  cloud: "Сервисы в интернете: нужен ключ от сервиса.",
};

export function Connections({ onChanged, onError }) {
  const cat = useLoad(() => api.get("/connections/catalog"), []);
  const catalog = cat.data;
  const [connections, setConnections] = useState([]);
  const [active, setActive] = useState(null);
  const [openId, setOpenIdRaw] = useState(() => getChoice("connection"));
  const setOpenId = (id) => { setOpenIdRaw(id); setChoice("connection", id); };
  const [adding, setAdding] = useState(false);
  const [revealId, setRevealId] = useState(null); // just created: scroll to its editor

  const reload = () => api.get("/connections").then((d) => {
    setConnections(d.connections);
    setActive(d.active);
    // the remembered connection may be gone by now
    setOpenIdRaw((id) => (id && !d.connections.some((c) => c.id === id) ? null : id));
    return d.connections;
  }).catch((e) => { onError(e.message); return []; });

  useEffect(() => { reload(); }, []);

  const serviceName = (id) => catalog?.services.find((s) => s.id === id)?.name || "";

  const choose = async (id) => {
    await api.put("/settings", { active_connection: id }).catch((e) => onError(e.message));
    setActive(id);
    onChanged();
  };

  const created = async (conn) => {
    setAdding(false);
    await reload();
    setOpenId(conn.id);
    setRevealId(conn.id);
    notify("Подключение создано", "ok");
    onChanged();
  };

  const removed = async () => {
    setOpenId(null);
    await reload();
    onChanged();
  };

  if (!catalog) return html`<${Pending} error=${cat.error} onRetry=${cat.reload} />`;

  return html`
    <h2>Подключения к моделям</h2>
    <p class="hint">Основное подключение отвечает во всех чатах, где не выбрано другое. Нажмите на строку, чтобы открыть настройки.</p>
    <ul class="list conn-list" data-slot="conn-list">
      ${connections.length === 0 && html`<li><${EmptyNote} title="Подключений пока нет"
        hint="Подключение говорит Таверне, к какой модели обращаться. Добавьте первое." /></li>`}
      ${connections.map((c) => html`
        <li key=${c.id} class="conn">
          <div class=${"conn-card" + (c.id === openId ? " open" : "")}>
            <label class="conn-active">
              <input type="radio" name="active" checked=${c.id === active} onChange=${() => choose(c.id)} />
              <span>${c.id === active ? "Основное" : "Выбрать"}</span>
            </label>
            <button class="conn-pick" aria-expanded=${c.id === openId}
              onClick=${() => setOpenId(c.id === openId ? null : c.id)}>
              <strong>${c.name}</strong>
              <span class="muted">${serviceName(c.service) || c.base_url}${c.model ? " · " + c.model : ""}</span>
              <span class="badge">${c.keys.length ? `ключей: ${c.keys.length}` : "без ключа"}</span>
            </button>
          </div>
          ${c.id === openId && html`<${ConnectionEditor} key=${c.id} conn=${c} catalog=${catalog}
            connections=${connections} reveal=${revealId === c.id}
            onSaved=${reload} onRemoved=${removed} onError=${onError} />`}
        </li>`)}
    </ul>
    ${!adding && html`<button onClick=${() => { setAdding(true); setOpenId(null); }}><${Icon} name="plus" /> Новое подключение</button>`}
    ${adding && html`<${NewConnection} catalog=${catalog} onCreated=${created} onCancel=${() => setAdding(false)}
      onError=${onError} />`}
    <details class="conn-more">
      <summary>Профили</summary>
      <${Profiles} onApplied=${() => { reload(); onChanged(); }} onError=${onError} />
    </details>
    <details class="conn-more">
      <summary>Несколько запросов сразу</summary>
      <${Parallel} connections=${connections} onError=${onError} />
    </details>
  `;
}

function NewConnection({ catalog, onCreated, onCancel, onError }) {
  const [query, setQuery] = useState("");
  const [service, setService] = useState(null);
  const [form, setForm] = useState({ name: "", base_url: "", key: "", proxy: "" });

  const pick = (s) => {
    setService(s);
    setForm({ name: s.name, base_url: s.base_url, key: "", proxy: "" });
  };
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const q = query.trim().toLowerCase();
  const shown = catalog.services.filter((s) => !q || s.name.toLowerCase().includes(q) || s.id.includes(q));
  const rank = (g) => (GROUP_ORDER.includes(g[0]) ? GROUP_ORDER.indexOf(g[0]) : GROUP_ORDER.length);
  const groups = [...catalog.groups].sort((a, b) => rank(a) - rank(b));

  const create = async (e) => {
    e.preventDefault();
    try {
      const keys = form.key || form.proxy ? [{ label: "Ключ 1", key: form.key, proxy: form.proxy }] : [];
      const conn = await api.post("/connections", {
        name: form.name, service: service.id, base_url: form.base_url, keys,
      });
      onCreated(conn);
    } catch (err) { onError(err.message); }
  };

  if (!service) return html`
    <div class="stack conn-new" data-slot="conn-new">
      <div class="row"><input placeholder="Найти сервис" value=${query} onInput=${(e) => setQuery(e.target.value)}
        aria-label="Найти сервис" /><button onClick=${onCancel}>Отмена</button></div>
      ${shown.length === 0 && html`<p class="muted">Ничего не нашлось.</p>`}
      ${groups.map(([gid, label]) => {
        const items = shown.filter((s) => s.group === gid);
        return items.length > 0 && html`
          <h3 key=${gid}>${label}</h3>
          ${GROUP_HINT[gid] && html`<p class="hint">${GROUP_HINT[gid]}</p>`}
          <div class="service-grid">
            ${items.map((s) => html`<button key=${s.id} class="service" onClick=${() => pick(s)}>${s.name}</button>`)}
          </div>`;
      })}
    </div>`;

  return html`
    <form class="stack conn-new" data-slot="conn-new" onSubmit=${create}>
      <h3>${service.name}</h3>
      <label class="stack">Название
        <input required value=${form.name} onInput=${set("name")} /></label>
      <label class="stack">Адрес API
        <input required value=${form.base_url} onInput=${set("base_url")} /></label>
      ${service.key !== "none" && html`
        <label class="stack">${service.key === "required" ? "Ключ API" : "Ключ API (необязательно)"}
          <input type="password" autocomplete="off" value=${form.key} onInput=${set("key")} /></label>
        ${service.site && html`<span class="hint">Где взять ключ: ${service.site}</span>`}`}
      <label class="stack">Прокси для ключа (необязательно)
        <input value=${form.proxy} onInput=${set("proxy")} /></label>
      <span class="hint">Например, socks5://логин:пароль@адрес:порт</span>
      <div class="row">
        <button type="submit">Создать</button>
        <button type="button" onClick=${() => setService(null)}>Другой сервис</button>
        <button type="button" onClick=${onCancel}>Отмена</button>
      </div>
    </form>`;
}

function ConnectionEditor({ conn, catalog, connections, reveal, onSaved, onRemoved, onError }) {
  const root = useRef(null);
  const [head, setHead] = useState({ name: conn.name, base_url: conn.base_url, model: conn.model });
  const [params, setParams] = useState(conn.params || {});
  const [dirty, setDirty] = useState(false);
  const [models, setModels] = useState([]);
  const [probeable, setProbeable] = useState([]);
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);

  const say = (text, kind = "") => setLog((l) => [...l.slice(-7), { text, kind, at: new Date().toLocaleTimeString() }]);

  useEffect(() => {
    if (reveal && root.current) root.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);

  // Changed from outside (a profile was applied): show it, unless the user is editing right now.
  useEffect(() => {
    if (dirty) return;
    setHead({ name: conn.name, base_url: conn.base_url, model: conn.model });
    setParams(conn.params || {});
  }, [conn.name, conn.base_url, conn.model, JSON.stringify(conn.params || {})]);

  useEffect(() => {
    api.get(`/connections/${conn.id}/model-info`).then((d) => {
      setModels(d.models);
      setProbeable(d.probeable);
      if (!d.models.length) inspect();
    }).catch((e) => say(e.message, "err"));
  }, [conn.id]);

  const info = models.find((m) => m.id === head.model) || null;

  // Name, address and model are saved the moment the field is left; the settings below wait for «Сохранить настройки».
  const saveHead = async (patch) => {
    try {
      await api.put(`/connections/${conn.id}`, patch);
      notify("Сохранено", "ok");
      onSaved();
    } catch (err) { onError(err.message); }
  };

  const inspect = async () => {
    setBusy(true);
    say("Спрашиваю у сервиса список моделей…");
    try {
      const d = await api.post(`/connections/${conn.id}/inspect`, {});
      setModels(d.models);
      setProbeable(d.probeable);
      say(`Готово: моделей ${d.models.length}`, "ok");
    } catch (err) { say(err.message, "err"); }
    setBusy(false);
  };

  const probe = async () => {
    if (!head.model) { say("Сначала выберите модель", "err"); return; }
    setBusy(true);
    say(`Пробные запросы к ${head.model}: ${probeable.length} настроек, по одному токену…`);
    try {
      const d = await api.post(`/connections/${conn.id}/probe`, { model: head.model });
      const bad = Object.entries(d.probe).filter(([k, v]) => v === "rejected" && k !== "structured").map(([k]) => k);
      const form = { ok: "отвечает по форме", words: "по форме — только по просьбе словами", rejected: "по форме не отвечает" }[d.probe.structured];
      say((bad.length ? `Не принимает: ${bad.join(", ")}` : "Все настройки приняты (или молча пропущены)") +
        (form ? `; ${form}` : ""), "ok");
      const fresh = await api.get(`/connections/${conn.id}/model-info`);
      setModels(fresh.models);
    } catch (err) { say(err.message, "err"); }
    setBusy(false);
  };

  const saveParams = async () => {
    try {
      await api.put(`/connections/${conn.id}`, { params });
      setDirty(false);
      say("Настройки сохранены", "ok");
      notify("Сохранено", "ok");
      onSaved();
    } catch (err) { say(err.message, "err"); }
  };

  const remove = async () => {
    if (!(await ask(`Удалить подключение «${conn.name}» вместе с его ключами?`, { ok: "Удалить", danger: true }))) return;
    await api.del(`/connections/${conn.id}`).catch((e) => onError(e.message));
    onRemoved();
  };

  const setParam = (id, value) => { setParams({ ...params, [id]: value }); setDirty(true); };
  const dropParam = (id) => { const next = { ...params }; delete next[id]; setParams(next); setDirty(true); };

  return html`
    <section ref=${root} class="conn-editor stack" data-slot="conn-editor" aria-label=${"Подключение " + conn.name}>
      <div class="row">
        <label class="stack">Название
          <input value=${head.name} onInput=${(e) => setHead({ ...head, name: e.target.value })}
            onChange=${(e) => saveHead({ name: e.target.value })} /></label>
        <span class="badge">${KIND_LABEL[conn.kind] || "протокол " + conn.kind}</span>
        <span class="spacer"></span>
        <button class="danger-text" onClick=${remove}><${Icon} name="trash-2" /> Удалить</button>
      </div>
      <label class="stack">Адрес API
        <input value=${head.base_url} onInput=${(e) => setHead({ ...head, base_url: e.target.value })}
          onChange=${(e) => saveHead({ base_url: e.target.value })} /></label>
      <span class="hint">Название, адрес и модель сохраняются сразу, как только вы выходите из поля.
        Остальное — кнопкой «Сохранить настройки».</span>

      <${Keys} conn=${conn} onSaved=${onSaved} onError=${onError} />

      <h3>Модель</h3>
      <div class="row">
        <label class="stack">Название модели
          <input value=${head.model} list=${"models-" + conn.id}
            onInput=${(e) => setHead({ ...head, model: e.target.value })}
            onChange=${(e) => saveHead({ model: e.target.value })} /></label>
        <datalist id=${"models-" + conn.id}>
          ${models.map((m) => html`<option value=${m.id}>${m.name !== m.id ? m.name : ""}</option>`)}
        </datalist>
        <button disabled=${busy} onClick=${inspect} title="Бесплатно: спросить у сервиса модели и их возможности">Проверить</button>
        <button disabled=${busy} onClick=${probe} title="По одному крошечному запросу на каждую настройку">Пробные запросы</button>
      </div>
      <p class="hint">«Проверить» бесплатно: спросит у сервиса список моделей и их возможности.
        «Пробные запросы» платные: по одному крошечному запросу на каждую настройку.</p>
      ${info && html`<${ModelFacts} info=${info} />`}

      <div class="conn-log" data-slot="conn-log" role="log" aria-live="polite">
        ${log.length === 0 && html`<div class="muted">› готово к работе</div>`}
        ${log.map((l, i) => html`<div key=${i} class=${"line " + l.kind}><span class="muted">${l.at}</span> › ${l.text}</div>`)}
      </div>

      <${Console} catalog=${catalog} kind=${conn.kind} params=${params} info=${info}
        onSet=${setParam} onDrop=${dropParam} onError=${onError} />
      ${(params.api_mode ?? "chat") === "text" && html`<${TextModeTemplate} catalog=${catalog} kind=${conn.kind}
        params=${params} info=${info} onSet=${setParam} onDrop=${dropParam} onError=${onError} />`}
      <${FallbackSteps} conn=${conn} connections=${connections} steps=${params.fallbacks || []}
        phrases=${params.refusal_phrases || []}
        onSteps=${(steps) => (steps.length ? setParam("fallbacks", steps) : dropParam("fallbacks"))}
        onPhrases=${(list) => (list.length ? setParam("refusal_phrases", list) : dropParam("refusal_phrases"))} />
      <div class="row">
        <button class="primary" disabled=${!dirty} onClick=${saveParams}>Сохранить настройки</button>
        ${dirty && html`<span class="muted">Есть несохранённые изменения</span>`}
      </div>
    </section>`;
}

function ModelFacts({ info }) {
  const facts = [];
  if (info.context) facts.push(`контекст ${info.context.toLocaleString()}`);
  if (info.max_output) facts.push(`ответ до ${info.max_output.toLocaleString()}`);
  if (info.price_in != null) facts.push(`$${info.price_in} / $${info.price_out} за млн токенов (вход / выход)`);
  if (info.params == null) facts.push("какие настройки принимает — сервис не сообщает, помогут пробные запросы");
  return html`<p class="muted model-facts" data-slot="model-facts">${[...facts, ...(info.notes || [])].join(" · ")}</p>`;
}

function Keys({ conn, onSaved, onError }) {
  const [editing, setEditing] = useState(null); // key id or "new"
  const [form, setForm] = useState({ label: "", key: "", proxy: "" });

  const start = (k) => {
    setEditing(k ? k.id : "new");
    setForm({ label: k ? k.label : "", key: "", proxy: "" });
  };
  const set = (name) => (e) => setForm({ ...form, [name]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    try {
      if (editing === "new") await api.post(`/connections/${conn.id}/keys`, form);
      else {
        const patch = { label: form.label, key: form.key };
        if (form.proxy || form.clearProxy) patch.proxy = form.clearProxy ? "" : form.proxy;
        await api.put(`/connections/${conn.id}/keys/${editing}`, patch);
      }
      setEditing(null);
      notify("Сохранено", "ok");
      onSaved();
    } catch (err) { onError(err.message); }
  };

  const del = async (k) => {
    if (!(await ask(`Удалить ключ «${k.label || "без названия"}»?`, { ok: "Удалить", danger: true, skip: "key" }))) return;
    await api.del(`/connections/${conn.id}/keys/${k.id}`).catch((e) => onError(e.message));
    onSaved();
  };

  const move = async (index, by) => {
    const ids = conn.keys.map((k) => k.id);
    const [id] = ids.splice(index, 1);
    ids.splice(index + by, 0, id);
    await api.put(`/connections/${conn.id}/keys/order`, { ids }).catch((e) => onError(e.message));
    onSaved();
  };

  const isNew = editing === "new";
  return html`
    <h3>Ключи</h3>
    <p class="hint">Если ключ не сработал, пробуется следующий по порядку.</p>
    <ul class="list keys" data-slot="conn-keys">
      ${conn.keys.length === 0 && html`<li class="muted">Ключей нет. Для моделей на своём компьютере обычно и не нужно.</li>`}
      ${conn.keys.map((k, i) => html`
        <li key=${k.id} class="key-row">
          <span>${i + 1}. <strong>${k.label || "без названия"}</strong></span>
          <span class="muted">${k.has_key ? "ключ задан" : "без ключа"}${k.has_proxy ? " · через " + k.proxy : ""}</span>
          <span class="spacer"></span>
          <${IconButton} icon="arrow-up" label="Выше" disabled=${i === 0} onClick=${() => move(i, -1)} />
          <${IconButton} icon="arrow-down" label="Ниже" disabled=${i === conn.keys.length - 1} onClick=${() => move(i, 1)} />
          <${IconButton} icon="pencil" label="Изменить ключ" onClick=${() => start(k)} />
          <${IconButton} icon="trash-2" label="Удалить ключ" class="danger-text" onClick=${() => del(k)} />
        </li>`)}
    </ul>
    ${editing === null && html`<button onClick=${() => start(null)}><${Icon} name="plus" /> Ключ</button>`}
    ${editing !== null && html`
      <form class="stack key-form" onSubmit=${submit}>
        <label class="stack">Название ключа
          <input value=${form.label} onInput=${set("label")} /></label>
        <label class="stack">${isNew ? "Ключ API" : "Новый ключ"}
          <input type="password" autocomplete="off" value=${form.key} onInput=${set("key")} /></label>
        ${!isNew && html`<span class="hint">Пусто — оставить прежний.</span>`}
        <label class="stack">${isNew ? "Прокси (необязательно)" : "Новый прокси"}
          <input value=${form.proxy} onInput=${set("proxy")} /></label>
        <span class="hint">${isNew ? "Например, http://… или socks5://…" : "Пусто — оставить прежний."}</span>
        ${!isNew && html`<label><input type="checkbox" checked=${Boolean(form.clearProxy)}
          onChange=${(e) => setForm({ ...form, clearProxy: e.target.checked })} /> Убрать прокси</label>`}
        <div class="row"><button type="submit">Сохранить</button>
          <button type="button" onClick=${() => setEditing(null)}>Отмена</button></div>
      </form>`}`;
}

function verdict(spec, info) {
  if (spec.send === false) return "yes";
  if (!info) return "unknown";
  const tried = (info.probe || {})[spec.id];
  if (tried === "rejected") return "no";
  if (tried === "ok") return "yes";
  if (info.params == null) return "unknown";
  return info.params.includes(spec.id) ? "yes" : "no";
}

function Console({ catalog, kind, params, info, onSet, onDrop, onError }) {
  // The template setting lives in its own folded section (TextModeTemplate), only for text mode.
  const specs = useMemo(() => catalog.settings.params.filter((p) => p.kinds.includes(kind) && p.type !== "template"), [catalog, kind]);
  const templates = [];
  return html`
    <div class="conn-console" data-slot="conn-console">
      <p class="hint">Галочка — отправлять настройку. Без галочки сервис берёт своё значение.
        Значок справа: принимает, не принимает или неизвестно.</p>
      ${catalog.settings.groups.map(([gid, label]) => {
        const items = specs.filter((p) => p.group === gid);
        return items.length > 0 && html`
          <fieldset key=${gid}><legend>${label}</legend>
            ${items.map((p) => html`<${ParamRow} key=${p.id} spec=${p} value=${params[p.id]} on=${p.id in params}
              mark=${verdict(p, info)} limit=${info?.limits?.[p.id]} templates=${templates}
              onSet=${onSet} onDrop=${onDrop} />`)}
          </fieldset>`;
      })}
    </div>`;
}

// Instruct template: matters only when the connection sends one text (text mode). Folded, auto by default.
function TextModeTemplate({ catalog, kind, params, info, onSet, onDrop, onError }) {
  const [templates, setTemplates] = useState([]);
  const spec = catalog.settings.params.find((p) => p.type === "template" && p.kinds.includes(kind));
  useEffect(() => { api.get("/templates").then((d) => setTemplates(d.templates)).catch((e) => onError(e.message)); }, []);
  if (!spec) return null;
  return html`
    <details class="conn-more" data-slot="conn-template">
      <summary>Шаблон для текстового режима</summary>
      <p class="hint">В текстовом режиме весь разговор уходит в модель одним текстом с метками, на которых она училась;
        по умолчанию шаблон меток выбирается сам, по названию модели.</p>
      <${ParamRow} spec=${spec} value=${params[spec.id]} on=${spec.id in params} mark=${verdict(spec, info)}
        templates=${templates} onSet=${onSet} onDrop=${onDrop} />
      <details class="conn-more">
        <summary>Свои шаблоны: создать, изменить, импорт</summary>
        <${Templates} onError=${onError} />
      </details>
    </details>`;
}

function ParamRow({ spec, value, on, mark, limit, templates, onSet, onDrop }) {
  const [text, setText] = useState(null); // raw text of list/json fields while typing
  const [badJson, setBadJson] = useState(false);
  const current = on ? value : spec.default;
  const [lo, hi] = limit || [spec.min, spec.max];
  const toggle = (e) => (e.target.checked ? onSet(spec.id, spec.default) : onDrop(spec.id));
  const num = (e) => {
    const v = spec.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
    if (!Number.isNaN(v)) onSet(spec.id, v);
  };
  const json = (raw) => {
    setText(raw);
    try { onSet(spec.id, JSON.parse(raw || "{}")); setBadJson(false); } catch { setBadJson(true); }
  };
  const [sign, word] = MARK[mark];

  let control;
  if (spec.type === "float" || spec.type === "int") {
    // A slider over a million tokens is useless: it covers the usual range, the number field the rest.
    const sliderMax = spec.type === "int" && hi > 32768 ? 32768 : hi;
    control = html`
      <input type="range" min=${lo} max=${sliderMax} step=${spec.step} value=${current} disabled=${!on}
        onInput=${num} aria-label=${spec.label} />
      <input type="number" class="num" min=${lo} max=${hi} step=${spec.step} value=${current} disabled=${!on}
        onChange=${num} aria-label=${spec.label + ", число"} />`;
  } else if (spec.type === "bool") {
    control = html`<${Select} value=${String(current)} disabled=${!on} aria-label=${spec.label}
      onChange=${(e) => onSet(spec.id, e.target.value === "true")}>
      <option value="true">да</option><option value="false">нет</option></${Select}>`;
  } else if (spec.type === "template") {
    control = html`<${Select} value=${current} disabled=${!on} aria-label=${spec.label}
      onChange=${(e) => onSet(spec.id, e.target.value)}>
      <option value="auto">Автоматически, по названию модели</option>
      ${templates.map((t) => html`<option key=${t.id} value=${t.id}>${t.name}${t.builtin ? "" : " (свой)"}</option>`)}
    </${Select}>`;
  } else if (spec.type === "choice") {
    control = html`<${Select} value=${current} disabled=${!on} aria-label=${spec.label}
      onChange=${(e) => onSet(spec.id, e.target.value)}>
      ${spec.choices.map((c) => html`<option value=${c}>${c}</option>`)}</${Select}>`;
  } else if (spec.type === "list") {
    control = html`<textarea rows="2" disabled=${!on} aria-label=${spec.label} placeholder="по одной на строку"
      value=${text ?? (current || []).join("\n")}
      onInput=${(e) => { setText(e.target.value); onSet(spec.id, e.target.value.split("\n").filter((s) => s)); }} />`;
  } else {
    control = html`<textarea rows="2" class="mono" disabled=${!on} aria-label=${spec.label}
      value=${text ?? JSON.stringify(current ?? {})} onInput=${(e) => json(e.target.value)} />
      ${badJson && html`<span class="hint">Это не похоже на JSON: пока сохранится прежнее значение.</span>`}`;
  }

  return html`
    <div class=${"param " + mark + (on ? " on" : "")} title=${spec.hint}>
      <label class="param-name"><input type="checkbox" checked=${on} onChange=${toggle} />
        <span>${spec.label}</span> <span class="muted param-id">${spec.id}</span></label>
      <span class="param-control">${control}</span>
      <span class=${"param-mark " + mark} title=${word} role="img" aria-label=${word}><${Icon} name=${sign} /></span>
    </div>`;
}
