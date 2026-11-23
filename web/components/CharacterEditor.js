// Character editor: the card (create or change it) and, for an existing character, its tree of
// bubbles (tab "Дерево", see CharacterTree.js). The user's own character is an ordinary
// character too; "Мой персонаж по умолчанию" makes it join every new chat as "me".
// Unknown card fields are kept: the editor sends back the whole card with only these fields changed.
// The card saves itself (owner, 2026-11-22, UI_MAP §9а-8): a short pause after typing, and at once when the window
// closes; a new card is created as soon as it has a name. No «Сохранить / Отмена»: a change is taken back with
// "Отменить" (or Ctrl+Z outside text fields), as far back as you like, or a version from "История".
// Closing the editor ends the current version.
import { html, useState, useEffect, useMemo, useRef } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { CharacterTree } from "./CharacterTree.js";
import { BookPicker } from "./Lorebooks.js";
import { Icon, IconButton } from "./Icon.js";
import { RegexOwner } from "./Regex.js";
import { ask } from "./Dialogs.js";
import { EmptyNote, Pending, useLoad } from "./State.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";

// Versions of a character or a lorebook; `base` is its API path ("/characters/ID", "/lorebooks/ID").
// It floats over the window (CSS .history-pop) instead of pushing it: the window is centred, so a panel
// in the flow made everything jump when it opened, loaded or turned out empty.
export function HistoryPanel({ base, revision, onRestored, onError }) {
  const { data, error, reload } = useLoad(() => api.get(`${base}/history`), [base, revision]);
  const restore = async (version) => {
    if (!await ask("Вернуть к этой версии?", { ok: "Вернуть", danger: true,
      hint: "Все изменения, сделанные после неё, будут отменены. Вы сможете вернуть их, только создав заново." })) return;
    try {
      const { undone } = await api.post(`${base}/history/${version.id}/restore`);
      onRestored(`Возвращено к версии, отменено шагов: ${undone}`);
    } catch (e) { onError(e.message); }
  };
  const frame = (body) => html`<div class="history history-pop" data-slot="character-history" role="region"
    aria-label="История версий">${body}</div>`;
  if (!data) return frame(html`<${Pending} error=${error} onRetry=${reload} />`);
  const when = (at) => new Date(at).toLocaleString("ru-RU");
  if (data.versions.length === 0) return frame(html`<${EmptyNote}
    title=${data.enabled ? "Изменений пока нет" : "История версий выключена"}
    hint=${data.enabled ? "Здесь появится журнал правок: что было изменено и возможность вернуть любую прошлую версию." : "Включить ведение истории можно в общих настройках приложения."} />`);
  return frame(html`
    ${data.versions.map((v, i) => html`<details key=${v.id} open=${i === 0} class="history-version">
      <summary><strong>${i === 0 && !v.closed_at ? "Сейчас" : when(v.closed_at || v.started_at)}</strong>
        <span class="muted"> · шагов: ${v.steps.length}</span>
        ${i > 0 && html`<button onClick=${(e) => { e.preventDefault(); restore(v); }}>Вернуть эту версию</button>`}</summary>
      <ol class="history-steps">
        ${v.steps.map((s) => html`<li key=${s.id} class=${s.undone ? "undone" : ""}>
          <span class="muted">${new Date(s.at).toLocaleTimeString("ru-RU")}</span> ${s.what}${s.undone ? " (отменено)" : ""}</li>`)}
      </ol>
    </details>`)}
    <p class="muted">Объём истории: ${(data.bytes / 1024).toFixed(1)} КБ</p>
  `);
}

const FIELDS = [
  ["description", "Описание", 8, "Внешность, характер, биография, манеры речи и ключевые черты персонажа"],
  ["first_mes", "Первое сообщение", 4, "Первая реплика или действие, с которого персонаж начинает новый диалог"],
];
// Fields of imported cards (SillyTavern habit). The card does not ask for them, but they stay saved, still
// go to the model and export; they are shown, folded, only for a card that has any of them filled.
const IMPORTED_FIELDS = [
  ["personality", "Характер", 3],
  ["scenario", "Сценарий", 3],
  ["mes_example", "Примеры диалога", 4],
  ["system_prompt", "Свой главный промпт", 3, "Заменяет главный промпт пресета; {{original}} вставляет его на это место"],
  ["post_history_instructions", "Инструкция после истории", 3],
];
const hasImported = (card) => Boolean(card) && IMPORTED_FIELDS.some(([key]) => String(card[key] || "").trim() !== "");

