// Regex rules (stage 11, ADR 0017): the Settings tab «Регекс», one list of rules for a character or a preset
// (`RegexOwner`), the rule window with a live try-out, and `RegexNodes` that draws what a screen rule wrote as HTML.
//
// Patterns are JavaScript-style (`/что/флаги`), the engine is on the server. Rules that came with an imported
// card or preset do nothing until «Разрешить». The HTML a rule writes arrives as a cleaned TREE and is built
// into elements here (never put into the page as markup), with the same short lists of tags and styles as the
// server, so a mistake on one side is caught by the other.
import { Select } from "./Select.js";
import { h, html, useState, useEffect, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, EmptyNote, useLoad } from "./State.js";

// --- drawing the cleaned tree ----------------------------------------------------------------

const TAGS = new Set(("div p h1 h2 h3 h4 h5 h6 ul ol li table thead tbody tfoot tr td th caption blockquote pre details " +
  "summary hr dl dt dd center span b strong i em u s del ins small sub sup code kbd mark abbr a br img progress meter " +
  "cite q time var samp").split(" "));
const VOID = new Set(["br", "hr", "img"]);
const NUMBER = /^-?\d{1,6}(\.\d{1,4})?$/;
const camel = (name) => name.replace(/-([a-z])/g, (_, c) => c.toUpperCase());

