// Connection profiles and fallback steps.
// - Profiles: the "Профили" block of Settings → Подключения (save what is active, choose which parts
//   it switches, apply, update, delete).
// - ProfileSwitch: a compact picker for the chat's top bar: one click switches everything.
// - FallbackSteps: the "Если не получилось" part of a connection: steps tried in order when the
//   connection fails before writing anything (another model, connection or key = proxy).
// - Parallel: "Несколько запросов сразу" (Settings → Подключения): extra requests beside the main one.
// - PickVariants: the window that shows every version of a reply side by side ("pick" mode).
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";

const PARTS = [["connection", "подключение"], ["model", "модель"], ["params", "настройки"], ["preset", "пресет"]];
const REASONS = [
  ["refused", "отказ (цензура)"], ["empty", "пустой ответ"], ["limit", "кончился лимит"],
  ["auth", "ключ не принят"], ["network", "нет связи"], ["server", "сервис сломался"], ["request", "запрос отвергнут"],
];

export function useProfiles(onError) {
  const [state, setState] = useState({ profiles: [], active: null });
  const reload = () => api.get("/profiles").then(setState).catch((e) => onError(e.message));
  useEffect(() => { reload(); }, []);
  const apply = async (id) => {
    try {
      const d = await api.post(`/profiles/${id}/apply`, {});
      setState({ profiles: d.profiles, active: d.active });
      return d.connection;
    } catch (err) { onError(err.message); return null; }
  };
  return { ...state, reload, apply };
}

export function ProfileSwitch({ onChanged, onError }) {
  const { profiles, active, apply } = useProfiles(onError);
  if (!profiles.length) return null;
  return html`<select class="profile-switch" data-slot="profile-switch" value=${active || ""}
    aria-label="Профиль подключения" title="Профиль подключения: переключает всё сразу"
    onChange=${async (e) => { if (e.target.value && await apply(e.target.value)) onChanged(); }}>
    ${!active && html`<option value="">профиль…</option>`}
    ${profiles.map((p) => html`<option key=${p.id} value=${p.id}>${p.name}</option>`)}
  </select>`;
}

export function Profiles({ onApplied, onError }) {
  const { profiles, active, reload, apply } = useProfiles(onError);
  const [name, setName] = useState("");
  const [parts, setParts] = useState({ connection: true, model: true, params: true, preset: true });

  const save = async (e) => {
    e.preventDefault();
    try {
      await api.post("/profiles", { name, parts });
      setName("");
      reload();
    } catch (err) { onError(err.message); }
  };
  const recapture = async (p) => {
    if (!confirm(`Записать в «${p.name}» то, что выбрано сейчас?`)) return;
    await api.put(`/profiles/${p.id}`, { recapture: true }).catch((e) => onError(e.message));
    reload();
  };
  const toggle = async (p, part) => {
    const next = { ...p.parts, [part]: !p.parts[part] };
    await api.put(`/profiles/${p.id}`, { parts: next }).catch((e) => onError(e.message));
    reload();
  };
  const remove = async (p) => {
    if (!confirm(`Удалить профиль «${p.name}»?`)) return;
    await api.del(`/profiles/${p.id}`).catch((e) => onError(e.message));
    reload();
  };

  return html`
    <h3>Профили <span class="muted">(переключают всё одним нажатием)</span></h3>
    <ul class="list profiles" data-slot="profiles">
      ${profiles.map((p) => html`<li key=${p.id} class=${"profile-row" + (p.id === active ? " active" : "")}>
        <button class=${p.id === active ? "primary" : ""} onClick=${async () => { if (await apply(p.id)) onApplied(); }}
          title=${`${p.connection_name} · ${p.model || "модель не выбрана"}`}>${p.name}</button>
        ${PARTS.map(([k, label]) => html`<label key=${k}><input type="checkbox" checked=${p.parts[k]}
          onChange=${() => toggle(p, k)} />${label}</label>`)}
        <span class="spacer"></span>
        <button onClick=${() => recapture(p)} title="Записать в профиль то, что выбрано сейчас">⟳</button>
        <button onClick=${() => remove(p)} aria-label="Удалить профиль">✕</button>
      </li>`)}
    </ul>
    <form class="row" onSubmit=${save}>
      <input required placeholder="Название профиля" value=${name} onInput=${(e) => setName(e.target.value)}
        aria-label="Название профиля" />
      ${PARTS.map(([k, label]) => html`<label key=${k}><input type="checkbox" checked=${parts[k]}
        onChange=${() => setParts({ ...parts, [k]: !parts[k] })} />${label}</label>`)}
      <button type="submit">Сохранить текущее как профиль</button>
    </form>`;
}

