// Character tree editor: the card is the root bubble, blocks hang under it. Two views of one tree:
// a map (pan and pinch-zoom with fingers, drag bubbles, drop a bubble on another to move it inside)
// and a list. A bubble's ring colour is its mode: blue = always sent, green = sent when the topic
// comes up (keywords / meaning), grey = off. A bubble shows its sub-bubbles either joined by lines
// or inside its own space ("nested"); a found group sends all of them.
// Moving: drag a bubble onto another to put it inside. Ports (the dots on a bubble's sides): pull a
// line from one to another bubble to link them (drawn as faint arcs behind everything).
// "Отцепить" sets a bubble free (a free bubble and its chain are pale and not sent). The small
// number at a bubble's corner is its place among its neighbours: the order the model reads them in.
// A line's colour shows the chance of the green bubble it leads to.
// Built to stay smooth with thousands of bubbles: only bubbles in view are rendered, lines are a
// few SVG paths (one per colour), pan/zoom moves one layer, and pointer moves apply once per frame.
import { Select } from "./Select.js";
import { html, useState, useEffect, useRef, useMemo, useCallback } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { ROLES } from "./roles.js";
import { Icon, IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { EmptyNote, Pending } from "./State.js";
import { notify } from "../core/notify.js";
import { panelFolded, setPanelFolded, useLayout } from "../core/layout.js";

const COL = 230; // map: distance between levels
const ROW = 66; // map: distance between leaves
export const W = 168; // bubble size
export const H = 46;
const PAD = 18; // space around a nested group
export const GAP = 14; // bubbles push each other apart to keep at least this much space
const MODES = [["always", "Всегда"], ["search", "По теме"], ["off", "Выключен"]];
export const MARGIN_PX = 300; // bubbles this far outside the screen are kept in the page
export const MOVE_REDRAW = 120; // the camera may move this many screen pixels before the bubbles are chosen again
export const FAR_ZOOM = 0.45; // below this zoom bubbles are drawn plain: no ports, numbers or shadows
const SAVE_AFTER = 600; // ms of quiet typing before a bubble's text is saved
const PORT_TITLE = "Потяните линию к другому пузырю, чтобы связать их ссылкой";

// Bubbles never overlap: any two that do are pushed apart along the shorter way out. A grid of
// cells keeps this near-linear for thousands of bubbles. `fixed` ids (the card) do not move.
function separate(pos, fixed) {
  const cw = W + GAP; const ch = H + GAP;
  for (let pass = 0; pass < 6; pass += 1) {
    const grid = new Map();
    for (const [id, p] of pos) {
      const key = `${Math.floor(p.x / cw)},${Math.floor(p.y / ch)}`;
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(id);
    }
    let moved = false;
    for (const [id, p] of pos) {
      if (fixed.has(id)) continue;
      const gx = Math.floor(p.x / cw); const gy = Math.floor(p.y / ch);
      for (let i = -1; i <= 1; i += 1) for (let j = -1; j <= 1; j += 1) {
        for (const other of grid.get(`${gx + i},${gy + j}`) || []) {
          if (other === id) continue;
          const q = pos.get(other);
          const ox = cw - Math.abs(p.x - q.x); const oy = ch - Math.abs(p.y - q.y);
          if (ox <= 0 || oy <= 0) continue;
          // of two loose bubbles the one with the smaller id gives way: the same result every time
          if (!fixed.has(other) && other < id) continue;
          if (oy <= ox) p.y += (p.y >= q.y ? oy : -oy);
          else p.x += (p.x >= q.x ? ox : -ox);
          moved = true;
        }
      }
    }
    if (!moved) break;
  }
  return pos;
}

// Positions: saved ones win; unsaved bubbles follow the nearest saved ancestor so a moved group
// keeps its shape. Returns {pos: Map id -> {x, y}, kids: Map parent -> [block], byId}.
function layoutTree(blocks) {
  const byId = new Map(blocks.map((b) => [b.id, b]));
  const kids = new Map();
  for (const b of blocks) {
    const parent = byId.has(b.parent_id) ? b.parent_id : null;
    if (!kids.has(parent)) kids.set(parent, []);
    kids.get(parent).push(b);
  }
  for (const list of kids.values()) list.sort((a, b) => a.position - b.position || (a.created_at < b.created_at ? -1 : 1));
  const auto = new Map();
  let row = 0;
  const place = (id, depth) => {
    const children = (byId.get(id)?.layout?.collapsed ? [] : kids.get(id)) || [];
    let y;
    if (!children.length) { y = row * ROW; row += 1; } else {
      children.forEach((c) => place(c.id, depth + 1));
      y = (auto.get(children[0].id).y + auto.get(children[children.length - 1].id).y) / 2;
    }
    auto.set(id, { x: depth * COL, y });
  };
  const top = kids.get(null) || [];
  top.forEach((b) => place(b.id, 1));
  const rootY = top.length ? (auto.get(top[0].id).y + auto.get(top[top.length - 1].id).y) / 2 : 0;
  const pos = new Map([["root", { x: 0, y: rootY }]]);
  const settle = (id, shift) => {
    const block = byId.get(id);
    const a = auto.get(id);
    if (!a) return;
    const saved = block.layout && typeof block.layout.x === "number" ? { x: block.layout.x, y: block.layout.y } : null;
    const p = saved || { x: a.x + shift.x, y: a.y + shift.y };
    pos.set(id, p);
    const next = { x: p.x - a.x, y: p.y - a.y };
    if (!block.layout?.collapsed) (kids.get(id) || []).forEach((c) => settle(c.id, next));
  };
  top.forEach((b) => settle(b.id, { x: 0, y: 0 }));
  // Saved and automatic positions alike: bubbles that would overlap push each other apart.
  const loose = new Map([...pos].map(([id, p]) => [id, { ...p }]));
  separate(loose, new Set(["root"]));
  return { pos: loose, kids, byId };
}

// Line colours by the chance of the green bubble they lead to.
const CHANCE_BANDS = [[100, "c100"], [75, "c75"], [50, "c50"], [25, "c25"], [0, "c0"]];
const band = (b) => (b.mode !== "search" ? "c100" : CHANCE_BANDS.find(([min]) => (b.chance ?? 100) >= min)[1]);

// A link between two bubbles: a wide arc from centre to centre that bows out sideways (to the
// right when they stand one above the other), drawn under the bubbles, so it never reads as a
// parent line.
const arc = (p, q) => {
  const x1 = p.x + W / 2; const y1 = p.y + H / 2; const x2 = q.x + W / 2; const y2 = q.y + H / 2;
  const dx = x2 - x1; const dy = y2 - y1; const len = Math.hypot(dx, dy) || 1;
  let nx = -dy / len; let ny = dx / len; // perpendicular
  if (nx < 0 || (nx === 0 && ny < 0)) { nx = -nx; ny = -ny; }
  const bow = W * 1.3 + len * 0.3; // the top of the arc clears the bubbles it passes
  const cx = (x1 + x2) / 2 + nx * bow; const cy = (y1 + y2) / 2 + ny * bow;
  return `M${x1} ${y1}Q${cx} ${cy} ${x2} ${y2}`;
};

// Digits only, at most `max` (empty stays empty).
const digitsOf = (e, max) => {
  const digits = e.target.value.replace(/\D+/g, "").slice(0, 4);
  if (digits !== e.target.value) e.target.value = digits;
  return digits === "" ? null : Math.min(max, Number(digits));
};

function descendants(kids, id, out = []) {
  for (const c of kids.get(id) || []) { out.push(c.id); descendants(kids, c.id, out); }
  return out;
}

const curve = (p, q) => {
  const x1 = p.x + W; const y1 = p.y + H / 2; const x2 = q.x; const y2 = q.y + H / 2; const mx = (x1 + x2) / 2;
  return `M${x1} ${y1}C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`;
};

function Ring({ mode }) {
  return html`<span class=${"mode-dot mode-" + mode} aria-hidden="true"></span>`;
}

const blockFields = (d) => ({ title: d.title, content: d.content, mode: d.mode, keywords: d.keywords,
  depth: d.depth, role: d.role, sticky: d.sticky, chance: d.chance, every: d.every, rules: d.rules || {},
  layout: { display: d.layout.display || "line", collapsed: Boolean(d.layout.collapsed) } });

const LOGICS = [["and_any", "и хотя бы одно из"], ["and_all", "и все"], ["not_any", "но ни одного из"],
  ["not_all", "но не все"]];
const PLACES = [["", "по полю «Глубина»"], ["card_before", "до карточек"], ["card_after", "после карточек"],
  ["an_top", "у заметки автора, сверху"], ["an_bottom", "у заметки автора, снизу"],
  ["examples_top", "перед примерами диалога"], ["examples_bottom", "после примеров диалога"]];
const listOf = (text) => text.split(",").map((w) => w.trim()).filter(Boolean);

// «Ещё»: the finer options of a bubble (core/retrieval.py lists them). Folded: most bubbles need none.
function MoreRules({ rules, onRules }) {
  const [people, setPeople] = useState(null); // characters, loaded when the filter is first opened
  const [peopleError, setPeopleError] = useState("");
  const [open, setOpen] = useState(() => Object.keys(rules).some((k) => k !== "title_key"));
  const flag = (key, label, title, fallback = false) => html`<label title=${title}>
    <input type="checkbox" checked=${rules[key] ?? fallback}
      onChange=${(e) => onRules({ [key]: e.target.checked === fallback ? null : e.target.checked })} /> ${label}</label>`;
  const number = (key, label, title, max, placeholder = "") => html`<label title=${title}>${label}
    <input class="num" inputmode="numeric" pattern="[0-9]*" placeholder=${placeholder}
      value=${rules[key] === undefined ? "" : String(rules[key])} onInput=${(e) => onRules({ [key]: digitsOf(e, max) }, false)} /></label>`;
  const words = (key, label, title, placeholder = "слово, ещё слово") => html`<label class="grow" title=${title}>${label}
    <input value=${(rules[key] || []).join(", ")} placeholder=${placeholder}
      onChange=${(e) => onRules({ [key]: listOf(e.target.value) })} /></label>`;
  const filter = rules.filter || { mode: "only", ids: [] };
  const loadPeople = () => {
    setPeopleError("");
    api.get("/characters").then((d) => setPeople(d.characters)).catch((e) => setPeopleError(e.message));
  };
  useEffect(() => {
    if (open && people === null) loadPeople();
  }, [open]);
  const toggleWho = (id, on) => {
    const ids = on ? [...filter.ids, id] : filter.ids.filter((x) => x !== id);
    onRules({ filter: ids.length ? { mode: filter.mode, ids } : null });
  };
  return html`<details class="tree-more" data-slot="tree-more" open=${open} onToggle=${(e) => setOpen(e.target.open)}>
    <summary>Ещё</summary>
    <div class="stack">
      ${flag("title_key", "название — тоже ключевое слово", "Снимите, если название — просто подпись", true)}
      <div class="row">${words("keys2", "Вторые слова", "Проверяются, когда нашлось хоть одно ключевое слово")}
        <${Select} value=${rules.logic || "and_any"} aria-label="Как проверять вторые слова"
          onChange=${(e) => onRules({ logic: e.target.value === "and_any" ? null : e.target.value })}>
          ${LOGICS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
        </${Select}></div>
      <span class="hint">Вторые слова проверяются, когда нашлось хоть одно ключевое слово</span>
      <div class="row">
        ${flag("whole", "только целые слова", "Без других окончаний: «кот» не найдёт «котята»")}
        ${flag("case", "учитывать регистр", "«Rat» не найдёт «rat»")}</div>
      <div class="row">
        ${number("scan", "Смотреть сообщений", "Сколько последних сообщений чата просматривать для этого пузыря", 1000, "как везде")}
        ${number("cooldown", "Пауза", "После того как пузырь отдержался, столько ответов он не приходит", 1000, "0")}
        ${number("delay", "Не раньше ответа №", "Пузырь не приходит, пока в чате меньше ответов", 9999, "0")}</div>
      <span class="hint">Смотреть сообщений: сколько последних сообщений читать. Пауза: сколько ответов пузырь не приходит после срабатывания. Не раньше ответа №: пока в чате меньше ответов, пузырь не приходит</span>
      <div class="row">${words("groups", "Группа", "Из найденных пузырей одной группы приходит только один", "название группы")}
        ${number("weight", "Вес", "Шанс победить в группе относительно других (обычно 100)", 1000, "100")}
        ${flag("main", "главный в группе", "Главный побеждает в своей группе (если никто не держится)")}</div>
      <span class="hint">Из найденных пузырей одной группы приходит только один. Вес: шанс победить в группе, обычно 100. Главный побеждает, если никто не держится</span>
      <label>Куда в промпте
        <${Select} value=${rules.place || ""} onChange=${(e) => onRules({ place: e.target.value || null })}>
          ${PLACES.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
        </${Select}></label>
      <div class="row">
        ${flag("no_wake", "не будит других", "Текст этого пузыря не подтягивает другие записи лорбуков по цепочке")}
        ${flag("not_woken", "не будится другими", "Приходит только по словам из чата, не по цепочке")}</div>
      <span class="hint">Не будит других: текст пузыря не подтягивает другие пузыри по цепочке. Не будится другими: пузырь приходит только по словам из чата</span>
      <div class="stack">
        <label>Персонажи
          <${Select} value=${filter.mode} onChange=${(e) => filter.ids.length && onRules({ filter: { ...filter, mode: e.target.value } })}>
            <option value="only">только когда в чате</option><option value="except">кроме чатов, где есть</option>
          </${Select}></label>
        <div class="chips">${(people || []).map((c) => html`<label key=${c.id} class="chip">
          <input type="checkbox" checked=${filter.ids.includes(c.id)} onChange=${(e) => toggleWho(c.id, e.target.checked)} /> ${c.name}</label>`)}
          ${people && !people.length && !peopleError ? html`<span class="muted">Персонажей нет</span>` : ""}</div>
        ${peopleError && html`<${Pending} error=${peopleError} onRetry=${loadPeople} />`}
        ${!filter.ids.length && html`<span class="hint">Никто не отмечен: пузырь работает с любыми персонажами</span>`}
      </div>
    </div>
  </details>`;
}

// Every change saves itself: typing after a short pause (and when the panel closes or another
// bubble is picked), buttons and switches at once. There is no "Сохранить" button.
function BlockPanel({ block, path, vectorized, askDelete, links, hasKids, order, onSave, onDelete, onAddChild, onMove,
  onOrder, onDetach, onStartLink, onUnlink, onChanceBelow, onOpen, onClose, onError, onNotice }) {
  const { folded, button: foldButton } = useFoldPanel();
  const [below, setBelow] = useState(null); // chance for every sub-bubble, before "Применить"
  const [draft, setDraft] = useState(block);
  const [word, setWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [place, setPlace] = useState(null); // order number being typed
  const suggested = useRef([]); // every word "Подобрать" gave for this bubble: never offered again
  useEffect(() => { suggested.current = []; }, [block.id]);
  useEffect(() => setPlace(null), [block.id, order && order.n]);
  const [state, setState] = useState("saved"); // saved | waiting | saving | failed
  const pending = useRef(null); // {id, changes} not sent yet
  const timer = useRef(0);
  const flush = () => {
    clearTimeout(timer.current);
    const job = pending.current;
    pending.current = null;
    if (!job) return Promise.resolve();
    setState("saving");
    return onSave(job.id, job.changes).then((ok) => {
      if (ok === false) {
        if (!pending.current) pending.current = job; // kept: the next change or «Повторить» sends it again
        setState("failed");
      } else setState(pending.current ? "waiting" : "saved");
    });
  };
  // A different bubble: send what is left of the old one, then show the new one.
  useEffect(() => { setDraft(block); setWord(""); return () => { flush(); }; }, [block.id]);
  const set = (patch, now = false) => {
    setDraft((d) => {
      const next = { ...d, ...patch };
      pending.current = { id: block.id, changes: blockFields(next) };
      return next;
    });
    setState("waiting");
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, now ? 0 : SAVE_AFTER);
  };
  const addWords = (text) => {
    const fresh = text.split(",").map((w) => w.trim()).filter(Boolean);
    if (!fresh.length) return;
    const known = new Set(draft.keywords.map((k) => k.toLowerCase()));
    set({ keywords: [...draft.keywords, ...fresh.filter((w) => !known.has(w.toLowerCase()))] }, true);
    setWord("");
  };
  const suggest = async () => {
    setBusy(true);
    try {
      await flush();
      const { keywords } = await api.post(`/blocks/${block.id}/suggest-keywords`, { avoid: suggested.current });
      suggested.current = [...suggested.current, ...keywords];
      if (keywords.length) addWords(keywords.join(","));
      else onNotice("Новых слов модель не нашла", "info");
    } catch (e) { onError(e.message); } finally { setBusy(false); }
  };
  const remove = async () => {
    if (askDelete && !await ask(`Удалить «${block.title}» вместе с подпузырями?`, { ok: "Удалить", danger: true })) return;
    pending.current = null;
    clearTimeout(timer.current);
    onDelete(block);
  };
  // Depth: only digits can be typed; empty = next to the card.
  const depthInput = (e) => set({ depth: digitsOf(e, 1000) });

  return html`
    <aside class=${"tree-panel" + (folded ? " folded" : "")} data-slot="tree-panel" aria-label="Пузырь" data-folded=${folded ? "true" : "false"}>
      <div class="row">${foldButton}<span class="muted path">${path}</span><span class="spacer"></span>
        <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
      <input class="tree-title" value=${draft.title} aria-label="Название"
        onInput=${(e) => set({ title: e.target.value })} />
      <div class="mode-pick" role="radiogroup" aria-label="Когда отправлять">
        ${MODES.map(([id, label]) => html`<button key=${id} role="radio" aria-checked=${draft.mode === id}
          class=${"mode-btn mode-" + id} onClick=${() => set({ mode: id }, true)}><${Ring} mode=${id} /> ${label}</button>`)}
      </div>
      <textarea rows="7" value=${draft.content} placeholder="Текст, который получит модель"
        onInput=${(e) => set({ content: e.target.value })}></textarea>
      ${draft.mode === "search" && html`
        <div class="stack">
          <span class="muted">Ключевые слова
            ${vectorized ? html` · <${Icon} name="waypoints" size=${14} /> векторизован` : ""}</span>
          <span class="hint">Название тоже считается, окончания не важны</span>
          <div class="chips">
            ${draft.keywords.map((k) => html`<span key=${k} class="chip">${k}
              <button type="button" aria-label=${"Убрать слово " + k} title="Убрать"
                onClick=${() => set({ keywords: draft.keywords.filter((x) => x !== k) }, true)}><${Icon} name="x" size=${14} /></button></span>`)}
          </div>
          <div class="row">
            <input value=${word} placeholder="слово, ещё слово" aria-label="Новое ключевое слово"
              onInput=${(e) => setWord(e.target.value)}
              onKeyDown=${(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addWords(word); } }} />
            <${IconButton} icon="plus" label="Добавить слово" onClick=${() => addWords(word)} />
            <button disabled=${busy} onClick=${suggest} title="Модель прочитает текст и предложит слова">
              ${busy ? "Думаю…" : "Подобрать"}</button>
          </div>
        </div>`}
      <div class="row">
        <label title="За сколько сообщений до конца чата вставить. Пусто — рядом с карточкой">Глубина
          <input class="num" inputmode="numeric" pattern="[0-9]*" placeholder="рядом"
            value=${draft.depth === null ? "" : String(draft.depth)} onInput=${depthInput} /></label>
        <label>От кого
          <${Select} value=${draft.role} onChange=${(e) => set({ role: e.target.value }, true)}>
            ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
          </${Select}></label>
        ${draft.mode === "search" && html`<label title="Сколько ответов пузырь держится после того, как тема всплыла">
          Держится <input class="num" inputmode="numeric" pattern="[0-9]*" value=${String(draft.sticky)}
            onInput=${(e) => set({ sticky: digitsOf(e, 1000) ?? 0 })} /></label>`}
        ${draft.mode === "search" && html`<label title="С каким шансом найденный пузырь правда уйдёт модели. Не выпал — пузырь не придёт, пока держится">
          Шанс <input class="num" inputmode="numeric" pattern="[0-9]*" value=${String(draft.chance ?? 100)}
            aria-label="Шанс, процентов" onInput=${(e) => set({ chance: digitsOf(e, 100) ?? 0 })} />%</label>`}
        ${draft.mode === "always" && html`<label title="Сколько ответов пропускать между отправками: 0 — на каждый ответ">
          Через <input class="num" inputmode="numeric" pattern="[0-9]*" value=${String(draft.every ?? 0)}
            aria-label="Через сколько ответов" onInput=${(e) => set({ every: digitsOf(e, 1000) ?? 0 })} /> отв.</label>`}
        <label title="Как показывать подпузыри на карте">Подпузыри на карте
          <${Select} value=${draft.layout.display || "line"}
            onChange=${(e) => set({ layout: { ...draft.layout, display: e.target.value } }, true)}>
            <option value="line">линиями</option><option value="nested">внутри пузыря</option>
          </${Select}></label>
        <label title="Спрятать подпузыри на карте (на отправку модели не влияет)">
          <input type="checkbox" checked=${Boolean(draft.layout.collapsed)}
            onChange=${(e) => set({ layout: { ...draft.layout, collapsed: e.target.checked } }, true)} /> скрыть подпузыри на карте</label>
      </div>
      <span class="hint">Глубина: за сколько сообщений до конца чата вставить пузырь; пусто — рядом с карточкой.${draft.mode === "search"
        ? " Держится: сколько ответов пузырь остаётся после того, как тема всплыла. Шанс: с какой вероятностью найденный пузырь уйдёт модели."
        : ""}${draft.mode === "always" ? " Через: сколько ответов пропускать между отправками, 0 — на каждый ответ." : ""}</span>
      <${MoreRules} key=${block.id} rules=${draft.rules || {}}
        onRules=${(patch, now = true) => {
          const next = { ...(draft.rules || {}), ...patch };
          for (const k of Object.keys(next)) if (next[k] === null) delete next[k];
          set({ rules: next }, now);
        }} />
      ${links.length > 0 && html`<div class="stack tree-links">
        <span class="muted">Ссылки: придут вместе с этим пузырём</span>
        ${links.map((l) => html`<span key=${l.id} class="chip"><${Icon} name="link" size=${14} /> ${l.path}
          <button type="button" aria-label="Убрать ссылку" title="Убрать ссылку"
            onClick=${() => onUnlink(block.id, l.id)}><${Icon} name="x" size=${14} /></button></span>`)}
      </div>`}
      ${hasKids && html`<div class="row">
        <label title="Один шанс для всех подпузырей внутри этого пузыря; другие ветки не меняются">Шанс всем подпузырям
          <input class="num" inputmode="numeric" pattern="[0-9]*" placeholder="%" value=${below === null ? "" : String(below)}
            onInput=${(e) => setBelow(digitsOf(e, 100))} /></label>
        <button disabled=${below === null} onClick=${() => { onChanceBelow(block.id, below); setBelow(null); }}>Применить</button>
      </div>`}
      <div class="row">
        <span class=${"save-state " + state} aria-live="polite">${state === "saved"
          ? html`<${Icon} name="check" size=${14} /> Сохранено`
          : state === "failed" ? "Не сохранено" : "Сохраняю…"}</span>
        ${state === "failed" && html`<button type="button" onClick=${flush}>Повторить</button>`}
        <button onClick=${() => onAddChild(block.id)}><${Icon} name="plus" /> Подпузырь</button>
        ${hasKids && html`<button title="Открыть пространство этого пузыря (двойное нажатие на пузырь)"
          onClick=${() => onOpen(block.id)}><${Icon} name="maximize-2" /> Открыть внутри</button>`}
        ${order && html`<label title=${"Номер среди соседей (1–" + order.of + "): модель читает их по порядку. Занятый номер — остальные сдвинутся"}>Порядок
          <input class="num" inputmode="numeric" pattern="[0-9]*" aria-label="Порядковый номер"
            value=${place === null ? String(order.n) : place}
            onInput=${(e) => setPlace(e.target.value.replace(/\D/g, "").slice(0, 5))}
            onKeyDown=${(e) => e.key === "Enter" && e.target.blur()}
            onBlur=${() => { const n = Number(place); if (place && n !== order.n) onOrder(block, n); else setPlace(null); }} /></label>`}
        ${block.parent_id && html`<${IconButton} icon="corner-left-up" label="Вынести на уровень выше" onClick=${() => onMove(block)} />`}
        ${!block.detached && html`<button title="Отцепить: пузырь повиснет свободно и перестанет уходить модели"
          onClick=${() => onDetach(block)}><${Icon} name="unlink" /> Отцепить</button>`}
        <button title="Связать ссылкой с пузырём из другой группы: придут вместе" onClick=${() => onStartLink(block)}><${Icon} name="link" /> Ссылка</button>
        <span class="spacer"></span>
        <button class="danger" onClick=${remove}>Удалить</button>
      </div>
    </aside>`;
}

