// Spending (stage 6е).
// - SpendingBar: the top-bar line "чат · сегодня · месяц" (each part only when its checkbox is on);
//   red when a limit is reached.
// - SpendingSettings: the "Расход" tab of Settings: where it is shown, currency and rate, limits and
//   what happens at a limit, own prices per model, and the last month by model and by day.
import { html, useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { money, sumLine, tokens, spendingChanged } from "../core/spending.js";

const PLACES = [["message", "под каждым сообщением"], ["chat", "за чат"], ["day", "за сегодня"], ["month", "за месяц"]];

function limitText(limit, settings) {
  return `${limit} ${settings.currency.code || "$"}`;
}

export function SpendingBar({ summary }) {
  if (!summary) return null;
  const s = summary.settings;
  const parts = [];
  if (s.show.chat && summary.chat) parts.push(["chat", "чат", summary.chat, 0]);
  if (s.show.day) parts.push(["day", "сегодня", summary.day, s.limits.day]);
  if (s.show.month) parts.push(["month", "месяц", summary.month, s.limits.month]);
  if (!parts.length) return null;
  return html`<span class="spending-bar muted" data-slot="spending-bar">
    ${parts.map(([id, label, sum, limit]) => html`<span key=${id}
      class=${"spend-part" + (summary.over === id ? " over" : "")}
      title=${`${label}: запросов ${sum.requests}, токенов ${sum.prompt_tokens} + ${sum.completion_tokens}` +
        (sum.unpriced ? `; без цены: ${sum.unpriced}` : "")}>
      ${label} ${sumLine(sum, s)}${limit ? html` <span class="spend-limit">/ ${limitText(limit, s)}</span>` : ""}
    </span>`)}
  </span>`;
}

function priceText(price) {
  if (price.in === null && price.out === null) return "неизвестна";
  const who = price.source === "own" ? "своя" : "от сервиса";
  return `$${price.in ?? 0} / $${price.out ?? 0} · ${who}`;
}

export function SpendingSettings({ onError }) {
  const [settings, setSettings] = useState(null);
  const [prices, setPrices] = useState([]);
  const [report, setReport] = useState(null);
  const [dirty, setDirty] = useState(false);

  const load = () => {
    api.get("/spending/settings").then((s) => {
      setSettings(s);
      setPrices(Object.entries(s.prices).map(([model, p]) => ({ model, in: p.in, out: p.out })));
      setDirty(false);
    }).catch((e) => onError(e.message));
    api.get("/spending/report").then(setReport).catch((e) => onError(e.message));
  };
  useEffect(load, []);
  if (!settings) return html`<p class="muted">Загрузка…</p>`;

  const change = (patch) => { setSettings({ ...settings, ...patch }); setDirty(true); };
  const setPrice = (i, patch) => { setPrices(prices.map((p, j) => (j === i ? { ...p, ...patch } : p))); setDirty(true); };
  const ownPrice = (row) => {
    if (prices.some((p) => p.model === row.model)) return;
    setPrices([...prices, { model: row.model, in: row.price.in ?? 0, out: row.price.out ?? 0 }]);
    setDirty(true);
  };
  const num = (value) => Math.max(0, parseFloat(String(value).replace(",", ".")) || 0);
  const save = async () => {
    const own = {};
    for (const p of prices) if (p.model.trim()) own[p.model.trim()] = { in: num(p.in), out: num(p.out) };
    try {
      await api.put("/spending/settings", { ...settings, prices: own });
      load();
      spendingChanged();
    } catch (err) { onError(err.message); }
  };
  const cur = settings.currency.code || "$";

  return html`
    <section class="spending stack" data-slot="spending-settings">
      <p class="muted">Расход считается всегда; галочки решают только, где его показывать.</p>
      <h3>Где показывать</h3>
      <div class="row">
        ${PLACES.map(([id, label]) => html`<label key=${id}><input type="checkbox" checked=${settings.show[id]}
          onChange=${(e) => change({ show: { ...settings.show, [id]: e.target.checked } })} />${label}</label>`)}
        <label><input type="checkbox" checked=${settings.tokens}
          onChange=${(e) => change({ tokens: e.target.checked })} />и токены</label>
      </div>

      <h3>Валюта</h3>
      <div class="row">
        <input class="num" value=${settings.currency.code} placeholder="$" aria-label="Своя валюта" maxlength="8"
          onInput=${(e) => change({ currency: { ...settings.currency, code: e.target.value } })} />
        <label>за 1 $ <input class="num" type="number" min="0" step="any" value=${settings.currency.rate || ""}
          aria-label="Курс: сколько за доллар" disabled=${!settings.currency.code}
          onInput=${(e) => change({ currency: { ...settings.currency, rate: num(e.target.value) } })} /></label>
        <span class="muted">Пусто — в долларах. Сервисы берут доллары; курс вы пишете сами.</span>
      </div>

      <h3>Лимит</h3>
      <div class="row">
        <label>в день <input class="num" type="number" min="0" step="any" value=${settings.limits.day || ""}
          placeholder="нет" onInput=${(e) => change({ limits: { ...settings.limits, day: num(e.target.value) } })} /> ${cur}</label>
        <label>в месяц <input class="num" type="number" min="0" step="any" value=${settings.limits.month || ""}
          placeholder="нет" onInput=${(e) => change({ limits: { ...settings.limits, month: num(e.target.value) } })} /> ${cur}</label>
        <select value=${settings.on_limit} aria-label="Что делать у лимита"
          onChange=${(e) => change({ on_limit: e.target.value })}>
          <option value="warn">только предупреждать</option>
          <option value="stop">не отправлять новые запросы</option>
        </select>
      </div>

      <h3>Свои цены <span class="muted">(долларов за миллион токенов; важнее цены сервиса)</span></h3>
      <ul class="list">
        ${prices.map((p, i) => html`<li key=${i} class="row">
          <input value=${p.model} placeholder="модель" aria-label="Модель" onInput=${(e) => setPrice(i, { model: e.target.value })} />
          <label>запрос $<input class="num" type="number" min="0" step="any" value=${p.in}
            onInput=${(e) => setPrice(i, { in: e.target.value })} /></label>
          <label>ответ $<input class="num" type="number" min="0" step="any" value=${p.out}
            onInput=${(e) => setPrice(i, { out: e.target.value })} /></label>
          <button onClick=${() => { setPrices(prices.filter((_, j) => j !== i)); setDirty(true); }} aria-label="Убрать цену">✕</button>
        </li>`)}
      </ul>
      <div class="row">
        <button onClick=${() => { setPrices([...prices, { model: "", in: 0, out: 0 }]); setDirty(true); }}>＋ Цена</button>
        <span class="spacer"></span>
        <button class="primary" disabled=${!dirty} onClick=${save}>Сохранить</button>
      </div>

      ${report && html`
        <h3>За последний месяц <span class="muted">(с ${report.since})</span></h3>
        ${!report.models.length && html`<p class="muted">Запросов пока не было.</p>`}
        ${report.models.length > 0 && html`<div class="table-wrap"><table class="spend-table" data-slot="spending-report">
          <thead><tr><th>Модель</th><th>Запросов</th><th>Токены ↑ / ↓</th><th>Стоимость</th><th>Цена за 1M</th><th></th></tr></thead>
          <tbody>${report.models.map((r) => html`<tr key=${r.connection_id + r.model}>
            <td>${r.model || "—"}<div class="muted">${r.connection_name}</div></td>
            <td>${r.requests}</td>
            <td>${tokens(r.prompt_tokens)} / ${tokens(r.completion_tokens)}</td>
            <td>${money(r.cost, settings)}${r.unpriced ? html`<span class="muted" title="Запросы без известной цены"> +${r.unpriced}?</span>` : ""}</td>
            <td>${priceText(r.price)}</td>
            <td>${r.price.source !== "own" && html`<button onClick=${() => ownPrice(r)}
              title="Указать для этой модели свою цену">своя</button>`}</td>
          </tr>`)}</tbody>
        </table></div>`}
        ${(report.plugins || []).length > 0 && html`<h3>Дополнения</h3>
        <ul class="list spend-plugins" data-slot="spending-plugins">
          ${report.plugins.map((p) => html`<li key=${p.plugin} class="row"><span>${p.name}</span>
            <span class="muted">запросов: ${p.requests}</span><span class="spacer"></span>
            <span class="muted">${sumLine(p, settings)}</span></li>`)}
        </ul>`}
        ${report.assistant && html`<h3>Помощник</h3>
        <ul class="list spend-plugins" data-slot="spending-assistant">
          <li class="row"><span>🪄 Помощник</span>
            <span class="muted">запросов: ${report.assistant.requests}</span><span class="spacer"></span>
            <span class="muted">${sumLine(report.assistant, settings)}</span></li>
        </ul>`}
        ${report.days.length > 0 && html`<ul class="list spend-days">
          ${report.days.map((d) => html`<li key=${d.day} class="row"><span>${d.day}</span><span class="spacer"></span>
            <span class="muted">${sumLine(d, settings)}</span></li>`)}
        </ul>`}`}
    </section>`;
}