export function FallbackSteps({ conn, connections, steps, phrases, onSteps, onPhrases }) {
  const [phraseText, setPhraseText] = useState((phrases || []).join("\n"));
  const set = (i, patch) => onSteps(steps.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const flip = (i, reason) => {
    const on = steps[i].on.includes(reason) ? steps[i].on.filter((r) => r !== reason) : [...steps[i].on, reason];
    if (on.length) set(i, { on });
  };
  const move = (i, by) => {
    const next = [...steps];
    const [s] = next.splice(i, 1);
    next.splice(i + by, 0, s);
    onSteps(next);
  };
  const target = (s) => connections.find((c) => c.id === (s.connection || conn.id)) || conn;

  return html`
    <h3>Если не получилось <span class="muted">(до первого слова ответа)</span></h3>
    <ol class="fallback-steps" data-slot="fallback-steps">
      ${steps.map((s, i) => html`<li key=${i} class="fallback-step stack">
        <div class="row">${REASONS.map(([r, label]) => html`<label key=${r}>
          <input type="checkbox" checked=${s.on.includes(r)} onChange=${() => flip(i, r)} />${label}</label>`)}</div>
        <div class="row">
          <span class="muted">→</span>
          <select value=${s.connection || ""} aria-label="Подключение" onChange=${(e) => set(i, { connection: e.target.value, key: "" })}>
            <option value="">это же подключение</option>
            ${connections.filter((c) => c.id !== conn.id).map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
          </select>
          <input value=${s.model} placeholder="модель (пусто — та же)" aria-label="Модель"
            onInput=${(e) => set(i, { model: e.target.value })} />
          <select value=${s.key || ""} aria-label="Ключ (свой прокси)" onChange=${(e) => set(i, { key: e.target.value })}>
            <option value="">ключи по порядку</option>
            ${target(s).keys.map((k) => html`<option key=${k.id} value=${k.id}>${k.label || "ключ"}${k.has_proxy ? " · " + k.proxy : ""}</option>`)}
          </select>
          <span class="spacer"></span>
          <button disabled=${i === 0} onClick=${() => move(i, -1)} aria-label="Выше">↑</button>
          <button disabled=${i === steps.length - 1} onClick=${() => move(i, 1)} aria-label="Ниже">↓</button>
          <button onClick=${() => onSteps(steps.filter((_, j) => j !== i))} aria-label="Убрать шаг">✕</button>
        </div>
      </li>`)}
    </ol>
    <button onClick=${() => onSteps([...steps, { on: ["refused"], connection: "", model: "", key: "" }])}>＋ Шаг</button>
    ${steps.some((s) => s.on.includes("refused")) && html`
      <label class="stack">Фразы отказа: короткий ответ с такой фразой считается отказом (пусто — встроенный список)
        <textarea rows="3" value=${phraseText} placeholder="по одной на строку"
          onInput=${(e) => { setPhraseText(e.target.value); onPhrases(e.target.value.split("\n").filter((x) => x.trim())); }} />
      </label>
      <p class="muted">Пока ждём отказ, короткий ответ появляется не сразу, а целиком: так его можно заменить.</p>`}`;
}

const MODES = [
  ["variants", "все ответы — варианты сообщения (листать стрелками)"],
  ["first", "кто первым начал писать — тот и отвечает, остальные останавливаются"],
  ["pick", "показать все ответы рядом и выбрать лучший"],
];

export function Parallel({ connections, onError }) {
  const [plan, setPlan] = useState(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { api.get("/parallel").then(setPlan).catch((e) => onError(e.message)); }, []);
  if (!plan) return null;
  const change = (patch) => { setPlan({ ...plan, ...patch }); setDirty(true); };
  const setTarget = (i, patch) => change({ targets: plan.targets.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  const save = async () => {
    try { setPlan(await api.put("/parallel", plan)); setDirty(false); } catch (err) { onError(err.message); }
  };
  const total = plan.targets.reduce((n, t) => n + t.count, 0);

  return html`
    <h3>Несколько запросов сразу</h3>
    <section class="parallel stack" data-slot="parallel">
      <label><input type="checkbox" checked=${plan.enabled} onChange=${(e) => change({ enabled: e.target.checked })} />
        вместе с основным запросом отправлять ещё (при отправке и перегенерации)</label>
      <select value=${plan.mode} aria-label="Что делать с ответами" onChange=${(e) => change({ mode: e.target.value })}>
        ${MODES.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
      </select>
      <ul class="list">
        ${plan.targets.map((t, i) => html`<li key=${i} class="row parallel-target">
          <select value=${t.connection} aria-label="Подключение" onChange=${(e) => setTarget(i, { connection: e.target.value })}>
            <option value="">выбранное сейчас</option>
            ${connections.map((c) => html`<option key=${c.id} value=${c.id}>${c.name}</option>`)}
          </select>
          <input value=${t.model} placeholder="модель (пусто — своя)" aria-label="Модель"
            onInput=${(e) => setTarget(i, { model: e.target.value })} />
          <label>×<input type="number" class="num" min="1" max="8" value=${t.count} aria-label="Сколько раз"
            onChange=${(e) => setTarget(i, { count: Math.max(1, parseInt(e.target.value, 10) || 1) })} /></label>
          <button onClick=${() => change({ targets: plan.targets.filter((_, j) => j !== i) })} aria-label="Убрать">✕</button>
        </li>`)}
      </ul>
      <div class="row">
        <button onClick=${() => change({ targets: [...plan.targets, { connection: "", model: "", count: 1 }] })}>＋ Запрос</button>
        <span class="muted">${total ? `дополнительно: ${total} (до 8)` : "дополнительных запросов нет"}</span>
        <span class="spacer"></span>
        <button class="primary" disabled=${!dirty} onClick=${save}>Сохранить</button>
      </div>
      <p class="muted">Каждый запрос — это отдельная оплата у сервиса. Подключение может повторяться: «×3» спросит его трижды.</p>
    </section>`;
}

export function PickVariants({ messageId, onPick, onClose, onError }) {
  const [swipes, setSwipes] = useState(null);
  useEffect(() => {
    api.get(`/messages/${messageId}/swipes`).then((d) => setSwipes(d.swipes)).catch((e) => onError(e.message));
  }, [messageId]);
  return html`
    <div class="overlay" onClick=${(e) => e.target === e.currentTarget && onClose()}>
      <div class="dialog wide" data-slot="dialog" role="dialog" aria-modal="true" aria-label="Выбор ответа">
        <div class="side-head"><strong>Выберите ответ</strong><span class="spacer"></span>
          <button onClick=${onClose} title="Закрыть">✕</button></div>
        ${!swipes && html`<p class="muted">Загрузка…</p>`}
        <div class="pick-grid" data-slot="pick-variants">
          ${(swipes || []).map((s) => html`<article key=${s.index} class="pick-card stack">
            <header class="muted">${s.meta.via || `Вариант ${s.index + 1}`}</header>
            <div class="pick-text">${s.content}</div>
            <button class="primary" onClick=${() => onPick(s.index)}>Этот</button>
          </article>`)}
        </div>
        <p class="muted">Остальные ответы останутся вариантами сообщения: их можно будет пролистать стрелками.</p>
      </div>
    </div>`;
}
