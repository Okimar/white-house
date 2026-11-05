// Settings tab «Инструкции» (stage 10б): skills the assistant reads for a kind of work, and the
// programmer's guides. Built-in, plugin and programmer ones are read-only: «Дублировать» makes your own,
// and a duplicate of a built-in one replaces it for the assistant. Each can be switched off. The text is
// English (models follow it best). A skill opened from the assistant's «Инструкция» line is remembered in
// localStorage "tavern.skill" and shown here first. The programmer's guides are folded (closed by default).
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { notify } from "../core/notify.js";
import { Icon } from "./Icon.js";
import { ask } from "./Dialogs.js";
import { Pending, EmptyNote, useLoad } from "./State.js";

export const OPEN_SKILL_KEY = "tavern.skill";
const GROUPS = [["builtin", "Встроенные"], ["own", "Ваши"], ["plugin", "Из дополнений"], ["dev", "Для программиста"]];
const AUDIENCE = { assistant: "помощник", programmer: "программист", both: "помощник и программист" };

function wanted() {
  try { const id = localStorage.getItem(OPEN_SKILL_KEY); localStorage.removeItem(OPEN_SKILL_KEY); return id; } catch { return null; }
}

export function Skills({ onError }) {
  const loaded = useLoad(() => api.get("/skills").then((d) => d.skills), []);
  const list = loaded.data || [];
  const [openId, setOpenId] = useState(wanted);
  const [skill, setSkill] = useState(null);
  const [draft, setDraft] = useState(null);

  const reload = () => loaded.reload();
  useEffect(() => {
    if (!openId) { setSkill(null); setDraft(null); return; }
    api.get(`/skills/${encodeURIComponent(openId)}`).then((s) => { setSkill(s); setDraft(s.readonly ? null : { ...s }); })
      .catch((e) => { onError(e.message); setOpenId(null); });
  }, [openId]);

  const changed = Boolean(draft && skill && ["name", "title", "description", "body", "audience"].some((k) => draft[k] !== skill[k]));
  // Leaving a skill with unsaved edits asks first.
  const discardOk = async () => !changed || ask("Выбросить несохранённые правки?", { ok: "Выбросить", danger: true });
  const pick = async (id) => {
    if (id === openId) return;
    if (await discardOk()) setOpenId(id);
  };

  const act = async (fn) => { try { await fn(); } catch (e) { onError(e.message); } };
  const copy = () => act(async () => {
    if (!(await discardOk())) return;
    const made = await api.post("/skills", { from: skill.id });
    await reload();
    setOpenId(made.id);
  });
  const create = () => act(async () => {
    if (!(await discardOk())) return;
    const taken = new Set(list.map((s) => s.name));
    let name = "my-skill";
    for (let n = 2; taken.has(name); n += 1) name = `my-skill-${n}`;
    const made = await api.post("/skills", { name, title: "Новая инструкция", body: "" });
    await reload();
    setOpenId(made.id);
  });
  const save = () => act(async () => {
    const { name, title, description, body, audience } = draft;
    const saved = await api.put(`/skills/${skill.id}`, { name, title, description, body, audience });
    setSkill({ ...skill, ...saved });
    await reload();
    notify("Сохранено", "ok");
  });
  const toggle = (s) => act(async () => {
    await api.put(`/skills/${encodeURIComponent(s.id)}`, { enabled: !s.enabled });
    await reload();
    if (skill && skill.id === s.id) setSkill({ ...skill, enabled: !s.enabled });
  });
  const remove = () => act(async () => {
    if (!(await ask(`Удалить инструкцию «${skill.title}»?`, { ok: "Удалить", danger: true }))) return;
    await api.del(`/skills/${skill.id}`);
    setOpenId(null);
    await reload();
  });

  const status = (s) => (s.replaced ? "заменена вашим дубликатом" : !s.enabled ? "выключена"
    : s.used ? "включена, помощник читает" : "включена, только для программиста");

  const item = (s) => html`<div key=${s.id} class=${"skills-item" + (s.id === openId ? " open" : "") + (s.used ? "" : " off")}>
    <input type="checkbox" checked=${s.enabled} aria-label=${"Включить: " + s.title}
      onChange=${() => toggle(s)} />
    <button class="link" onClick=${() => pick(s.id)}>${s.title}</button>
    <small class="muted">${status(s)}</small>
  </div>`;

  return html`<div class="skills" data-slot="skills">
    <div class="skills-list" data-slot="skills-list">
      <p class="hint">Инструкции помощнику для разных работ. Он сам выбирает подходящую; в беседе видно строку
        <${Icon} name="book-open" /> «Инструкция: …». Текст на английском — так модели держат его надёжнее.</p>
      <button onClick=${create}><${Icon} name="plus" /> Новая инструкция</button>
      ${!loaded.data && html`<${Pending} error=${loaded.error} onRetry=${loaded.reload} />`}
      ${loaded.data && list.length === 0 && html`<${EmptyNote} title="Инструкций пока нет"
        hint="Создайте свою или дублируйте встроенную." />`}
      ${GROUPS.map(([source, label]) => {
        const items = list.filter((s) => s.source === source);
        if (!items.length) return null;
        if (source === "dev") {
          return html`<details key=${source} class="skills-group skills-dev" open=${skill?.source === "dev" || undefined}>
            <summary>${label} (${items.length})</summary>
            ${items.map(item)}
          </details>`;
        }
        return html`<div key=${source} class="skills-group">
          <strong>${label}</strong>
          ${items.map(item)}
        </div>`;
      })}
    </div>
    ${skill && html`<div class="skills-view" data-slot="skills-view">
      <div class="row">
        <h3>${skill.title}</h3>
        <span class="muted">${skill.name} · для: ${AUDIENCE[skill.audience] || skill.audience}${skill.plugin ? ` · дополнение ${skill.plugin}` : ""}</span>
      </div>
      ${skill.readonly ? html`
        <p>${skill.description}</p>
        <div class="row">
          <button onClick=${copy}><${Icon} name="copy" /> Дублировать</button>
          <span class="muted">${skill.source === "builtin"
            ? "Дубликат заменит встроенную для помощника; править можно только дубликат."
            : "Только для чтения; дубликат станет вашей инструкцией."}</span>
        </div>
        <pre class="skills-text">${skill.body}</pre>` : html`
        <label>Название<br /><input value=${draft.title} onInput=${(e) => setDraft({ ...draft, title: e.target.value })} /></label>
        <label>Имя (латиницей)<br /><input value=${draft.name} onInput=${(e) => setDraft({ ...draft, name: e.target.value })} /></label>
        <label>Когда брать<br />
          <input value=${draft.description} onInput=${(e) => setDraft({ ...draft, description: e.target.value })} />
          <span class="hint">По этой строке помощник выбирает инструкцию; лучше по-английски.</span></label>
        <label>Для кого<br /><select value=${draft.audience} onChange=${(e) => setDraft({ ...draft, audience: e.target.value })}>
          ${Object.entries(AUDIENCE).map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
        </select></label>
        ${skill.based_on && html`<p class="muted">Дубликат встроенной «${skill.based_on}»: пока включена, помощник читает её вместо встроенной.</p>`}
        <label>Текст<br />
          <textarea class="skills-text" rows="18" value=${draft.body} onInput=${(e) => setDraft({ ...draft, body: e.target.value })}></textarea>
          <span class="hint">Разделы «## Plan» и «## Write» читают только планировщик и писатель; строки «- …» под «## Check» — проверки для критика.</span></label>
        <div class="row">
          <button class="primary" disabled=${!changed} onClick=${save}>Сохранить</button>
          <button onClick=${copy}><${Icon} name="copy" /> Дублировать</button>
          <button class="danger-text" onClick=${remove}><${Icon} name="trash-2" /> Удалить</button>
          ${changed && html`<span class="muted">Есть несохранённые изменения</span>`}
        </div>`}
    </div>`}
  </div>`;
}