function safeStyle(style) {
  const out = {};
  for (const [name, value] of Object.entries(style || {})) {
    if (!/^[a-z-]+$/.test(name) || typeof value !== "string" || /url\s*\(|expression|javascript|@|\\|</i.test(value)) continue;
    out[camel(name)] = value;
  }
  return out;
}

function safeAttrs(tag, attrs) {
  const out = {};
  for (const [name, value] of Object.entries(attrs || {})) {
    if (typeof value !== "string" || name.startsWith("on")) continue;
    if (name === "href" && tag === "a" && /^(https?:\/\/|mailto:)/i.test(value)) {
      out.href = value; out.target = "_blank"; out.rel = "noopener noreferrer";
    } else if (name === "src" && tag === "img" && value.startsWith("/assets/") && !value.includes("..")) {
      out.src = value; out.loading = "lazy";
    } else if (["title", "alt"].includes(name)) out[name] = value;
    else if (["colspan", "rowspan", "width", "height", "value", "max", "min", "low", "high", "optimum"].includes(name) && NUMBER.test(value)) out[name] = value;
    else if (name === "open" && tag === "details") out.open = true;
  }
  return out;
}

function build(node, key) {
  if (typeof node === "string") return node;
  if (!node || !TAGS.has(node.t)) return null;
  const props = { key, ...safeAttrs(node.t, node.a) };
  const style = safeStyle(node.s);
  if (Object.keys(style).length) props.style = style;
  if (VOID.has(node.t)) return h(node.t, props);
  return h(node.t, props, ...(node.c || []).map((child, i) => build(child, i)));
}

export function RegexNodes({ nodes }) {
  return html`<div class="rx-html" data-slot="regex-html">${(nodes || []).map((n, i) => build(n, i))}</div>`;
}

// --- the try-out ------------------------------------------------------------------------------

function Highlighted({ text, matches }) {
  const parts = [];
  let at = 0;
  for (const m of matches.slice(0, 300)) {
    if (m.start < at) continue;
    if (m.start > at) parts.push(text.slice(at, m.start));
    parts.push(html`<mark key=${m.start} title=${m.groups.length ? "Группы: " + m.groups.map((g) => g ?? "—").join(" | ") : ""}>${m.text || "∅"}</mark>`);
    at = m.end;
  }
  parts.push(text.slice(at));
  return html`<div class="rx-sample" data-slot="regex-sample">${parts}</div>`;
}

const WHEN = [
  ["display", "Только на экране", "В чате вы видите изменённый текст. В сохранённом сообщении и в том, что получает модель, всё как было."],
  ["prompt", "Только для модели", "Модель получает изменённый текст. В чате и в сохранённом сообщении всё как было. Видно в «Что видела модель»."],
  ["both", "На экране и для модели", "И вы, и модель видят изменённый текст. Сохранённое сообщение не меняется."],
  ["stored", "В самом сообщении", "Один раз при сохранении сообщения его текст меняется насовсем (прежний можно вернуть из сообщения)."],
];
const PLACES = [["user", "Ваши сообщения"], ["assistant", "Ответы модели"], ["reasoning", "Размышления модели"], ["lore", "Записи лорбука"]];
const MACROS = [["none", "Не подставлять"], ["raw", "Подставлять как есть"], ["escaped", "Подставлять как обычный текст"]];

const EMPTY = {
  name: "", enabled: true, find: "", replace: "", trim: [], places: ["user", "assistant"], when: "display",
  min_depth: null, max_depth: null, run_on_edit: false, macros: "none", render_html: false,
};

function RuleEditor({ rule, scope, owner, chatId, onClose, onSaved }) {
  const [draft, setDraft] = useState(() => ({ ...EMPTY, ...(rule || {}), trim: (rule && rule.trim) || [] }));
  const [trimText, setTrimText] = useState(() => ((rule && rule.trim) || []).join("\n"));
  const [sample, setSample] = useState("Привет, **мир**! [hp=5] Сегодня *хороший* день.");
  const [probe, setProbe] = useState(null);
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const run = useRef(0);
  useEscape(onClose);
  const set = (key) => (e) => setDraft((d) => ({ ...d, [key]: e.target.value }));
  const flip = (key) => (e) => setDraft((d) => ({ ...d, [key]: e.target.checked }));
  const number = (key) => (e) => {
    const v = e.target.value.trim();
    setDraft((d) => ({ ...d, [key]: v === "" ? null : Math.max(0, parseInt(v, 10) || 0) }));
  };
  const togglePlace = (id) => setDraft((d) => ({
    ...d, places: d.places.includes(id) ? d.places.filter((p) => p !== id) : [...d.places, id],
  }));
  const payload = () => ({
    name: draft.name, enabled: draft.enabled, find: draft.find, replace: draft.replace,
    trim: trimText.split("\n").filter((t) => t !== ""), places: draft.places, when: draft.when,
    min_depth: draft.min_depth, max_depth: draft.max_depth, run_on_edit: draft.run_on_edit,
    macros: draft.macros, render_html: draft.render_html && ["display", "both"].includes(draft.when),
  });

  // Live try-out: after a short pause in typing, ask the server what the rule does to the sample.
  useEffect(() => {
    if (!draft.find.trim()) { setProbe(null); return undefined; }
    const mine = ++run.current;
    const timer = setTimeout(() => {
      api.post("/regex/try", { rule: payload(), text: sample, chat_id: chatId || undefined })
        .then((r) => { if (mine === run.current) setProbe(r); })
        .catch((e) => { if (mine === run.current) setProbe({ ok: false, error: e.message, matches: [], result: sample }); });
    }, 350);
    return () => clearTimeout(timer);
  }, [draft.find, draft.replace, trimText, draft.macros, draft.render_html, draft.when, sample]);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setSaveError("");
    try {
      const saved = rule && rule.id
        ? await api.put(`/regex/rules/${rule.id}`, payload())
        : await api.post("/regex/rules", { ...payload(), scope, owner });
      onSaved(saved);
    } catch (err) {
      setSaveError(err.message);
    } finally { setSaving(false); }
  };

  const whenHint = (WHEN.find((w) => w[0] === draft.when) || WHEN[0])[2];
  const screen = ["display", "both"].includes(draft.when);
  return html`<div class="overlay" ...${backdrop(onClose)}>
    <form class="dialog wide stack regex-editor" data-slot="regex-editor" role="dialog" aria-modal="true"
      aria-label="Правило" onSubmit=${save}>
      <div class="side-head"><strong>${rule && rule.id ? "Правило" : "Новое правило"}</strong><span class="spacer"></span>
        <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
      <label class="stack">Название
        <input value=${draft.name} placeholder="Например: убрать блок состояния" onInput=${set("name")} /></label>
      <label class="stack">Что искать
        <span class="hint">Шаблон в стиле JavaScript: <code>/что_искать/флаги</code>, например <code>/\\*\\*(.+?)\\*\\*/g</code>.
          Можно просто слово. Флаги: <b>g</b> — заменить все совпадения (без неё только первое), <b>i</b> — не различать
          заглавные и строчные, <b>s</b> — точка находит и перенос строки, <b>m</b> — «^» и «$» работают для каждой строки.</span>
        <input class="mono" value=${draft.find} spellcheck="false" autocapitalize="off" autocomplete="off"
          onInput=${set("find")} /></label>
      ${probe && !probe.ok && html`<p class="error" role="alert" data-slot="regex-error">${probe.error}</p>`}
      <label class="stack">На что заменить
        <span class="hint"><code>$1</code>, <code>$2</code> — то, что нашлось в скобках; <code>${"$&"}</code> или
          <code>{{match}}</code> — всё найденное; <code>${"$<имя>"}</code> — группа с именем. Пусто — найденное просто удалится.
          Можно писать <code>{{char}}</code> и <code>{{user}}</code>.</span>
        <textarea rows="3" class="mono" value=${draft.replace} spellcheck="false" onInput=${set("replace")}></textarea></label>
      <label class="stack">Вырезать из вставляемого текста
        <span class="hint">По одной строке. Эти слова убираются из найденного, прежде чем оно попадёт в замену.</span>
        <textarea rows="2" class="mono" value=${trimText} spellcheck="false" onInput=${(e) => setTrimText(e.target.value)}></textarea></label>

      <fieldset class="regex-where"><legend>Где работает</legend>
        ${PLACES.map(([id, label]) => html`<label key=${id}><input type="checkbox" checked=${draft.places.includes(id)}
          onChange=${() => togglePlace(id)} /> ${label}</label>`)}
        <span class="hint">Сообщения «Система» и слэш-команды правила не трогают. Записи лорбука меняются только для модели.</span>
      </fieldset>
      <label class="stack">Когда
        <${Select} value=${draft.when} onChange=${set("when")}>${WHEN.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}</${Select}>
        <span class="hint">${whenHint}</span></label>
      <div class="row regex-depth">
        <label>Глубина от <input type="number" min="0" inputmode="numeric" value=${draft.min_depth ?? ""} onInput=${number("min_depth")} /></label>
        <label>до <input type="number" min="0" inputmode="numeric" value=${draft.max_depth ?? ""} onInput=${number("max_depth")} /></label>
      </div>
      <span class="hint">Глубина — сколько сообщений назад: 0 — самое последнее, 1 — предыдущее. Пусто — без ограничения.
        Не действует на записи лорбука.</span>
      <label><input type="checkbox" checked=${draft.run_on_edit} disabled=${draft.when !== "stored"} onChange=${flip("run_on_edit")} />
        Применять и когда вы правите сообщение <span class="hint">(только для «В самом сообщении»)</span></label>
      <label class="stack">Макросы в шаблоне
        <${Select} value=${draft.macros} onChange=${set("macros")}>${MACROS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}</${Select}>
        <span class="hint">Если в шаблоне есть <code>{{char}}</code> или <code>{{user}}</code>: «как есть» — имя подставится в шаблон
          как часть шаблона; «как обычный текст» — как слова, без особых знаков.</span></label>
      <label><input type="checkbox" checked=${draft.render_html && screen} disabled=${!screen} onChange=${flip("render_html")} />
        Показывать результат как разметку (HTML)
        <span class="hint">Для панелей состояния и красивых блоков. Скрипты, формы, внешние картинки и сторонние стили вырезаются,
          остаются простые теги и оформление. Только для того, что видно на экране.</span></label>

      <div class="regex-try" data-slot="regex-try">
        <strong>Проба</strong>
        <span class="hint">Вставьте кусок текста: найденное подсветится, ниже будет результат. Ничего не сохраняется.</span>
        <textarea rows="3" value=${sample} aria-label="Текст для пробы" onInput=${(e) => setSample(e.target.value)}></textarea>
        ${probe && !probe.ok && html`<p class="error" role="alert" data-slot="regex-try-error">${probe.error}</p>`}
        ${probe && probe.ok && html`
          <${Highlighted} text=${sample} matches=${probe.matches} />
          <small class="muted">${probe.matches.length ? `Найдено: ${probe.matches.length}` : "Ничего не найдено"}</small>
          <div class="rx-result" data-slot="regex-result">
            ${probe.nodes ? html`<${RegexNodes} nodes=${probe.nodes} />` : html`<div class="rx-sample">${probe.result}</div>`}</div>`}
      </div>
      ${saveError && html`<p class="error" role="alert">${saveError}</p>`}
      <div class="row">
        <span class="spacer"></span>
        <button type="button" onClick=${onClose}>Отмена</button>
        <button type="submit" class="primary" disabled=${saving || !draft.find.trim()}>Сохранить</button>
      </div>
    </form>
  </div>`;
}

