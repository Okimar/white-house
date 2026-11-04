// Settings tab "Подключения": the list of connections, a service picker for a new one, and the
// editor: address, keys (each with its own proxy), model with what the service told about it,
// and every setting in a console-like panel. Keys and proxies are write-only: the server only says
// whether they are set and shows a proxy's address without its login.
import { html, useState, useEffect, useMemo } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { Profiles, FallbackSteps, Parallel } from "./Profiles.js";

const KIND_LABEL = { openai: "протокол OpenAI", gemini: "протокол Gemini", anthropic: "протокол Claude" };
const MARK = { yes: ["✓", "принимает"], no: ["✗", "не принимает"], unknown: ["?", "неизвестно"] };

export function Connections({ onChanged, onError }) {
  const [catalog, setCatalog] = useState(null);
  const [connections, setConnections] = useState([]);
  const [active, setActive] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [adding, setAdding] = useState(false);

  const reload = () => api.get("/connections").then((d) => {
    setConnections(d.connections);
    setActive(d.active);
    return d.connections;
  }).catch((e) => { onError(e.message); return []; });

  useEffect(() => {
    api.get("/connections/catalog").then(setCatalog).catch((e) => onError(e.message));
    reload();
  }, []);

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
    onChanged();
  };

  const removed = async () => {
    setOpenId(null);
    await reload();
    onChanged();
  };

  if (!catalog) return html`<p class="muted">Загрузка…</p>`;
  const open = connections.find((c) => c.id === openId);

  return html`
    <h2>Подключения к моделям</h2>
    <ul class="list conn-list" data-slot="conn-list">
      ${connections.length === 0 && html`<li class="muted">Подключений нет. Добавьте первое.</li>`}
      ${connections.map((c) => html`
        <li key=${c.id} class=${"conn-card" + (c.id === openId ? " open" : "")}>
          <input type="radio" name="active" aria-label=${"Использовать " + c.name}
            checked=${c.id === active} onChange=${() => choose(c.id)} />
          <button class="conn-pick" onClick=${() => setOpenId(c.id === openId ? null : c.id)}>
            <strong>${c.name}</strong>
            <span class="muted">${serviceName(c.service) || c.base_url}${c.model ? " · " + c.model : ""}</span>
            <span class="badge">${c.keys.length ? `ключей: ${c.keys.length}` : "без ключа"}</span>
          </button>
        </li>`)}
    </ul>
    ${!adding && html`<button class="primary" onClick=${() => { setAdding(true); setOpenId(null); }}>＋ Новое подключение</button>`}
    ${adding && html`<${NewConnection} catalog=${catalog} onCreated=${created} onCancel=${() => setAdding(false)}
      onError=${onError} />`}
    <${Profiles} onApplied=${() => { reload(); onChanged(); }} onError=${onError} />
    <${Parallel} connections=${connections} onError=${onError} />
    ${open && html`<${ConnectionEditor} key=${open.id} conn=${open} catalog=${catalog} connections=${connections}
      onSaved=${reload} onRemoved=${removed} onError=${onError} />`}
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
      <div class="row"><input placeholder="Найти сервис…" value=${query} onInput=${(e) => setQuery(e.target.value)}
        aria-label="Найти сервис" /><button onClick=${onCancel}>Отмена</button></div>
      ${catalog.groups.map(([gid, label]) => {
        const items = shown.filter((s) => s.group === gid);
        return items.length > 0 && html`
          <h3 key=${gid}>${label}</h3>
          <div class="service-grid">
            ${items.map((s) => html`<button key=${s.id} class="service" onClick=${() => pick(s)}>${s.name}</button>`)}
          </div>`;
      })}
    </div>`;

  return html`
    <form class="stack conn-new" data-slot="conn-new" onSubmit=${create}>
      <h3>${service.name}</h3>
      <input required placeholder="Название" value=${form.name} onInput=${set("name")} aria-label="Название" />
      <input required placeholder="Адрес API" value=${form.base_url} onInput=${set("base_url")} aria-label="Адрес API" />
      ${service.key !== "none" && html`
        <input type="password" autocomplete="off" value=${form.key} onInput=${set("key")} aria-label="Ключ"
          placeholder=${service.key === "required" ? "Ключ API" : "Ключ API (необязательно)"} />
        ${service.site && html`<span class="muted">Где взять ключ: ${service.site}</span>`}`}
      <input placeholder="Прокси для этого ключа, например socks5://логин:пароль@адрес:порт (необязательно)"
        value=${form.proxy} onInput=${set("proxy")} aria-label="Прокси" />
      <div class="row">
        <button class="primary" type="submit">Создать</button>
        <button type="button" onClick=${() => setService(null)}>Другой сервис</button>
        <button type="button" onClick=${onCancel}>Отмена</button>
      </div>
    </form>`;
}