const ROW_H = 38; // list view: every row is one line of this height, so only rows in view are drawn

// List view: the tree as rows; a branch opens and closes with its arrow (only the top level is open at
// first). Thousands of rows stay smooth: only the rows in view (plus a margin) are in the page.
function TreeList({ kids, selected, vectorized, open, loaded, error, onRetry, onToggle, onSelect }) {
  const box = useRef(null);
  const [view, setView] = useState({ top: 0, height: 800 });
  const rows = useMemo(() => {
    const out = [];
    const walk = (parent, level) => {
      for (const b of kids.get(parent) || []) {
        const count = (kids.get(b.id) || []).length;
        out.push({ b, level, count });
        if (count && open.has(b.id)) walk(b.id, level + 1);
      }
    };
    walk(null, 0);
    return out;
  }, [kids, open]);
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    setView({ top: el.scrollTop, height: el.clientHeight });
    const observer = new ResizeObserver(() => setView({ top: el.scrollTop, height: el.clientHeight }));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  // The picked bubble (e.g. picked on the map) is scrolled into view.
  useEffect(() => {
    const el = box.current;
    const index = rows.findIndex((r) => r.b.id === selected);
    if (!el || index < 0) return;
    const y = index * ROW_H;
    if (y < el.scrollTop || y + ROW_H > el.scrollTop + el.clientHeight) el.scrollTop = Math.max(0, y - el.clientHeight / 3);
  }, [selected]);
  if (!loaded) return html`<div class="tree-list"><${Pending} error=${error} onRetry=${onRetry} /></div>`;
  if (!rows.length) return html`<div class="tree-list"><${EmptyNote} title="Пузырей пока нет" hint="Добавьте первый кнопкой «Пузырь» вверху" /></div>`;
  const first = Math.max(0, Math.floor(view.top / ROW_H) - 10);
  const last = Math.min(rows.length, Math.ceil((view.top + view.height) / ROW_H) + 10);
  return html`<div class="tree-list" data-slot="tree-list" ref=${box}
      onScroll=${(e) => setView({ top: e.currentTarget.scrollTop, height: e.currentTarget.clientHeight })}>
    <ul class="list" style=${`height:${rows.length * ROW_H}px`}>
      ${rows.slice(first, last).map(({ b, level, count }, i) => html`<li key=${b.id}
        class=${"tree-row" + (selected === b.id ? " active" : "")}
        style=${`top:${(first + i) * ROW_H}px;padding-left:${level * 18}px`}>
        ${count
          ? html`<button class="fold" aria-expanded=${open.has(b.id)} onClick=${() => onToggle(b.id)}
              aria-label=${open.has(b.id) ? "Скрыть подпузыри" : "Показать подпузыри"}
              title=${open.has(b.id) ? "Скрыть" : "Показать"}><${Icon} name=${open.has(b.id) ? "chevron-down" : "chevron-right"} /></button>`
          : html`<span class="fold-gap"></span>`}
        <button class="item" onClick=${() => onSelect(b.id)}><${Ring} mode=${b.mode} /> ${b.title}
          ${vectorized.has(b.id) ? html`<span class="muted" title="векторизован"> <${Icon} name="waypoints" size=${12} /></span>` : ""}
          ${count ? html`<span class="muted"> (${count})</span>` : ""}</button>
      </li>`)}
    </ul>
  </div>`;
}