// --- a list of rules --------------------------------------------------------------------------

const WHEN_BADGE = { display: "экран", prompt: "модель", both: "экран + модель", stored: "текст" };

function RuleRow({ rule, index, count, onMove, onEdit, onToggle, onCopy, onDelete }) {
  const problem = rule.error ? "Шаблон не работает: " + rule.error
    : rule.slow ? "Работает слишком долго, пропускается. Исправьте шаблон"
      : !rule.approved ? "Ждёт вашего разрешения" : !rule.places.length ? "Не выбрано, где работает" : "";
  return html`<div class=${"regex-rule" + (rule.enabled ? "" : " off")} data-slot="regex-rule">
    <input type="checkbox" checked=${rule.enabled} aria-label=${"Включить: " + (rule.name || rule.find)}
      onChange=${() => onToggle(rule)} />
    <div class="regex-name"><button type="button" class="link" onClick=${() => onEdit(rule)} title=${rule.name || rule.find}>${rule.name || rule.find}</button>
      <small class="muted">${WHEN_BADGE[rule.when]}${rule.render_html ? " · разметка" : ""}</small>
      ${problem && html`<small class="regex-problem" title=${problem}><${Icon} name="triangle-alert" /> ${problem}</small>`}</div>
    <div class="regex-buttons">
      <${IconButton} icon="arrow-up" label="Выше" disabled=${index === 0} onClick=${() => onMove(index, -1)} />
      <${IconButton} icon="arrow-down" label="Ниже" disabled=${index === count - 1} onClick=${() => onMove(index, 1)} />
      <${IconButton} icon="pencil" label="Изменить" onClick=${() => onEdit(rule)} />
      <${IconButton} icon="copy" label="Копия" onClick=${() => onCopy(rule)} />
      <${IconButton} icon="trash-2" label="Удалить" onClick=${() => onDelete(rule)} />
    </div>
  </div>`;
}