const SAVE_PAUSE_MS = 1200; // after the last key press

export function CharacterEditor({ characterId, settings, initialTab, onClose, onSaved, onError }) {
  // id: a new card gets one when it is first saved; from then on it is edited like any other
  const [id, setId] = useState(characterId || null);
  const [card, setCard] = useState(characterId ? null : { name: "" });
  const [baseline, setBaseline] = useState(characterId ? "" : JSON.stringify({ name: "" })); // the card as last loaded or saved
  const [isDefaultMe, setDefaultMe] = useState(Boolean(characterId) && settings.default_me === characterId);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState(initialTab || "card");
  const [hasAvatar, setHasAvatar] = useState(false);
  const [revision, setRevision] = useState(0); // bumps after undo / restore: everything reloads
  const [showHistory, setShowHistory] = useState(false);
  const savedCard = useMemo(() => { try { return JSON.parse(baseline); } catch (e) { return null; } }, [baseline]);
  const dirty = card !== null && JSON.stringify(card) !== baseline;
  const noName = card !== null && !String(card.name || "").trim();
  // what a save reads: the newest values, not the ones of the render that scheduled it
  const now = useRef({});
  now.current = { id, card, baseline, isDefaultMe };
  const timer = useRef(null);
  const chain = useRef(Promise.resolve()); // saves run one after another

  useEffect(() => {
    const target = now.current.id; // also a card created in this window (after an undo or a restore)
    if (!target) return;
    api.get(`/characters/${target}`).then((c) => {
      setCard(c.data); setBaseline(JSON.stringify(c.data)); setHasAvatar(Boolean(c.avatar));
    }).catch((e) => { onError(e.message); onClose(); });
  }, [revision]);

  const writeNow = async () => {
    const { id: current, card: c, baseline: base, isDefaultMe: me } = now.current;
    if (!c || !String(c.name || "").trim()) return; // a card needs a name; the field says so
    const text = JSON.stringify(c);
    if (text === base) return;
    setSaving(true);
    try {
      const saved = current ? await api.put(`/characters/${current}`, c) : await api.post("/characters", c);
      if (!current) {
        setId(saved.id);
        if (me) await api.put("/settings", { default_me: saved.id });
      }
      now.current = { ...now.current, id: saved.id, baseline: text };
      setBaseline(text);
      onSaved(saved);
    } catch (err) {
      onError(err.message);
    } finally {
      setSaving(false);
    }
  };
  const flush = () => {
    clearTimeout(timer.current);
    timer.current = null;
    chain.current = chain.current.then(writeNow);
    return chain.current;
  };
  useEffect(() => {
    if (!dirty || noName) return undefined;
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, SAVE_PAUSE_MS);
    return () => clearTimeout(timer.current);
  }, [card]);

  const say = (text, kind = "ok") => notify(text, kind);
  const undo = async () => {
    await flush(); // the typed text is a change too: it is saved first, then the newest change goes back
    try {
      const { undone } = await api.post(`/characters/${id}/undo`);
      if (undone) say(`Отменено: ${undone.what}`); else say("Отменять больше нечего", "info");
      setRevision((r) => r + 1);
    } catch (e) { onError(e.message); }
  };
  const close = async () => {
    await flush();
    // a best-effort note that the version ends; closing must not fail because of it
    if (now.current.id) api.post(`/characters/${now.current.id}/history/close`).catch(() => {});
    onClose();
  };
  useEscape(() => (showHistory ? setShowHistory(false) : close())); // Esc closes the history first
  useEffect(() => {
    if (!id) return undefined;
    const key = (e) => {
      const typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable]");
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [id]);

  const set = (key) => (e) => setCard({ ...card, [key]: e.target.value });
  const chooseDefaultMe = async (on) => {
    setDefaultMe(on);
    if (!id) return; // a new card: set when it is created
    try { await api.put("/settings", { default_me: on ? id : "" }); } catch (err) { onError(err.message); setDefaultMe(!on); }
  };

  const title = id ? (card ? card.name || "Персонаж" : "Персонаж") : "Новый персонаж";
  const state = noName ? "Нужно имя — без него карточка не сохраняется"
    : saving ? "Сохраняется…" : dirty ? "Сохранится через секунду" : id ? "Сохранено" : "Впишите имя";
  return html`
    <div class="overlay" ...${backdrop(close)}>
      <div class=${"dialog stack" + (tab === "tree" ? " tree-dialog" : "")} data-slot="dialog" role="dialog"
        aria-modal="true" aria-label=${title}>
        <div class="side-head">
          <div class="row char-head-title">
            ${hasAvatar && id && html`<img class="speaker-avatar char-head-avatar" src=${`/api/characters/${id}/avatar`} alt="" />`}
            <strong class="char-dialog-title" title=${title}>${title}</strong>
          </div>
          <span class="spacer"></span>
          ${id && html`<${IconButton} icon="undo-2" label="Отменить последнее изменение"
            title="Отменить последнее изменение карточки или пузырей (Ctrl+Z)" onClick=${undo} />
            <${IconButton} icon="history" label="История версий" aria-pressed=${showHistory}
              onClick=${() => setShowHistory(!showHistory)} />`}
          <${IconButton} icon="x" label="Закрыть" onClick=${close} /></div>
        ${showHistory && id && html`<${HistoryPanel} base=${`/characters/${id}`} revision=${revision}
          onRestored=${(text) => { say(text); setRevision((r) => r + 1); }} onError=${onError} />`}
        ${id && html`<div class="tabs" role="tablist">
          <button role="tab" aria-selected=${tab === "card"} onClick=${() => setTab("card")}>Карточка</button>
          <button role="tab" aria-selected=${tab === "tree"} onClick=${() => setTab("tree")}>Дерево</button>
          <button role="tab" aria-selected=${tab === "regex"} onClick=${() => setTab("regex")}>Регекс</button>
        </div>`}
        ${card === null && html`<${Pending} />`}
        ${card !== null && tab === "tree" && html`<${CharacterTree}
          character=${{ id, name: card.name, avatar: hasAvatar }} settings=${settings}
          revision=${revision} onError=${onError} />`}
        ${card !== null && tab === "regex" && html`<div class="stack">
          <p class="hint">Правила этого персонажа работают в чатах, где он участвует. Общие правила — в Настройках, вкладка «Регекс».</p>
          <${RegexOwner} scope="character" owner=${id} onError=${onError} /></div>`}
        ${card !== null && tab === "card" && html`<form class="stack" onSubmit=${(e) => { e.preventDefault(); flush(); }}>
          <label class="stack">Имя
            <input required value=${card.name} onInput=${set("name")} aria-invalid=${noName ? "true" : null} /></label>
          ${FIELDS.map(([key, label, rows, hint]) => html`<label key=${key} class="stack">${label}
            ${hint && html`<span class="hint">${hint}</span>`}
            <textarea rows=${rows} value=${card[key] || ""} onInput=${set(key)}></textarea></label>`)}
          ${(hasImported(card) || hasImported(savedCard)) && html`<details class="imported-fields" data-slot="imported-fields">
            <summary>Поля из импортированной карточки</summary>
            <span class="hint">Эти поля пришли вместе с карточкой. Они по-прежнему уходят модели и сохраняются при экспорте.</span>
            ${IMPORTED_FIELDS.map(([key, label, rows, hint]) => html`<label key=${key} class="stack">${label}
              ${hint && html`<span class="hint">${hint}</span>`}
              <textarea rows=${rows} value=${card[key] || ""} onInput=${set(key)}></textarea></label>`)}
          </details>`}
          <div class="char-option-card">
            <label class="check"><input type="checkbox" checked=${isDefaultMe} onChange=${(e) => chooseDefaultMe(e.target.checked)} />
              <span>Мой персонаж по умолчанию</span></label>
            <p class="hint">Автоматически входит в каждый новый чат как «это я» (ваш герой, от чьего лица вы пишете).</p>
          </div>
          ${id && html`<${BookPicker} scope="character" target=${id} onError=${onError}
            title="Лорбуки персонажа"
            hint="Записи из выбранных лорбуков подключаются в любом чате с этим персонажем (и когда он говорит сам, и когда выбран как «это я»). Сохраняются сразу." />`}
          <div class="row sticky-save" data-slot="char-save-bar">
            <span class=${"save-state save-state-pill " + (noName ? "error" : saving ? "saving" : dirty ? "waiting" : "saved")}
              role="status" data-slot="char-save-state">
              <${Icon} name=${noName ? "triangle-alert" : saving ? "refresh-cw" : dirty ? "circle-dot" : "check"} size=${14}
                class=${saving ? "spin-icon" : ""} />
              <span>${state}</span>
            </span>
            <span class="spacer"></span>
            <span class="muted char-save-hint">Сохраняется само · отменить — ↶ вверху</span>
          </div>
        </form>`}
      </div>
    </div>
  `;
}