function ConnectionEditor({ conn, catalog, connections, onSaved, onRemoved, onError }) {
  const [head, setHead] = useState({ name: conn.name, base_url: conn.base_url, model: conn.model });
  const [params, setParams] = useState(conn.params || {});
  const [dirty, setDirty] = useState(false);
  const [models, setModels] = useState([]);
  const [probeable, setProbeable] = useState([]);
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);

  const say = (text, kind = "") => setLog((l) => [...l.slice(-7), { text, kind, at: new Date().toLocaleTimeString() }]);

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

  const saveHead = async (patch) => {
    try {
      await api.put(`/connections/${conn.id}`, patch);
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
      onSaved();
    } catch (err) { say(err.message, "err"); }
  };

  const remove = async () => {
    if (!confirm(`Удалить подключение «${conn.name}» вместе с его ключами?`)) return;
    await api.del(`/connections/${conn.id}`).catch((e) => onError(e.message));
    onRemoved();
  };

  const setParam = (id, value) => { setParams({ ...params, [id]: value }); setDirty(true); };
  const dropParam = (id) => { const next = { ...params }; delete next[id]; setParams(next); setDirty(true); };

  return html`
    <section class="conn-editor stack" data-slot="conn-editor" aria-label=${"Подключение " + conn.name}>
      <div class="row">
        <input value=${head.name} aria-label="Название" onInput=${(e) => setHead({ ...head, name: e.target.value })}
          onChange=${(e) => saveHead({ name: e.target.value })} />
        <span class="badge">${KIND_LABEL[conn.kind] || "протокол " + conn.kind}</span>
        <span class="spacer"></span>
        <button onClick=${remove}>Удалить</button>
      </div>
      <input value=${head.base_url} aria-label="Адрес API" onInput=${(e) => setHead({ ...head, base_url: e.target.value })}
        onChange=${(e) => saveHead({ base_url: e.target.value })} />

      <${Keys} conn=${conn} onSaved=${onSaved} onError=${onError} />

      <h3>Модель</h3>
      <div class="row">
        <input value=${head.model} placeholder="модель" list=${"models-" + conn.id} aria-label="Модель"
          onInput=${(e) => setHead({ ...head, model: e.target.value })}
          onChange=${(e) => saveHead({ model: e.target.value })} />
        <datalist id=${"models-" + conn.id}>
          ${models.map((m) => html`<option value=${m.id}>${m.name !== m.id ? m.name : ""}</option>`)}
        </datalist>
        <button disabled=${busy} onClick=${inspect} title="Бесплатно: спросить у сервиса модели и их возможности">Проверить</button>
        <button disabled=${busy} onClick=${probe} title="По одному крошечному запросу на каждую настройку">Пробные запросы</button>
      </div>
      ${info && html`<${ModelFacts} info=${info} />`}

      <div class="conn-log" data-slot="conn-log" role="log" aria-live="polite">
        ${log.length === 0 && html`<div class="muted">› готово к работе</div>`}
        ${log.map((l, i) => html`<div key=${i} class=${"line " + l.kind}><span class="muted">${l.at}</span> › ${l.text}</div>`)}
      </div>

      <${Console} catalog=${catalog} kind=${conn.kind} params=${params} info=${info}
        onSet=${setParam} onDrop=${dropParam} />
      <${FallbackSteps} conn=${conn} connections=${connections} steps=${params.fallbacks || []}
        phrases=${params.refusal_phrases || []}
        onSteps=${(steps) => (steps.length ? setParam("fallbacks", steps) : dropParam("fallbacks"))}
        onPhrases=${(list) => (list.length ? setParam("refusal_phrases", list) : dropParam("refusal_phrases"))} />
      <div class="row">
        <button class="primary" disabled=${!dirty} onClick=${saveParams}>Сохранить настройки</button>
        ${dirty && html`<span class="muted">есть несохранённые изменения</span>`}
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
      onSaved();
    } catch (err) { onError(err.message); }
  };

  const del = async (k) => {
    if (!confirm(`Удалить ключ «${k.label || "без названия"}»?`)) return;
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

  return html`
    <h3>Ключи <span class="muted">(пробуются по порядку)</span></h3>
    <ul class="list keys" data-slot="conn-keys">
      ${conn.keys.length === 0 && html`<li class="muted">Ключей нет — для своих моделей обычно и не нужно.</li>`}
      ${conn.keys.map((k, i) => html`
        <li key=${k.id} class="key-row">
          <span>${i + 1}. <strong>${k.label || "без названия"}</strong></span>
          <span class="muted">${k.has_key ? "ключ задан" : "без ключа"}${k.has_proxy ? " · через " + k.proxy : ""}</span>
          <span class="spacer"></span>
          <button disabled=${i === 0} onClick=${() => move(i, -1)} aria-label="Выше">↑</button>
          <button disabled=${i === conn.keys.length - 1} onClick=${() => move(i, 1)} aria-label="Ниже">↓</button>
          <button onClick=${() => start(k)}>Изменить</button>
          <button onClick=${() => del(k)} aria-label="Удалить ключ">✕</button>
        </li>`)}
    </ul>
    ${editing === null && html`<button onClick=${() => start(null)}>＋ Ключ</button>`}
    ${editing !== null && html`
      <form class="stack key-form" onSubmit=${submit}>
        <input placeholder="Название ключа" value=${form.label} onInput=${set("label")} aria-label="Название ключа" />
        <input type="password" autocomplete="off" value=${form.key} onInput=${set("key")} aria-label="Ключ"
          placeholder=${editing === "new" ? "Ключ API" : "Новый ключ (пусто — оставить прежний)"} />
        <input value=${form.proxy} onInput=${set("proxy")} aria-label="Прокси"
          placeholder=${editing === "new" ? "Прокси (необязательно): http://… или socks5://…" : "Новый прокси (пусто — оставить прежний)"} />
        ${editing !== "new" && html`<label><input type="checkbox" checked=${Boolean(form.clearProxy)}
          onChange=${(e) => setForm({ ...form, clearProxy: e.target.checked })} /> убрать прокси</label>`}
        <div class="row"><button class="primary" type="submit">Сохранить</button>
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

function Console({ catalog, kind, params, info, onSet, onDrop }) {
  const specs = useMemo(() => catalog.settings.params.filter((p) => p.kinds.includes(kind)), [catalog, kind]);
  const [templates, setTemplates] = useState([]); // for the "template" setting (text mode)
  useEffect(() => {
    if (specs.some((p) => p.type === "template")) api.get("/templates").then((d) => setTemplates(d.templates)).catch(() => {});
  }, [specs]);
  return html`
    <div class="conn-console" data-slot="conn-console">
      <p class="muted">Галочка — отправлять настройку. Без галочки сервис берёт своё значение.</p>
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

function ParamRow({ spec, value, on, mark, limit, templates, onSet, onDrop }) {
  const [text, setText] = useState(null); // raw text of list/json fields while typing
  const current = on ? value : spec.default;
  const [lo, hi] = limit || [spec.min, spec.max];
  const toggle = (e) => (e.target.checked ? onSet(spec.id, spec.default) : onDrop(spec.id));
  const num = (e) => {
    const v = spec.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
    if (!Number.isNaN(v)) onSet(spec.id, v);
  };
  const json = (raw) => {
    setText(raw);
    try { onSet(spec.id, JSON.parse(raw || "{}")); } catch { /* keep typing */ }
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
    control = html`<select value=${String(current)} disabled=${!on} aria-label=${spec.label}
      onChange=${(e) => onSet(spec.id, e.target.value === "true")}>
      <option value="true">да</option><option value="false">нет</option></select>`;
  } else if (spec.type === "template") {
    control = html`<select value=${current} disabled=${!on} aria-label=${spec.label}
      onChange=${(e) => onSet(spec.id, e.target.value)}>
      <option value="auto">auto — по названию модели</option>
      ${templates.map((t) => html`<option key=${t.id} value=${t.id}>${t.name}${t.builtin ? "" : " (свой)"}</option>`)}
    </select>`;
  } else if (spec.type === "choice") {
    control = html`<select value=${current} disabled=${!on} aria-label=${spec.label}
      onChange=${(e) => onSet(spec.id, e.target.value)}>
      ${spec.choices.map((c) => html`<option value=${c}>${c}</option>`)}</select>`;
  } else if (spec.type === "list") {
    control = html`<textarea rows="2" disabled=${!on} aria-label=${spec.label} placeholder="по одной на строку"
      value=${text ?? (current || []).join("\n")}
      onInput=${(e) => { setText(e.target.value); onSet(spec.id, e.target.value.split("\n").filter((s) => s)); }} />`;
  } else {
    control = html`<textarea rows="2" class="mono" disabled=${!on} aria-label=${spec.label}
      value=${text ?? JSON.stringify(current ?? {})} onInput=${(e) => json(e.target.value)} />`;
  }

  return html`
    <div class=${"param " + mark + (on ? " on" : "")} title=${spec.hint}>
      <label class="param-name"><input type="checkbox" checked=${on} onChange=${toggle} />
        <code>${spec.id}</code> <span>${spec.label}</span></label>
      <span class="param-control">${control}</span>
      <span class=${"param-mark " + mark} title=${word} aria-label=${word}>${sign}</span>
    </div>`;
}