const PAGE_RULES = 40;  // a list of a thousand rules is shown by pages: all of them in the page would freeze a phone
const SEARCH_FROM = 20; // the search field appears with this many rules

function RuleList({ scope, owner, name, rules, pending, chatId, onError, changed }) {
  const [editing, setEditing] = useState(null); // a rule, or {} for a new one
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(PAGE_RULES);
  const needle = query.trim().toLowerCase();
  const found = rules.map((rule, i) => ({ rule, i }))
    .filter(({ rule }) => !needle || (rule.name || "").toLowerCase().includes(needle) || (rule.find || "").toLowerCase().includes(needle));
  const fileInput = useRef(null);
  const act = (fn) => async (...args) => {
    try { await fn(...args); changed(); } catch (e) { onError(e.message); }
  };
  const move = act(async (index, delta) => {
    const ids = rules.map((r) => r.id);
    [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
    await api.put("/regex/order", { scope, owner, ids });
  });
  const toggle = act((rule) => api.put(`/regex/rules/${rule.id}`, { enabled: !rule.enabled }));
  const copy = act((rule) => api.post(`/regex/rules/${rule.id}/copy`));
  const remove = act(async (rule) => {
    if (await ask(`Удалить правило «${rule.name || rule.find}»?`, { ok: "Удалить", danger: true, skip: "rule" })) await api.del(`/regex/rules/${rule.id}`);
  });
  const allow = act(async () => { await api.post("/regex/approve", { scope, owner }); notify("Правила разрешены", "ok"); });
  const importFile = act(async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    let scripts;
    try { scripts = JSON.parse(await file.text()); } catch (err) { throw new Error("Файл — не JSON"); }
    const done = await api.post("/regex/import", { scope, owner, scripts });
    notify(`Добавлено правил: ${done.imported.length}` + (done.problems.length ? `. Не заработают: ${done.problems.map((p) => p.name).join(", ")} (выключены, причина в строке правила)` : ""),
      done.problems.length ? "info" : "ok");
  });
  const exportHref = `/api/regex/export?scope=${scope}&owner=${encodeURIComponent(owner || "")}`;

  return html`<div class="regex-list" data-slot="regex-list">
    ${pending > 0 && html`<div class="regex-pending" role="status">
      <p>Эти правила пришли вместе с ${scope === "preset" ? "пресетом" : "карточкой"} и пока <strong>не работают</strong>: правило может менять то,
        что получает модель, поэтому оно включится только с вашего согласия. Прочитайте их и разрешите, если всё в порядке.</p>
      <button class="primary" onClick=${allow}>Разрешить все (${pending})</button></div>`}
    ${rules.length === 0 && html`<${EmptyNote} title="Правил пока нет" hint="Правило находит кусок текста и заменяет его: на экране, для модели или в самом сообщении." />`}
    ${rules.length >= SEARCH_FROM && html`<input type="search" class="regex-search" value=${query} placeholder=${`Найти среди ${rules.length} правил`}
      aria-label="Найти правило по имени или шаблону" onInput=${(e) => { setQuery(e.target.value); setLimit(PAGE_RULES); }} />`}
    ${found.slice(0, limit).map(({ rule, i }) => html`<${RuleRow} key=${rule.id} rule=${rule} index=${i} count=${rules.length} onMove=${move}
      onEdit=${setEditing} onToggle=${toggle} onCopy=${copy} onDelete=${remove} />`)}
    ${found.length > limit && html`<button class="regex-more" onClick=${() => setLimit(limit + PAGE_RULES)}>Показать ещё (${found.length - limit})</button>`}
    ${needle && found.length === 0 && html`<p class="muted">Ничего не найдено.</p>`}
    <div class="row regex-actions">
      <button onClick=${() => setEditing({})}><${Icon} name="plus" /> Новое правило</button>
      <button onClick=${() => fileInput.current.click()}><${Icon} name="upload" /> Импорт</button>
      <input ref=${fileInput} type="file" accept=".json,application/json" hidden onChange=${importFile} />
      ${rules.length > 0 && html`<a class="button" href=${exportHref} download><${Icon} name="download" /> Экспорт</a>`}
    </div>
    ${editing && html`<${RuleEditor} rule=${editing.id ? editing : null} scope=${scope} owner=${owner} chatId=${chatId}
      onClose=${() => setEditing(null)} onSaved=${() => { setEditing(null); changed(); }} />`}
  </div>`;
}

