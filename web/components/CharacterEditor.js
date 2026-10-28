// Character editor: the card (create or change it) and, for an existing character, its tree of
// blocks (tab "Дерево", see CharacterTree.js). The user's own character is an ordinary
// character too; "Мой персонаж по умолчанию" makes it join every new chat as "me".
// Unknown card fields are kept: the editor sends back the whole card with only these fields changed.
// "↶ Отменить" (or Ctrl+Z outside text fields) takes back the newest change of the card or its
// bubbles, as far back as you like; "🕘 История" lists versions to restore. Closing the editor
// ends the current version.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { CharacterTree } from "./CharacterTree.js";

function HistoryPanel({ characterId, revision, onRestored, onError }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    api.get(`/characters/${characterId}/history`).then(setData).catch((e) => onError(e.message));
  }, [characterId, revision]);
  const restore = async (version) => {
    if (!confirm("Вернуть персонажа к этой версии? Всё сделанное после неё можно будет вернуть только заново.")) return;
    try {
      const { undone } = await api.post(`/characters/${characterId}/history/${version.id}/restore`);
      onRestored(`Возвращено к версии: отменено шагов — ${undone}`);
    } catch (e) { onError(e.message); }
  };
  if (!data) return html`<p class="muted">Загрузка…</p>`;
  const when = (at) => new Date(at).toLocaleString();
  return html`<div class="history" data-slot="character-history">
    ${!data.enabled && html`<p class="muted">История выключена в настройках.</p>`}
    ${data.versions.length === 0 && html`<p class="muted">Изменений пока нет.</p>`}
    ${data.versions.map((v, i) => html`<details key=${v.id} open=${i === 0} class="history-version">
      <summary><strong>${i === 0 && !v.closed_at ? "Сейчас" : when(v.closed_at || v.started_at)}</strong>
        <span class="muted"> · шагов: ${v.steps.length}</span>
        ${i > 0 && html`<button onClick=${(e) => { e.preventDefault(); restore(v); }}>Вернуть эту версию</button>`}</summary>
      <ol class="history-steps">
        ${v.steps.map((s) => html`<li key=${s.id} class=${s.undone ? "undone" : ""}>
          <span class="muted">${new Date(s.at).toLocaleTimeString()}</span> ${s.what}${s.undone ? " (отменено)" : ""}</li>`)}
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
  ["system_prompt", "Свой главный промпт (заменяет промпт пресета, {{original}} — вставить его)", 3],
  ["post_history_instructions", "Инструкция после истории", 3],
];

export function CharacterEditor({ characterId, settings, initialTab, onClose, onSaved, onError }) {
  const [card, setCard] = useState(characterId ? null : { name: "" });
  const [isDefaultMe, setDefaultMe] = useState(Boolean(characterId) && settings.default_me === characterId);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState(initialTab || "card");
  const [hasAvatar, setHasAvatar] = useState(false);
  const [revision, setRevision] = useState(0); // bumps after undo / restore: everything reloads
  const [showHistory, setShowHistory] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!characterId) return;
    api.get(`/characters/${characterId}`).then((c) => { setCard(c.data); setHasAvatar(Boolean(c.avatar)); })
      .catch((e) => { onError(e.message); onClose(); });
  }, [characterId, revision]);

  const say = (text) => { setStatus(text); setTimeout(() => setStatus(""), 3500); };
  const undo = async () => {
    try {
      const { undone } = await api.post(`/characters/${characterId}/undo`);
      say(undone ? `Отменено: ${undone.what}` : "Отменять больше нечего");
      setRevision((r) => r + 1);
    } catch (e) { onError(e.message); }
  };
  const close = () => {
    if (characterId) api.post(`/characters/${characterId}/history/close`).catch(() => {});
    onClose();
  };
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
  }, [characterId]);

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
      onSaved(saved);
    } catch (err) {
      onError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const title = characterId ? (card ? card.name || "Персонаж" : "Персонаж") : "Новый персонаж";
  return html`
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && close()}>
      <div class=${"dialog stack" + (tab === "tree" ? " tree-dialog" : "")} data-slot="dialog" role="dialog"
        aria-modal="true" aria-label=${title}>
        <div class="side-head"><strong>${title}</strong>
          ${status && html`<span class="muted history-status" role="status">${status}</span>`}
          <span class="spacer"></span>
          ${characterId && html`<button type="button" onClick=${undo}
            title="Отменить последнее изменение карточки или пузырей (Ctrl+Z)">↶ Отменить</button>
            <button type="button" aria-pressed=${showHistory} onClick=${() => setShowHistory(!showHistory)}
              title="Версии персонажа">🕘 История</button>`}
          <button type="button" onClick=${close} title="Закрыть">✕</button></div>
        ${showHistory && characterId && html`<${HistoryPanel} characterId=${characterId} revision=${revision}
          onRestored=${(text) => { say(text); setRevision((r) => r + 1); }} onError=${onError} />`}
        ${characterId && html`<div class="tabs" role="tablist">
          <button role="tab" aria-selected=${tab === "card"} onClick=${() => setTab("card")}>Карточка</button>
          <button role="tab" aria-selected=${tab === "tree"} onClick=${() => setTab("tree")}>Дерево</button>
        </div>`}
        ${card === null && html`<p class="muted">Загрузка…</p>`}
        ${card !== null && tab === "tree" && html`<${CharacterTree}
          character=${{ id: characterId, name: card.name, avatar: hasAvatar }} settings=${settings}
          revision=${revision} onError=${onError} />`}
        ${card !== null && tab === "card" && html`<form class="stack" onSubmit=${save}>
          <label class="stack">Имя
            <input required value=${card.name} onInput=${set("name")} /></label>
          ${FIELDS.map(([key, label, rows]) => html`<label key=${key} class="stack">${label}
            <textarea rows=${rows} value=${card[key] || ""} onInput=${set(key)}></textarea></label>`)}
          <label><input type="checkbox" checked=${isDefaultMe} onChange=${(e) => setDefaultMe(e.target.checked)} />
            Мой персонаж по умолчанию (входит в каждый новый чат как «это я»)</label>
          <div class="row">
            <button class="primary" type="submit" disabled=${saving}>Сохранить</button>
            <button type="button" onClick=${close}>Отмена</button>
          </div>
        </form>`}
      </div>
    </div>
  `;
}
