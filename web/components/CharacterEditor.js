// Character editor: the card (create or change it) and, for an existing character, its tree of
// bubbles (tab "Дерево", see CharacterTree.js). The user's own character is an ordinary
// character too; "Мой персонаж по умолчанию" makes it join every new chat as "me".
// Unknown card fields are kept: the editor sends back the whole card with only these fields changed.
// "Отменить" (or Ctrl+Z outside text fields) takes back the newest saved change of the card or its
// bubbles, as far back as you like; "История" lists versions to restore. Closing the editor
// ends the current version. Closing or undoing with unsaved card text asks first.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { CharacterTree } from "./CharacterTree.js";
import { BookPicker } from "./Lorebooks.js";
import { IconButton } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, useLoad } from "./State.js";
import { notify } from "../core/notify.js";
import { backdrop, useEscape } from "../core/keys.js";

// Versions of a character or a lorebook; `base` is its API path ("/characters/ID", "/lorebooks/ID").
export function HistoryPanel({ base, revision, onRestored, onError }) {
  const { data, error, reload } = useLoad(() => api.get(`${base}/history`), [base, revision]);
  const restore = async (version) => {
    if (!await ask("Вернуть к этой версии?", { ok: "Вернуть", danger: true,
      hint: "Всё, что сделано после неё, можно будет вернуть только заново." })) return;
    try {
      const { undone } = await api.post(`${base}/history/${version.id}/restore`);
      onRestored(`Возвращено к версии, отменено шагов: ${undone}`);
    } catch (e) { onError(e.message); }
  };
  if (!data) return html`<${Pending} error=${error} onRetry=${reload} />`;
  const when = (at) => new Date(at).toLocaleString("ru-RU");
  return html`<div class="history" data-slot="character-history">
    ${!data.enabled && html`<p class="muted">История выключена в настройках</p>`}
    ${data.versions.length === 0 && html`<p class="muted">Изменений пока нет</p>`}
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
  </div>`;
}

const FIELDS = [
  ["description", "Описание", 8],
  ["personality", "Характер", 3],
  ["scenario", "Сценарий", 3],
  ["first_mes", "Первое сообщение", 4],
  ["mes_example", "Примеры диалога", 4],
  ["system_prompt", "Свой главный промпт", 3, "Заменяет главный промпт пресета; {{original}} вставляет его на это место"],
  ["post_history_instructions", "Инструкция после истории", 3],
];