// `character` is the owner of the tree: a character, or a lorebook given as {id, name, book: true}
// (its bubbles live under /lorebooks/{id}; the root is the book itself).
// The side panel folds to a thin strip (just its header on a phone), like the plugins' side panels. The choice is
// kept per device. It lives in the panel's own component, so folding never re-renders the map and its bubbles.
const PANEL_KEY = "tree-panel";
function useFoldPanel() {
  useLayout();
  const folded = panelFolded(PANEL_KEY);
  const button = html`<${IconButton} icon=${folded ? "chevron-left" : "chevron-right"} class="fold-panel" data-slot="fold-panel"
    label=${folded ? "Развернуть панель" : "Свернуть панель"} aria-expanded=${!folded}
    onClick=${() => setPanelFolded(PANEL_KEY, !folded)} />`;
  return { folded, button };
}

function RootPanel({ character, onClose, onCreate }) {
  const { folded, button } = useFoldPanel();
  return html`<aside class=${"tree-panel" + (folded ? " folded" : "")} data-slot="tree-panel" data-folded=${folded ? "true" : "false"}>
    <div class="row">${button}<strong>${character.name}</strong><span class="spacer"></span>
      <${IconButton} icon="x" label="Закрыть" onClick=${onClose} /></div>
    <p class="muted">${character.book
      ? "Это сам лорбук. Название и описание меняются наверху окна; модели уходят только его записи."
      : "Это сама карточка — она уходит модели как обычно. Её текст меняется на вкладке «Карточка»."}</p>
    <button onClick=${() => onCreate(null)}><${Icon} name="plus" /> Пузырь в корень</button>
  </aside>`;
}