// The rules of one character or one preset (inside their own windows).
export function RegexOwner({ scope, owner, onError }) {
  const loaded = useLoad(() => api.get(`/regex/rules?scope=${scope}&owner=${encodeURIComponent(owner)}`).then((d) => d.rules), [scope, owner]);
  useEffect(() => {
    const again = () => loaded.reload();
    window.addEventListener("tavern:regex", again);
    return () => window.removeEventListener("tavern:regex", again);
  }, [scope, owner]);
  if (!loaded.data) return html`<${Pending} error=${loaded.error} onRetry=${loaded.reload} />`;
  const pending = loaded.data.filter((r) => !r.approved).length;
  return html`<${RuleList} scope=${scope} owner=${owner} rules=${loaded.data} pending=${pending} onError=${onError}
    changed=${() => loaded.reload()} />`;
}

// Settings tab «Регекс».
export function RegexTab({ onError }) {
  const loaded = useLoad(() => api.get("/regex"), []);
  useEffect(() => {
    const again = () => loaded.reload();
    window.addEventListener("tavern:regex", again);
    return () => window.removeEventListener("tavern:regex", again);
  }, []);
  if (!loaded.data) return html`<${Pending} error=${loaded.error} onRetry=${loaded.reload} />`;
  const { global, characters, presets } = loaded.data;
  const reload = () => loaded.reload();
  const group = (title, items, scope) => items.length > 0 && html`<div class="regex-group">
    <strong>${title}</strong>
    ${items.map((o) => html`<details key=${o.id} open=${o.pending > 0 || undefined}>
      <summary><span class="regex-owner">${o.name}</span> <small class="muted">${o.rules.length}${o.pending ? ` · ждут разрешения: ${o.pending}` : ""}</small></summary>
      <${RuleList} scope=${scope} owner=${o.id} rules=${o.rules} pending=${o.pending} onError=${onError} changed=${reload} />
    </details>`)}</div>`;
  return html`<div class="regex-tab" data-slot="regex-tab">
    <p class="hint">Регекс-правило находит в тексте нужное и заменяет его. Так прячут блоки состояния, чистят ответы модели,
      оформляют панели. Одно правило может менять то, что вы видите, то, что получает модель, или сам сохранённый текст.
      Порядок: сначала правила для всех, потом правила пресета чата, потом правила персонажей чата. Внутри списка — сверху вниз.
      Правила персонажа и пресета удобнее открывать из их карточки; правила из ST-файлов импортируются здесь же.</p>
    <h3>Для всех</h3>
    <${RuleList} scope="global" owner="" rules=${global} pending=${0} onError=${onError} changed=${reload} />
    ${group("Правила персонажей", characters, "character")}
    ${group("Правила пресетов", presets, "preset")}
  </div>`;
}