export function CharacterEditor({ characterId, settings, initialTab, onClose, onSaved, onError }) {
  const [card, setCard] = useState(characterId ? null : { name: "" });
  const [baseline, setBaseline] = useState(characterId ? "" : JSON.stringify({ name: "" })); // the card as last loaded or saved
  const [isDefaultMe, setDefaultMe] = useState(Boolean(characterId) && settings.default_me === characterId);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState(initialTab || "card");
  const [hasAvatar, setHasAvatar] = useState(false);
  const [revision, setRevision] = useState(0); // bumps after undo / restore: everything reloads
  const [showHistory, setShowHistory] = useState(false);
  const wasDefaultMe = Boolean(characterId) && settings.default_me === characterId;
  const dirty = card !== null && (JSON.stringify(card) !== baseline || isDefaultMe !== wasDefaultMe);

  useEffect(() => {
    if (!characterId) return;
    api.get(`/characters/${characterId}`).then((c) => {
      setCard(c.data); setBaseline(JSON.stringify(c.data)); setHasAvatar(Boolean(c.avatar));
    }).catch((e) => { onError(e.message); onClose(); });
  }, [characterId, revision]);

  const say = (text, kind = "ok") => notify(text, kind);
  const undo = async () => {
    if (dirty && !await ask("Отменить последнее сохранённое изменение?", { ok: "Отменить", danger: true,
      hint: "Несохранённый текст карточки пропадёт." })) return;
    try {
      const { undone } = await api.post(`/characters/${characterId}/undo`);
      if (undone) say(`Отменено: ${undone.what}`); else say("Отменять больше нечего", "info");
      setRevision((r) => r + 1);
    } catch (e) { onError(e.message); }
  };
  const close = () => {
    // a best-effort note that the version ends; closing must not fail because of it
    if (characterId) api.post(`/characters/${characterId}/history/close`).catch(() => {});
    onClose();
  };
  const requestClose = async () => {
    if (dirty && !await ask("Закрыть без сохранения?", { ok: "Закрыть", danger: true })) return;
    close();
  };
  useEscape(requestClose);
  useEffect(() => {
    if (!characterId) return undefined;
    const key = (e) => {
      const typing = e.target.closest && e.target.closest("input, textarea, select, [contenteditable]");
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z" && !typing) {
        e.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [characterId, dirty]);

  const set = (key) => (e) => setCard({ ...card, [key]: e.target.value });

  const save = async (e) => {
    e.preventDefault();
    if (!card.name.trim()) { onError("У персонажа должно быть имя"); return; }
    setSaving(true);
    try {
      const saved = characterId
        ? await api.put(`/characters/${characterId}`, card)
        : await api.post("/characters", card);
      const wasDefault = settings.default_me === saved.id;
      if (isDefaultMe !== wasDefault) {
        await api.put("/settings", { default_me: isDefaultMe ? saved.id : "" });
      }
      setBaseline(JSON.stringify(card));
      onSaved(saved);
    } catch (err) {
      onError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const title = characterId ? (card ? card.name || "Персонаж" : "Персонаж") : "Новый персонаж";
  return html`
    <div class="overlay" ...${backdrop(requestClose)}>
      <div class=${"dialog stack" + (tab === "tree" ? " tree-dialog" : "")} data-slot="dialog" role="dialog"
        aria-modal="true" aria-label=${title}>
        <div class="side-head"><strong>${title}</strong>
          <span class="spacer"></span>
          ${characterId && html`<${IconButton} icon="undo-2" label="Отменить последнее изменение"
            title="Отменить последнее сохранённое изменение карточки или пузырей (Ctrl+Z)" onClick=${undo} />
            <${IconButton} icon="history" label="История версий" aria-pressed=${showHistory}
              onClick=${() => setShowHistory(!showHistory)} />`}
          <${IconButton} icon="x" label="Закрыть" onClick=${requestClose} /></div>
        ${showHistory && characterId && html`<${HistoryPanel} base=${`/characters/${characterId}`} revision=${revision}
          onRestored=${(text) => { say(text); setRevision((r) => r + 1); }} onError=${onError} />`}
        ${characterId && html`<div class="tabs" role="tablist">
          <button role="tab" aria-selected=${tab === "card"} onClick=${() => setTab("card")}>Карточка</button>
          <button role="tab" aria-selected=${tab === "tree"} onClick=${() => setTab("tree")}>Дерево</button>
        </div>`}
        ${card === null && html`<${Pending} />`}
        ${card !== null && tab === "tree" && html`<${CharacterTree}
          character=${{ id: characterId, name: card.name, avatar: hasAvatar }} settings=${settings}
          revision=${revision} onError=${onError} />`}
        ${card !== null && tab === "card" && html`<form class="stack" onSubmit=${save}>
          <label class="stack">Имя
            <input required value=${card.name} onInput=${set("name")} /></label>
          ${FIELDS.map(([key, label, rows, hint]) => html`<label key=${key} class="stack">${label}
            ${hint && html`<span class="hint">${hint}</span>`}
            <textarea rows=${rows} value=${card[key] || ""} onInput=${set(key)}></textarea></label>`)}
          <label><input type="checkbox" checked=${isDefaultMe} onChange=${(e) => setDefaultMe(e.target.checked)} />
            Мой персонаж по умолчанию</label>
          <span class="hint">Входит в каждый новый чат как «это я»</span>
          ${characterId && html`<${BookPicker} scope="character" target=${characterId} onError=${onError}
            title="Лорбуки персонажа"
            hint="Работают в любом чате, где он участвует, и как персонаж, и как «это я». Сохраняются сразу" />`}
          <div class="row sticky-save">
            <button class="primary" type="submit" disabled=${saving}>Сохранить</button>
            <button type="button" onClick=${requestClose}>Отмена</button>
          </div>
        </form>`}
      </div>
    </div>
  `;
}