export function CharacterTree({ character, settings, revision = 0, onError }) {
  const base = character.book ? `/lorebooks/${character.id}` : `/characters/${character.id}`;
  // Good news ("done, 3 bubbles") is a quiet toast, not the red error one and not a line that pushes the map.
  const onNotice = (text, kind = "ok") => notify(text, kind);
  const [blocks, setBlocks] = useState([]);
  const [loaded, setLoaded] = useState(false); // the first answer has come: before it, no «empty» hints
  const [loadError, setLoadError] = useState("");
  const [vecBusy, setVecBusy] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false); // phone: legend and «Шанс всем» folded in the toolbar
  const [vectorized, setVectorized] = useState(new Set());
  const [selected, setSelected] = useState(null);
  const [view, setView] = useState(() => { try { return localStorage.getItem("tavern.treeView") || "map"; } catch (e) { return "map"; } });
  const [camera, setCamera] = useState({ x: 40, y: 200, s: 1 });
  const [drag, setDrag] = useState(null); // {ids, dx, dy, target}
  const [links, setLinks] = useState([]); // [[a, b]] linked bubbles
  const [wire, setWire] = useState(null); // a line being pulled from a port: {from, port, x, y, target}
  const [linking, setLinking] = useState(null); // bubble id waiting for "the other end" of a link
  const [chanceAll, setChanceAll] = useState(null);
  // Opened bubbles, innermost last: [{id, cam}] where cam is the camera of the space underneath.
  const [spaces, setSpaces] = useState([]);
  const [nestOnDrop, setNestOnDrop] = useState(() => {
    try { return localStorage.getItem("tavern.treeNest") !== "0"; } catch (e) { return true; }
  });
  const [size, setSize] = useState({ w: 800, h: 600 });
  const viewport = useRef(null);
  const world = useRef(null);
  const cam = useRef(camera);
  const gesture = useRef(null);
  const frame = useRef(0);
  const settle = useRef(0); // timer: the culling catches up a moment after the camera stops
  const calm = useRef(0); // timer: dashes on the link arcs come back a moment after the map stops moving
  const drawn = useRef(camera); // the camera the bubbles in the page were last chosen for
  const dragRef = useRef(null);
  const dragFrame = useRef(0);

  const reload = useCallback(() => api.get(`${base}/blocks`).then((d) => {
    setBlocks(d.blocks);
    setVectorized(new Set(d.vectorized));
    setLinks(d.links || []);
    setLoaded(true);
    setLoadError("");
  }).catch((e) => { setLoadError(e.message); onError(e.message); }), [character.id]);
  useEffect(() => { setLoaded(false); }, [character.id]);
  useEffect(() => { reload(); }, [reload, revision]);
  useEffect(() => { if (revision) setSelected(null); }, [revision]); // after undo the open panel would show the old text
  useEffect(() => { try { localStorage.setItem("tavern.treeView", view); } catch (e) { /* storage off */ } }, [view]);
  useEffect(() => { try { localStorage.setItem("tavern.treeNest", nestOnDrop ? "1" : "0"); } catch (e) { /* storage off */ } }, [nestOnDrop]);
  useEffect(() => setSpaces([]), [character.id]);

  const { pos, kids, byId } = useMemo(() => layoutTree(blocks), [blocks]);
  // List view: which branches are open, remembered per character / book in this browser.
  const openKey = `tavern.treeOpen.${character.id}`;
  const [openIds, setOpenIds] = useState(new Set());
  useEffect(() => {
    try { setOpenIds(new Set(JSON.parse(localStorage.getItem(openKey) || "[]"))); } catch (e) { setOpenIds(new Set()); }
  }, [openKey]);
  const keepOpen = (next) => {
    setOpenIds(next);
    try { localStorage.setItem(openKey, JSON.stringify([...next])); } catch (e) { /* storage off */ }
  };
  const openRows = (ids) => {
    const missing = ids.filter((id) => !openIds.has(id));
    if (missing.length) keepOpen(new Set([...openIds, ...missing]));
  };
  const toggleRow = (id) => {
    const next = new Set(openIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    keepOpen(next);
  };
  // The path to the picked bubble is always open, so the list can show it.
  useEffect(() => {
    if (!selected || selected === "root" || !byId.has(selected)) return;
    const up = [];
    for (let b = byId.get(byId.get(selected).parent_id); b; b = byId.get(b.parent_id)) up.push(b.id);
    openRows(up);
  }, [selected, byId]);
  const depthOf = (id) => 1 + Math.max(0, ...(kids.get(id) || []).map((c) => depthOf(c.id)));
  // Free bubbles (joined to nothing) and everything under them: pale, not sent.
  const inactive = useMemo(() => {
    const out = new Set();
    for (const b of kids.get(null) || []) {
      if (b.detached) [b.id, ...descendants(kids, b.id)].forEach((id) => out.add(id));
    }
    return out;
  }, [kids]);
  // A bubble's number among its neighbours (the order the model reads them in); free bubbles have none.
  const orderOf = useMemo(() => {
    const out = new Map();
    for (const row of kids.values()) {
      const numbered = row.filter((b) => !b.detached);
      numbered.forEach((b, i) => out.set(b.id, { n: i + 1, of: numbered.length }));
    }
    return out;
  }, [kids]);

  // Centre the root on open and follow the map's size.
  useEffect(() => {
    if (view !== "map" || !viewport.current) return undefined;
    const box = viewport.current.getBoundingClientRect();
    setSize({ w: box.width, h: box.height });
    const start = { x: 24, y: box.height / 2 - H / 2 - (pos.get("root")?.y || 0), s: 1 };
    cam.current = start;
    drawn.current = start;
    setCamera(start);
    const observer = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    observer.observe(viewport.current);
    return () => observer.disconnect();
  }, [view, character.id, blocks.length === 0]);

  // Camera changes go straight to the layer (one transform, no re-render). Which bubbles are in the
  // page (culling) is chosen again only when the camera has gone a good part of the margin away from
  // where they were chosen, or a moment after it stops: re-drawing hundreds of bubbles on every
  // pointer move is what made panning a big zoomed-out tree lag.
  const commitCamera = () => {
    clearTimeout(settle.current);
    cancelAnimationFrame(frame.current);
    frame.current = 0;
    drawn.current = { ...cam.current };
    setCamera(drawn.current);
  };
  // While the map moves the link arcs are drawn solid (a dash pattern on long arcs is what made panning and
  // zooming lag with many links); the class comes off 200 ms after the last move.
  const markMoving = () => {
    const el = viewport.current;
    if (!el) return;
    el.classList.add("moving");
    clearTimeout(calm.current);
    calm.current = setTimeout(() => el.classList.remove("moving"), 200);
  };
  const applyCamera = (next) => {
    markMoving();
    cam.current = next;
    if (world.current) world.current.style.transform = `translate(${next.x}px, ${next.y}px) scale(${next.s})`;
    const d = drawn.current;
    const far = Math.abs(next.x - d.x) > MOVE_REDRAW || Math.abs(next.y - d.y) > MOVE_REDRAW
      || Math.abs(Math.log(next.s / d.s)) > 0.12;
    clearTimeout(settle.current);
    if (far) { if (!frame.current) frame.current = requestAnimationFrame(commitCamera); }
    else settle.current = setTimeout(commitCamera, 120);
  };
  useEffect(() => () => { clearTimeout(settle.current); clearTimeout(calm.current); cancelAnimationFrame(frame.current); }, []);
  const zoomAt = (px, py, factor) => {
    const c = cam.current;
    const s = Math.min(2.5, Math.max(0.12, c.s * factor));
    applyCamera({ s, x: px - ((px - c.x) * s) / c.s, y: py - ((py - c.y) * s) / c.s });
  };

  useEffect(() => {
    const el = viewport.current;
    if (!el) return undefined;
    const wheel = (e) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      if (e.ctrlKey || Math.abs(e.deltaY) >= Math.abs(e.deltaX)) zoomAt(e.clientX - box.left, e.clientY - box.top, Math.exp(-e.deltaY * 0.0015));
      else applyCamera({ ...cam.current, x: cam.current.x - e.deltaX });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, [view]);

  const dropTarget = (clientX, clientY, exclude) => {
    for (const el of document.elementsFromPoint(clientX, clientY)) {
      const id = el.closest && el.closest("[data-bid]")?.dataset.bid;
      if (id && !exclude.has(id)) return id;
    }
    return null;
  };

  const toWorld = (clientX, clientY) => {
    const box = viewport.current.getBoundingClientRect();
    const c = cam.current;
    return { x: (clientX - box.left - c.x) / c.s, y: (clientY - box.top - c.y) / c.s };
  };

  const onPointerDown = (e) => {
    if (e.button > 0) return;
    viewport.current.setPointerCapture(e.pointerId);
    const g = gesture.current || { pointers: new Map() };
    g.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (g.pointers.size === 1) {
      g.bid = e.target.closest("[data-bid]")?.dataset.bid || null;
      g.port = e.target.closest("[data-port]")?.dataset.port || null;
      g.mode = g.port && g.bid ? "wire" : g.bid && g.bid !== "root" ? "press" : "pan";
      g.start = { x: e.clientX, y: e.clientY, cam: { ...cam.current } };
    } else if (g.pointers.size === 2) {
      const [a, b] = [...g.pointers.values()];
      g.mode = "pinch";
      g.pinch = { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, s: cam.current.s };
      dragRef.current = null;
      setDrag(null);
    }
    gesture.current = g;
  };

  const onPointerMove = (e) => {
    const g = gesture.current;
    if (!g || !g.pointers.has(e.pointerId)) return;
    g.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (g.mode === "pinch" && g.pointers.size === 2) {
      const box = viewport.current.getBoundingClientRect();
      const [a, b] = [...g.pointers.values()];
      const factor = (g.pinch.s * Math.hypot(a.x - b.x, a.y - b.y)) / g.pinch.dist / cam.current.s;
      zoomAt((a.x + b.x) / 2 - box.left, (a.y + b.y) / 2 - box.top, factor);
      return;
    }
    const dx = e.clientX - g.start.x;
    const dy = e.clientY - g.start.y;
    if (g.mode === "wire") {
      const w = toWorld(e.clientX, e.clientY);
      dragRef.current = { from: g.bid, port: g.port, ...w, target: dropTarget(e.clientX, e.clientY, new Set([g.bid])) };
      if (!dragFrame.current) dragFrame.current = requestAnimationFrame(() => { dragFrame.current = 0; setWire(dragRef.current); });
    } else if (g.mode === "pan") {
      applyCamera({ ...cam.current, x: g.start.cam.x + dx, y: g.start.cam.y + dy });
    } else if ((g.mode === "press" && Math.hypot(dx, dy) > 6) || g.mode === "drag") {
      g.mode = "drag";
      markMoving();
      const ids = g.ids || (g.ids = new Set([g.bid, ...descendants(kids, g.bid)]));
      const s = cam.current.s;
      dragRef.current = { ids, dx: dx / s, dy: dy / s, target: nestOnDrop ? dropTarget(e.clientX, e.clientY, ids) : null };
      if (!dragFrame.current) dragFrame.current = requestAnimationFrame(() => { dragFrame.current = 0; setDrag(dragRef.current); });
    }
  };

  const onPointerUp = async (e) => {
    const g = gesture.current;
    if (!g) return;
    g.pointers.delete(e.pointerId);
    if (g.pointers.size > 0) { if (g.mode === "pinch") g.mode = "done"; return; }
    gesture.current = null;
    const c0 = cam.current; const d0 = drawn.current;
    if (c0.x !== d0.x || c0.y !== d0.y || c0.s !== d0.s) commitCamera();
    const still = Math.hypot(e.clientX - g.start.x, e.clientY - g.start.y) < 6;
    if (g.mode === "wire") {
      const current = dragRef.current;
      dragRef.current = null;
      setWire(null);
      if (current && !still) finishWire(current);
      return;
    }
    if (g.mode === "press" || (g.mode === "pan" && still)) {
      if (linking && g.bid && g.bid !== "root" && g.bid !== linking) {
        addLink(linking, g.bid);
        return;
      }
      setLinking(null);
      setSelected(g.bid || null);
      return;
    }
    if (g.mode !== "drag") return;
    const current = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!current) return;
    // Dropped on a bubble: it moves inside and takes its place in that group's layout.
    // Dropped on empty space: it stays where it was let go.
    const moved = current.target
      ? [...current.ids].map((id) => ({ id, auto: true }))
      : [...current.ids].map((id) => ({ id, x: pos.get(id).x + current.dx, y: pos.get(id).y + current.dy }));
    const parent = current.target === "root" ? null : current.target;
    const position = current.target ? (kids.get(parent) || []).length : 0;
    // A free bubble dropped on a bubble is joined again.
    // Show the result at once, in the same frame the drag ends: waiting for the server first made
    // the bubble flash back to where it came from.
    const at = new Map(moved.map((m) => [m.id, m]));
    setBlocks((list) => list.map((b) => {
      const m = at.get(b.id);
      if (!m) return b;
      const { x, y, ...rest } = b.layout || {};
      const layout = m.auto ? rest : { ...rest, x: m.x, y: m.y };
      return current.target && b.id === g.bid ? { ...b, parent_id: parent, position, layout, detached: false } : { ...b, layout };
    }));
    try {
      if (current.target) await api.put(`/blocks/${g.bid}`, { parent_id: parent, position });
      await api.put(`${base}/blocks/layout`, { items: moved });
    } catch (err) { onError(err.message); reload(); }
  };

  // "Открыть внутри": open a bubble as its own space; the one underneath stays behind, blurred.
  const openSpace = (id) => {
    if (!id || id === "root" || !(kids.get(id) || []).length) return;
    setSelected(null);
    setSpaces((list) => [...list, { id, cam: { ...cam.current } }]);
    const box = viewport.current ? viewport.current.getBoundingClientRect() : { height: size.h };
    const p = pos.get(id);
    applyCamera({ x: 24 - p.x, y: box.height / 2 - H / 2 - p.y, s: 1 });
    commitCamera();
  };
  const closeSpaces = (keep) => {
    setSelected(null);
    setSpaces((list) => {
      if (keep >= list.length) return list;
      applyCamera(list[keep].cam);
      commitCamera();
      return list.slice(0, keep);
    });
  };
  useEffect(() => {
    if (!spaces.length) return undefined;
    // capture + preventDefault: this Esc closes the space, not the window around the tree
    const key = (e) => { if (e.key === "Escape" && !linking) { e.preventDefault(); closeSpaces(spaces.length - 1); } };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [spaces, linking]);

  // Hang `child` under `parent` (null = the card) or set it free; the map shows it at once.
  const rehang = async (childId, parentId, free = false) => {
    const subtree = [childId, ...descendants(kids, childId)];
    const position = (kids.get(parentId) || []).length;
    setBlocks((list) => list.map((b) => {
      if (b.id === childId) {
        const layout = free ? b.layout : (({ x, y, ...rest }) => rest)(b.layout || {});
        return { ...b, parent_id: parentId, position, detached: free, layout };
      }
      if (!free && subtree.includes(b.id)) return { ...b, layout: (({ x, y, ...rest }) => rest)(b.layout || {}) };
      return b;
    }));
    try {
      await api.put(`/blocks/${childId}`, free ? { parent_id: null, detached: true } : { parent_id: parentId, position, detached: false });
      if (!free) await api.put(`${base}/blocks/layout`, { items: subtree.map((id) => ({ id, auto: true })) });
      else {
        const p = pos.get(childId);
        await api.put(`${base}/blocks/layout`, { items: [{ id: childId, x: p.x, y: p.y }] });
      }
    } catch (err) { onError(err.message); reload(); }
  };
  // A line pulled from a port and let go on another bubble links the two; anywhere else, nothing.
  // Moving a bubble to another place is done by dragging the bubble itself.
  const finishWire = (w) => {
    if (w.target && w.target !== "root" && w.from !== "root") addLink(w.from, w.target);
  };
  const addLink = async (a, b) => {
    setLinking(null);
    try { setLinks((await api.post(`${base}/links`, { a, b })).links); } catch (err) { onError(err.message); }
  };
  const unlink = async (a, b) => {
    try {
      const done = await api.del(`${base}/links?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
      setLinks(done.links);
    } catch (err) { onError(err.message); }
  };
  const setChance = async (chance, under = null) => {
    try {
      const { changed } = await api.put(`${base}/blocks/chance`, { chance, under });
      onNotice(`Шанс ${chance}% у пузырей: ${changed}`);
      await reload();
    } catch (err) { onError(err.message); }
  };
  useEffect(() => {
    if (!linking) return undefined;
    const key = (e) => { if (e.key === "Escape") { e.preventDefault(); setLinking(null); } };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [linking]);

  // A new bubble opens in the panel only when the setting asks for it (Settings → "Основное");
  // otherwise the current one stays open and a calm line says where the new one went.
  const create = async (parentId) => {
    const parent = parentId && parentId !== "root" ? parentId : null;
    try {
      const block = await api.post(`${base}/blocks`, { title: "Новый пузырь", mode: "search", parent_id: parent });
      await reload();
      if (settings.open_new_bubble) setSelected(block.id);
      else {
        if (parent) openRows([parent]);
        onNotice(parent ? `Новый подпузырь в «${byId.get(parent)?.title}»` : "Пузырь добавлен");
      }
    } catch (e) { onError(e.message); }
  };
  // Resolves true when saved, false when the server refused (the panel then says so and keeps the draft).
  const save = async (id, changes) => {
    try {
      const fresh = await api.put(`/blocks/${id}`, changes);
      setBlocks((list) => list.map((b) => (b.id === id ? fresh : b)));
      if (changes.content !== undefined) setVectorized((v) => { const n = new Set(v); n.delete(id); return n; });
      return true;
    } catch (e) { onError(e.message); return false; }
  };
  const remove = async (block) => {
    try { await api.del(`/blocks/${block.id}`); setSelected(null); await reload(); } catch (e) { onError(e.message); }
  };
  // One level up.
  const moveUp = async (block) => {
    try {
      const parent = byId.get(block.parent_id);
      await api.put(`/blocks/${block.id}`, { parent_id: parent ? parent.parent_id : null });
      await api.put(`${base}/blocks/layout`, {
        items: [block.id, ...descendants(kids, block.id)].map((id) => ({ id, auto: true })) });
      await reload();
    } catch (e) { onError(e.message); }
  };
  const setOrder = async (block, place) => {
    try { await api.put(`/blocks/${block.id}/order`, { place }); await reload(); } catch (e) { onError(e.message); }
  };
  const vectorize = async () => {
    setVecBusy(true);
    try {
      const { made } = await api.post(`${base}/vectorize`);
      if (made) onNotice(`Векторизовано пузырей: ${made}`); else onNotice("Все зелёные пузыри уже векторизованы", "info");
      reload();
    } catch (e) { onError(e.message); } finally { setVecBusy(false); }
  };

  // What to draw in one space (the whole map, or one bubble opened with "Открыть внутри"): bubbles in view (plus
  // a margin), nested-group spaces, lines by colour, link arcs. `scope` null = the card is the root.
  const scene = (scope, view_, dragState) => {
    const at = (id) => {
      const p = pos.get(id);
      return dragState && dragState.ids.has(id) ? { x: p.x + dragState.dx, y: p.y + dragState.dy } : p;
    };
    const margin = MARGIN_PX / view_.s;
    const left = -view_.x / view_.s - margin;
    const top = -view_.y / view_.s - margin;
    const right = left + size.w / view_.s + 2 * margin;
    const bottom = top + size.h / view_.s + 2 * margin;
    const inView = (p, w = W, h = H) => p.x + w >= left && p.x <= right && p.y + h >= top && p.y <= bottom;
    const nodes = [];
    const groups = [];
    const lines = { c100: "", c75: "", c50: "", c25: "", c0: "", off: "" };
    const line = (p, q, child) => { lines[inactive.has(child.id) ? "off" : band(child)] += curve(p, q); };
    const rootId = scope || "root";
    const root = pos.get(rootId);
    const inside = new Set(scope ? descendants(kids, scope) : pos.keys());
    for (const b of kids.get(scope) || []) {
      const q = at(b.id);
      if (!b.detached && (inView(root) || inView(q))) line(root, q, b);
    }
    const visit = (parent) => {
      for (const b of kids.get(parent) || []) {
        if (!pos.has(b.id)) continue;
        const p = at(b.id);
        if (inView(p)) nodes.push({ b, p });
        const children = b.layout?.collapsed && !scope ? [] : (kids.get(b.id) || []).filter((c) => pos.has(c.id));
        if (children.length && b.layout?.display === "nested") {
          const all = [b.id, ...descendants(kids, b.id)].filter((id) => pos.has(id)).map(at);
          const x0 = Math.min(...all.map((q) => q.x)); const y0 = Math.min(...all.map((q) => q.y));
          const box = { x: x0 - PAD, y: y0 - PAD,
            w: Math.max(...all.map((q) => q.x)) - x0 + W + 2 * PAD, h: Math.max(...all.map((q) => q.y)) - y0 + H + 2 * PAD };
          if (inView(box, box.w, box.h)) groups.push({ b, box });
        } else {
          for (const c of children) {
            const q = at(c.id);
            if (inView(p) || inView(q)) line(p, q, c);
          }
        }
        if (children.length) visit(b.id);
      }
    };
    visit(scope);
    let refs = "";
    for (const [a, b] of links) {
      if (!inside.has(a) || !inside.has(b) || !pos.has(a) || !pos.has(b)) continue;
      const p = at(a); const q = at(b);
      if (inView(p) || inView(q)) refs += arc(p, q);
    }
    return { nodes, groups, lines, refs, root, rootId };
  };
  const scope = spaces.length ? spaces[spaces.length - 1].id : null;
  const shown = useMemo(() => (view === "map" ? scene(scope, camera, drag)
    : { nodes: [], groups: [], lines: {}, refs: "", root: null }),
  [view, pos, kids, camera, size, drag, links, inactive, scope]);
  // The space underneath an opened bubble, frozen where it was and blurred.
  const behind = useMemo(() => (view === "map" && spaces.length
    ? scene(spaces.length > 1 ? spaces[spaces.length - 2].id : null, spaces[spaces.length - 1].cam, null) : null),
  [view, pos, kids, size, links, inactive, spaces]);

  const selectedBlock = selected && selected !== "root" ? byId.get(selected) : null;
  const pathOf = (block) => {
    const parts = [];
    for (let b = block; b; b = byId.get(b.parent_id)) parts.unshift(b.title);
    return [character.name, ...parts].join(" › ");
  };

  // Keys on the parts below: a hint line appearing must not make Preact rebuild the map (its size
  // watcher would keep watching the old, detached element and the map would draw nothing).
  return html`
    <div class="tree" data-slot="character-tree">
      <div key="toolbar" class="row tree-toolbar">
        <div class="tabs" role="tablist">
          <button role="tab" aria-selected=${view === "map"} onClick=${() => setView("map")}>Карта</button>
          <button role="tab" aria-selected=${view === "list"} onClick=${() => setView("list")}>Список</button>
        </div>
        <button onClick=${() => create(selected)}><${Icon} name="plus" /> Пузырь</button>
        ${view === "list" && html`<button title="Показать все ветки списка"
            onClick=${() => keepOpen(new Set(blocks.filter((b) => (kids.get(b.id) || []).length).map((b) => b.id)))}>Развернуть всё</button>
          <button title="Оставить только верхний уровень" onClick=${() => keepOpen(new Set())}>Свернуть всё</button>`}
        ${settings.embedding_connection && html`<button disabled=${vecBusy} onClick=${vectorize}
          title="Сделать векторы для поиска по смыслу (только текст пузырей)">
          <${Icon} name="waypoints" /> ${vecBusy ? "Векторизую…" : "Векторизовать"}</button>`}
        <button class="tree-tools-toggle" aria-expanded=${toolsOpen} onClick=${() => setToolsOpen(!toolsOpen)}>
          <${Icon} name=${toolsOpen ? "chevron-down" : "chevron-right"} /> Легенда и шанс</button>
        <div class=${"tree-tools" + (toolsOpen ? " open" : "")}>
          <span class="legend muted"><${Ring} mode="always" /> всегда <${Ring} mode="search" /> по теме
            <${Ring} mode="off" /> выключен
            <span class="legend-lines" title="Цвет линии — шанс зелёного пузыря">
              ${CHANCE_BANDS.map(([min, key]) => html`<i key=${key} class=${"swatch line-" + key}></i>`)} шанс 100→0%</span>
            <span title="Ссылка между пузырями разных групп"><i class="swatch tree-refs"></i> ссылка</span></span>
          <label class="chance-all" title="Один шанс для всех пузырей. Отменить можно кнопкой «Отменить» вверху окна">Шанс всем
            <input class="num" inputmode="numeric" pattern="[0-9]*" placeholder="%" value=${chanceAll === null ? "" : String(chanceAll)}
              onInput=${(e) => setChanceAll(digitsOf(e, 100))} />
            <button disabled=${chanceAll === null}
              onClick=${() => { setChance(chanceAll); setChanceAll(null); }}>Применить</button></label>
        </div>
      </div>
      ${linking ? html`<div key="linking" class="tree-hint" role="status"><${Icon} name="link" /> Нажмите на пузырь, с которым связать
        «${byId.get(linking)?.title}» <button onClick=${() => setLinking(null)}>Отмена</button></div>` : null}
      ${view === "map" && html`<div key="crumbs" class="row tree-crumbs" data-slot="tree-crumbs">
        <button class=${"crumb" + (spaces.length ? "" : " current")} onClick=${() => closeSpaces(0)}>${character.name}</button>
        ${spaces.map((sp, i) => html`<span key=${"s" + sp.id} class="muted"><${Icon} name="chevron-right" size=${14} /></span>
          <button key=${sp.id} class=${"crumb" + (i === spaces.length - 1 ? " current" : "")} onClick=${() => closeSpaces(i + 1)}>
            ${byId.get(sp.id)?.title}</button>`)}
        <span class="spacer"></span>
        <label title="Бросить пузырь на другой: положить внутрь. Выключено: пузыри только двигаются">
          <input type="checkbox" checked=${nestOnDrop} onChange=${(e) => setNestOnDrop(e.target.checked)} />
          Класть внутрь при перетаскивании</label>
      </div>`}
      <div key="body" class="tree-body">
        ${view === "map"
          ? html`<div class=${"tree-map" + (spaces.length ? " in-space" : "") + (camera.s < FAR_ZOOM ? " far" : "")} ref=${viewport}
              onPointerDown=${onPointerDown} onPointerMove=${onPointerMove}
              onPointerUp=${onPointerUp} onPointerCancel=${onPointerUp}
              onDblClick=${(e) => openSpace(e.target.closest("[data-bid]")?.dataset.bid)}>
              ${behind && html`<div class="tree-behind" aria-hidden="true">
                <div class="tree-world" style=${`transform: translate(${spaces[spaces.length - 1].cam.x}px, ${spaces[spaces.length - 1].cam.y}px) scale(${spaces[spaces.length - 1].cam.s})`}>
                  <svg class="tree-lines">${Object.entries(behind.lines).map(([key, d]) => d && html`<path key=${key} class=${"line-" + key} d=${d} />`)}</svg>
                  ${behind.root && html`<div class="bubble root" style=${`transform: translate(${behind.root.x}px, ${behind.root.y}px)`}>
                    <span class="bubble-title">${behind.rootId === "root" ? character.name : byId.get(behind.rootId)?.title}</span></div>`}
                  ${behind.nodes.map(({ b, p }) => html`<div key=${b.id} class=${"bubble mode-" + b.mode}
                    style=${`transform: translate(${p.x}px, ${p.y}px)`}><${Ring} mode=${b.mode} /><span class="bubble-title">${b.title}</span></div>`)}
                </div></div>`}
              <div class="tree-world" ref=${world}
                style=${`transform: translate(${cam.current.x}px, ${cam.current.y}px) scale(${cam.current.s})`}>
                ${shown.groups.map(({ b, box }) => html`<div key=${"g" + b.id} class=${"tree-group mode-" + b.mode}
                  style=${`left:${box.x}px;top:${box.y}px;width:${box.w}px;height:${box.h}px`}></div>`)}
                <svg class="tree-lines" aria-hidden="true">
                  <path class="tree-refs" d=${shown.refs} />
                  ${Object.entries(shown.lines).map(([key, d]) => d && html`<path key=${key} class=${"line-" + key} d=${d} />`)}
                  ${wire && html`<path class="tree-wire" d=${wire.port === "out"
                    ? curve(pos.get(wire.from), { x: wire.x, y: wire.y - H / 2 })
                    : curve({ x: wire.x - W, y: wire.y - H / 2 }, pos.get(wire.from))} />`}
                </svg>
                ${shown.root && !scope && html`<div data-bid="root"
                  class=${"bubble root" + (drag && drag.target === "root" ? " target" : "")
                    + (selected === "root" ? " selected" : "")}
                  style=${`transform: translate(${shown.root.x}px, ${shown.root.y}px)`}>
                  ${character.avatar && html`<img src=${`/api/characters/${character.id}/avatar`} alt="" />`}
                  <span class="bubble-title">${character.name}</span></div>`}
                ${shown.root && scope && html`<div data-bid=${scope}
                  class=${"bubble root space-root mode-" + byId.get(scope)?.mode
                    + ((drag && drag.target === scope) || (wire && wire.target === scope) ? " target" : "")
                    + (selected === scope ? " selected" : "")}
                  style=${`transform: translate(${shown.root.x}px, ${shown.root.y}px)`}>
                  <${Ring} mode=${byId.get(scope)?.mode} /><span class="bubble-title">${byId.get(scope)?.title}</span>
                  <span class="bubble-port out" data-port="out" title=${PORT_TITLE}></span></div>`}
                ${shown.nodes.map(({ b, p }) => html`<div key=${b.id} data-bid=${b.id}
                  class=${"bubble mode-" + b.mode + (selected === b.id ? " selected" : "")
                    + (drag && drag.ids.has(b.id) ? " dragging" : "")
                    + ((drag && drag.target === b.id) || (wire && wire.target === b.id) ? " target" : "")
                    + (inactive.has(b.id) ? " inactive" : "") + (linking === b.id ? " linking" : "")
                    + (b.layout?.collapsed && !scope && (kids.get(b.id) || []).length ? ` depth-${Math.min(3, depthOf(b.id))}` : "")}
                  style=${`transform: translate(${p.x}px, ${p.y}px)`}
                  title=${inactive.has(b.id) ? `${b.title} — не прицеплен к карточке, модели не уходит` : b.title}>
                  <span class="bubble-port in" data-port="in" title=${PORT_TITLE}></span>
                  <${Ring} mode=${b.mode} /><span class="bubble-title">${b.title}</span>
                  ${orderOf.has(b.id) && html`<sup class="bubble-order" title="номер среди соседей">${orderOf.get(b.id).n}</sup>`}
                  ${b.mode === "search" && (b.chance ?? 100) < 100 && html`<span class="bubble-chance" title="шанс">${b.chance}%</span>`}
                  ${vectorized.has(b.id) && html`<span class="bubble-vec" title="векторизован"><${Icon} name="waypoints" size=${12} /></span>`}
                  ${(kids.get(b.id) || []).length > 0 && html`<span class="bubble-count"
                    title=${b.layout?.collapsed ? "подпузыри свёрнуты" : "подпузырей"}>${b.layout?.collapsed ? "+" : ""}${(kids.get(b.id) || []).length}</span>`}

                  <span class="bubble-port out" data-port="out" title=${PORT_TITLE}></span>
                </div>`)}
              </div>
              ${!loaded && html`<${Pending} error=${loadError} onRetry=${reload} />`}
              ${loaded && blocks.length === 0 && html`<p class="tree-empty muted">Нажмите «Пузырь» вверху, чтобы добавить первый пузырь:
                например «Внешность», а в него «Волосы»</p>`}
            </div>`
          : html`<${TreeList} kids=${kids} selected=${selected} vectorized=${vectorized} open=${openIds}
              loaded=${loaded} error=${loadError} onRetry=${reload}
              onToggle=${toggleRow} onSelect=${setSelected} />`}
        ${selectedBlock && html`<${BlockPanel} block=${selectedBlock} path=${pathOf(selectedBlock)}
          vectorized=${vectorized.has(selectedBlock.id)} askDelete=${!settings.skip_delete_confirm}
          hasKids=${(kids.get(selectedBlock.id) || []).length > 0}
          links=${links.filter((l) => l.includes(selectedBlock.id)).map((l) => l[0] === selectedBlock.id ? l[1] : l[0])
            .filter((id) => byId.has(id)).map((id) => ({ id, path: pathOf(byId.get(id)) }))}
          onDetach=${(b) => rehang(b.id, null, true)} onStartLink=${(b) => { setLinking(b.id); setSelected(null); }}
          onOpen=${openSpace}
          onUnlink=${unlink} onChanceBelow=${(id, chance) => setChance(chance, id)}
          onSave=${save} onDelete=${remove} onAddChild=${create} order=${orderOf.get(selectedBlock.id)}
          onMove=${moveUp} onOrder=${setOrder} onClose=${() => setSelected(null)} onError=${onError} onNotice=${onNotice} />`}
        ${selected === "root" && html`<${RootPanel} character=${character} onClose=${() => setSelected(null)} onCreate=${create} />`}
      </div>
    </div>`;
}
