// 🧠 Память++ (stages M1-M4): the composer button opens the «Память» window; its «Главы» tab lists the chapters of
// the open branch (split, merge, move the border, delete, restore, undo) with their summaries (edit = lock, lock,
// «Пересказать заново», versions), «Сюжет до сих пор» on top, and a folded «Участники» list (M3). Chapter borders are thin lines above
// messages, «Новая глава отсюда» is in a message's «⋯» menu, and a chip in the top bar shows the background work.
// M4: the «Развитие» tab: a card per character of the chat and «Я» (the layer «how they are now»: edit = lock, lock,
// versions, «Перенести в другой чат…», «Не наслаивать»), and the branch's «Сейчас:» lines of lorebook bubbles (edit,
// «Удалить», «Записать в лорбук насовсем»).
export default function setup(t) {
  let cache = { chatId: "", view: null, state: null, job: null };
  let layersTick = 0; // bumped by the notice «layers»: the «Развитие» tab loads again
  let focusId = "";
  let loading = false;

  const reload = async () => {
    const chatId = t.chatId();
    if (!chatId) {
      cache = { chatId: "", view: null, state: null, job: null };
      t.refresh();
      return;
    }
    if (loading) return;
    loading = true;
    try {
      const [view, state] = await Promise.all([t.call("chapters", { chat_id: chatId }), t.call("state", { chat_id: chatId })]);
      if (t.chatId() === chatId) cache = { chatId, view, state, job: state.job };
    } catch (e) {
      cache = { chatId, view: null, state: null, job: null, error: e.message };
    } finally {
      loading = false;
      t.refresh();
    }
  };

  const mine = () => cache.chatId && cache.chatId === t.chatId();
  const win = t.window({
    icon: "🧠",
    title: "Память",
    button: false,
    render: (ui) => {
      const { html, useState, useEffect, Icon } = ui;
      const [tab, setTab] = useState("chapters");
      const [open, setOpen] = useState(focusId);
      const [busy, setBusy] = useState(false);
      useEffect(() => { reload(); }, []);
      useEffect(() => { if (focusId) { setOpen(focusId); focusId = ""; } });
      const chatId = t.chatId();
      if (!chatId) return html`<p class="muted">Откройте чат.</p>`;
      const view = mine() ? cache.view : null;
      const state = mine() ? cache.state : null;
      const call = async (action, payload = {}) => {
        setBusy(true);
        try {
          cache = { ...cache, view: await t.call(action, { chat_id: chatId, ...payload }) };
          t.refresh();
          reload();
        } catch (e) {
          ui.toast(e.message, "error");
        } finally {
          setBusy(false);
        }
      };
      const toggle = async () => {
        setBusy(true);
        try {
          await t.call("set_enabled", { chat_id: chatId, enabled: !(state && state.enabled) });
          await reload();
        } catch (e) {
          ui.toast(e.message, "error");
        } finally {
          setBusy(false);
        }
      };
      const job = state && state.job;
      return html`<div class="memory-window" data-slot="memory-window">
        <label class="memory-switch"><input type="checkbox" checked=${!!(state && state.enabled)} disabled=${busy || !state}
          onChange=${toggle} /> Память в этом чате</label>
        <p class="muted memory-hint">${!state ? "Загрузка…" : !state.enabled
          ? "Выключена: ничего не считается и в промпт не добавляется."
          : describe(state, job)}</p>
        <div class="tabs" role="tablist">
          <button role="tab" aria-selected=${tab === "chapters"} onClick=${() => setTab("chapters")}>Главы</button>
          <button role="tab" aria-selected=${tab === "layers"} onClick=${() => setTab("layers")}>Развитие</button>
        </div>
        ${cache.error && !view && html`<p class="muted" role="alert">${cache.error}</p>`}
        ${view && tab === "chapters" && html`<${Chapters} ui=${ui} view=${view} open=${open} setOpen=${setOpen} busy=${busy}
          call=${call} />`}
        ${tab === "layers" && html`<${Layers} ui=${ui} chatId=${chatId} tick=${layersTick} />`}
      </div>`;
    },
  });

  // «Развитие»: the layers of the open branch and its «Сейчас:» lines
  function Layers({ ui, chatId, tick }) {
    const { html, useState, useEffect } = ui;
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const load = async () => {
      try {
        const got = await t.call("layers", { chat_id: chatId });
        if (t.chatId() === chatId) { setData(got); setError(""); }
      } catch (e) {
        setError(e.message);
      }
    };
    useEffect(() => { load(); }, [chatId, tick]);
    const call = async (action, payload = {}) => {
      setBusy(true);
      try {
        const got = await t.call(action, { chat_id: chatId, ...payload });
        if (got && got.members && got.now_lines) setData(got);
        return got || {};
      } catch (e) {
        ui.toast(e.message, "error");
        return null;
      } finally {
        setBusy(false);
      }
    };
    const undo = async () => {
      if (await call("undo")) { await load(); reload(); }
    };
    if (!data) return html`<p class="muted" role=${error ? "alert" : "status"}>${error || "Загрузка…"}</p>`;
    return html`<div class="memory-chapters" data-slot="memory-layers">
      <div class="memory-toolbar">
        <button disabled=${busy || !data.undo} onClick=${undo}
          title="Отменить последнее ручное изменение">↶ ${data.undo ? "Отменить: " + data.undo : "Отменить"}</button>
      </div>
      <p class="muted memory-hint">${data.development_on
        ? `Карточка не меняется. После каждой закрытой главы модель обновляет «каким он стал сейчас» (до ${data.dev_tokens} токенов); слой идёт сразу после карточки и важнее её. Свой на каждой ветке.`
        : "«Развитие персонажей» выключено в настройках дополнения: слои не обновляются и в промпт не идут."}</p>
      <ul class="memory-list">
        ${data.members.map((m) => html`<${LayerCard} key=${m.target} ui=${ui} m=${m} busy=${busy} call=${call}
          chatId=${chatId} reloadLayers=${load} />`)}
      </ul>
      <h3 class="memory-label">Строки «Сейчас:»</h3>
      <p class="muted memory-hint">${data.now_on
        ? "Дописываются к пузырю лорбука только в этом чате и на этой ветке, и только когда пузырь идёт в ответ. Сам лорбук не меняется."
        : "«Строки «Сейчас:» для лорбуков» выключены в настройках дополнения: новые не появляются и в промпт не идут."}</p>
      ${data.now_lines.length === 0 && html`<p class="muted">Строк пока нет.</p>`}
      <ul class="memory-list">
        ${data.now_lines.map((l) => html`<${NowLine} key=${l.block_id} ui=${ui} l=${l} busy=${busy} call=${call} />`)}
      </ul>
    </div>`;
  }

  const BY = { hand: "вручную", auto: "модель", transfer: "перенесено" };

  function LayerCard({ ui, m, busy, call, chatId, reloadLayers }) {
    const { html, useState, Select } = ui;
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState(null);
    const [versions, setVersions] = useState(null);
    const [chats, setChats] = useState(null);
    const [to, setTo] = useState("");
    const text = draft ?? m.text;
    const toggleVersions = async () => {
      if (versions) { setVersions(null); return; }
      try { setVersions((await t.call("layer_versions", { chat_id: chatId, target: m.target })).versions); }
      catch (e) { ui.toast(e.message, "error"); }
    };
    const openTransfer = async () => {
      if (chats) { setChats(null); return; }
      try {
        const list = (await t.call("layer_chats", { chat_id: chatId, target: m.target })).chats;
        setChats(list);
        const first = list.find((c) => c.has_messages);
        setTo(first ? first.chat_id : "");
      } catch (e) { ui.toast(e.message, "error"); }
    };
    const send = async () => {
      const got = await call("layer_transfer", { target: m.target, to_chat_id: to });
      if (got && got.done) {
        ui.toast(`Развитие перенесено в «${got.done.title || "чат"}»${got.done.memory ? "" : " (там память выключена: включите её в том чате)"}`);
        setChats(null);
      }
    };
    const setNoLayer = async (value) => {
      if (await call("member_set", { character_id: m.target, no_layer: value })) reloadLayers();
    };
    return html`<li class="memory-chapter">
      <button class="memory-row" aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="memory-row-main"><strong>${m.is_me ? "Я · " + m.name : m.name}</strong>
          <span class="muted"> · ${m.no_layer ? "не наслаивать" : m.text ? BY[m.by] || "" : "ещё нет"}</span></span>
        ${m.locked && html`<span class="memory-state">🔒</span>`}
      </button>
      ${open && html`<div class="memory-sheet">
        <label class="memory-switch"><input type="checkbox" checked=${m.no_layer} disabled=${busy}
          onChange=${(e) => setNoLayer(e.target.checked)} /> Не наслаивать (без слоя и без запросов)</label>
        <textarea class="memory-text" rows="6" value=${text} aria-label=${"Развитие: " + m.name}
          placeholder="Слоя пока нет: он появится после закрытой главы. Можно написать свой: он будет под замком."
          onInput=${(e) => setDraft(e.target.value)}></textarea>
        <p class="muted memory-hint">Свой текст ставит замок: сами его больше не перепишем.</p>
        <div class="memory-actions">
          <button disabled=${busy || draft === null || !text.trim() || text === m.text}
            onClick=${async () => { if (await call("layer_edit", { target: m.target, text })) setDraft(null); }}>Сохранить</button>
          <button disabled=${busy || !m.text} aria-expanded=${!!versions} onClick=${toggleVersions}>Версии</button>
          <button disabled=${busy || !m.text} aria-expanded=${!!chats} onClick=${openTransfer}>Перенести в другой чат…</button>
        </div>
        ${m.text && html`<label class="memory-switch"><input type="checkbox" checked=${m.locked} disabled=${busy}
          onChange=${(e) => call("layer_lock", { target: m.target, locked: e.target.checked })} /> 🔒 Под замком</label>`}
        ${chats && html`<div class="memory-transfer">
          ${chats.length === 0 ? html`<p class="muted">Других чатов с этим персонажем нет.</p>` : html`
            <${Select} value=${to} onChange=${(e) => setTo(e.target.value)} aria-label="Чат, куда перенести">
              ${chats.map((c) => html`<option value=${c.chat_id} disabled=${!c.has_messages}>${c.title}${c.memory ? "" : " (память выключена)"}${c.has_messages ? "" : " (пусто)"}</option>`)}
            <//>
            <button disabled=${busy || !to} onClick=${send}>Перенести</button>
            <p class="muted memory-hint">Там это станет текущим развитием на открытой ветке; вернуть прежнее можно через «Версии».</p>`}
        </div>`}
        ${versions && html`<ul class="memory-versions">
          ${versions.length === 0 && html`<li class="muted">Версий нет.</li>`}
          ${versions.map((v, i) => html`<li key=${v.id}>
            <p class="muted">${new Date(v.created_at).toLocaleString()} · ${BY[v.by] || v.by}${i === 0 ? " · сейчас" : ""}</p>
            <p class="memory-preview">${v.text}</p>
            ${i > 0 && html`<button disabled=${busy} onClick=${async () => {
              if (await call("layer_restore", { target: m.target, text_id: v.id })) setVersions(null);
            }}>Вернуть эту</button>`}
          </li>`)}
        </ul>`}
      </div>`}
    </li>`;
  }

  function NowLine({ ui, l, busy, call }) {
    const { html, useState } = ui;
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState(null);
    const text = draft ?? l.line;
    const write = async () => {
      const sure = await ui.ask(`Дописать «Сейчас: ${l.line}» в пузырь «${l.title}» лорбука навсегда? Лорбук изменится во всех `
        + "чатах. Перед этим будет сделана копия данных; отменить можно в истории лорбука.", { ok: "Записать", danger: true });
      if (sure && await call("now_write", { block_id: l.block_id })) ui.toast("Записано в лорбук");
    };
    return html`<li class="memory-chapter">
      <button class="memory-row" aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="memory-row-main"><strong>${l.title || "Пузырь"}</strong>
          <span class="muted"> · ${l.book ? l.book + " · " : ""}Сейчас: ${l.line}</span></span>
        ${l.locked && html`<span class="memory-state">🔒</span>`}
      </button>
      ${open && html`<div class="memory-sheet">
        <textarea class="memory-text" rows="3" value=${text} aria-label=${"Сейчас: " + l.title}
          onInput=${(e) => setDraft(e.target.value)}></textarea>
        <div class="memory-actions">
          <button disabled=${busy || draft === null || !text.trim() || text === l.line}
            onClick=${async () => { if (await call("now_edit", { block_id: l.block_id, text })) setDraft(null); }}>Сохранить</button>
          <button class="danger-text" disabled=${busy} onClick=${() => call("now_delete", { block_id: l.block_id })}>Удалить</button>
          <button disabled=${busy} onClick=${write}>Записать в лорбук насовсем</button>
        </div>
      </div>`}
    </li>`;
  }

  function describe(state, job) {
    const parts = [];
    if (!state.connected) parts.push("нет подключения для векторов (Настройки → Промпт): отрывки ищутся только по словам");
    else parts.push(`векторы: ${state.vectors} из ${state.pieces} отрывков`);
    if (job && job.paused) parts.push("пауза: идёт ваш запрос");
    else if (job && job.running) parts.push("работаю в фоне…");
    if (job && job.error) parts.push(job.error);
    return parts.join(". ");
  }

  function Chapters({ ui, view, open, setOpen, busy, call }) {
    const { html, useState } = ui;
    const [number, setNumber] = useState("");
    const [title, setTitle] = useState({});
    const n = parseInt(number, 10);
    const hasNumber = Number.isInteger(n) && n > 0;
    const ask = async (text, ok) => ui.ask(text, { ok, danger: true });
    const chip = { raw: view.summaries_on ? "без пересказа" : "", summarized: "пересказ", stale: "устарела",
      failed: "ошибка", deleted: "удалена" };
    return html`<div class="memory-chapters">
      <div class="memory-toolbar">
        <button disabled=${busy || !view.undo} onClick=${() => call("undo")}
          title="Отменить последнее ручное изменение глав">↶ ${view.undo ? "Отменить: " + view.undo : "Отменить"}</button>
      </div>
      <${Plot} ui=${ui} view=${view} busy=${busy} call=${call} />
      <${Members} ui=${ui} view=${view} busy=${busy} />
      ${view.chapters.length === 0 && html`<p class="muted">Глав пока нет. Глава закрывается, когда после неё накопилось больше
        ${view.size} сообщений и нашлась граница сцены (или втрое больше). Сообщений без главы: ${view.open.count}.</p>`}
      <ul class="memory-list">
        ${view.chapters.map((c) => html`<li key=${c.id} class=${"memory-chapter " + c.state} data-state=${c.state}>
          <button class="memory-row" aria-expanded=${open === c.id} onClick=${() => setOpen(open === c.id ? "" : c.id)}>
            <span class="memory-row-main"><strong>${c.number ? "Глава " + c.number : "Глава (удалена)"}</strong>
              <span class="muted"> · №${c.first_no}–№${c.last_no}${c.title ? " · " + c.title : ""}</span></span>
            ${(chip[c.state] || c.locked) && html`<span class="memory-state">${c.locked ? "🔒 " : ""}${chip[c.state] || ""}</span>`}
          </button>
          ${open === c.id && html`<div class="memory-sheet">
            <p class="muted">${c.count} сообщений. Граница: ${c.reason || "—"}${c.by === "hand" ? " (вручную)" : c.by === "model" ? " (модель)" : ""}.</p>
            <p class="memory-preview">${c.preview}</p>
            <${Summary} ui=${ui} c=${c} busy=${busy} call=${call} />
            <div class="memory-field">
              <input value=${title[c.id] ?? c.title} placeholder="Название главы" aria-label="Название главы"
                onInput=${(e) => setTitle({ ...title, [c.id]: e.target.value })} />
              <button disabled=${busy || (title[c.id] ?? c.title) === c.title}
                onClick=${() => call("edit", { chapter_id: c.id, title: title[c.id] })}>Сохранить</button>
            </div>
            <label class="memory-switch"><input type="checkbox" checked=${c.whole} disabled=${busy}
              onChange=${(e) => call("edit", { chapter_id: c.id, whole: e.target.checked })} />
              Вся глава в ответ, когда найдена</label>
            ${c.state === "stale" && html`<button disabled=${busy} onClick=${() => call("accept", { chapter_id: c.id })}>
              Принять изменения текста</button>`}
            <div class="memory-field">
              <input type="text" inputmode="numeric" value=${number} placeholder="№ сообщения" aria-label="Номер сообщения"
                onInput=${(e) => setNumber(e.target.value.replace(/\D/g, ""))} />
            </div>
            <div class="memory-actions">
              <button disabled=${busy || !hasNumber || c.state === "deleted"}
                onClick=${() => call("split", { number: n })}>Разрезать перед №</button>
              <button disabled=${busy || !hasNumber || c.state === "deleted"}
                onClick=${() => call("move_border", { chapter_id: c.id, number: n })}>Начало главы — с №</button>
              <button disabled=${busy || c.state === "deleted"}
                onClick=${() => call("merge", { chapter_id: c.id })}>Склеить со следующей</button>
              ${c.state === "deleted"
                ? html`<button disabled=${busy} onClick=${() => call("restore", { chapter_id: c.id })}>Вернуть</button>`
                : html`<button class="danger-text" disabled=${busy} onClick=${async () => {
                  if (await ask("Удалить главу? Её можно вернуть.", "Удалить")) call("remove", { chapter_id: c.id });
                }}>Удалить</button>`}
            </div>
          </div>`}
        </li>`)}
      </ul>
      ${view.chapters.length > 0 && html`<p class="muted">Без главы: ${view.open.count} сообщений (последние).</p>`}
    </div>`;
  }

  // a chapter's summary: text (saving it by hand puts a lock), lock, «Пересказать заново», versions
  function Summary({ ui, c, busy, call }) {
    const { html, useState } = ui;
    const [draft, setDraft] = useState(null);
    const [versions, setVersions] = useState(null);
    const text = draft ?? c.summary;
    const loadVersions = async () => {
      try {
        setVersions((await t.call("summary_versions", { chat_id: t.chatId(), chapter_id: c.id })).versions);
      } catch (e) {
        ui.toast(e.message, "error");
      }
    };
    if (c.state === "deleted") return null;
    return html`<div class="memory-summary">
      <label class="memory-label" for=${"sum-" + c.id}>Пересказ${c.summary_by === "hand" ? " (вручную)" : ""}</label>
      ${c.error && html`<p class="muted" role="status">Не вышло (попыток: ${c.tries}): ${c.error}</p>`}
      <textarea id=${"sum-" + c.id} class="memory-text" rows="5" value=${text}
        placeholder=${c.summary ? "" : "Пересказа пока нет. Можно написать свой: он будет под замком."}
        onInput=${(e) => setDraft(e.target.value)}></textarea>
      <p class="muted memory-hint">Свой текст ставит замок: сами его больше не перепишем.</p>
      <div class="memory-actions">
        <button disabled=${busy || draft === null || !text.trim() || text === c.summary}
          onClick=${async () => { await call("summary_edit", { chapter_id: c.id, text }); setDraft(null); }}>Сохранить</button>
        <button disabled=${busy || c.locked}
          title=${c.locked ? "Сначала снимите замок" : "Пересказать эту главу моделью ещё раз"}
          onClick=${() => call("resummarize", { chapter_id: c.id })}>Пересказать заново</button>
        <button disabled=${busy} aria-expanded=${!!versions}
          onClick=${() => (versions ? setVersions(null) : loadVersions())}>Версии</button>
      </div>
      ${c.summary && html`<label class="memory-switch"><input type="checkbox" checked=${c.locked} disabled=${busy}
        onChange=${(e) => call("summary_lock", { chapter_id: c.id, locked: e.target.checked })} />
        🔒 Под замком (сами не переписываем)</label>`}
      ${versions && html`<ul class="memory-versions">
        ${versions.length === 0 && html`<li class="muted">Версий нет.</li>`}
        ${versions.map((v) => html`<li key=${v.id}>
          <p class="muted">${new Date(v.created_at).toLocaleString()} · ${v.by === "hand" ? "вручную" : "модель"}${v.current ? " · сейчас" : ""}</p>
          <p class="memory-preview">${v.text}</p>
          ${!v.current && html`<button disabled=${busy} onClick=${async () => {
            await call("summary_restore", { text_id: v.id }); setVersions(null);
          }}>Вернуть эту</button>`}
        </li>`)}
      </ul>`}
    </div>`;
  }

  // «Сюжет до сих пор» of the open branch
  function Plot({ ui, view, busy, call }) {
    const { html, useState } = ui;
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState(null);
    const [versions, setVersions] = useState(null);
    const plot = view.plot;
    const text = draft ?? (plot ? plot.text : "");
    const toggleVersions = async () => {
      if (versions) { setVersions(null); return; }
      try { setVersions((await t.call("plot_versions", { chat_id: t.chatId() })).versions); }
      catch (e) { ui.toast(e.message, "error"); }
    };
    return html`<section class="memory-plot memory-chapter">
      <button class="memory-row" aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="memory-row-main"><strong>Сюжет до сих пор</strong>
          <span class="muted"> · ${plot ? (plot.locked ? "🔒 " : "") + (plot.by === "hand" ? "вручную" : "по пересказам") : "ещё нет"}</span></span>
      </button>
      ${open && html`<div class="memory-sheet">
        <p class="muted">Идёт в промпт, когда что-то не влезло в окно модели; ещё подставляется в {{summary}}.${view.plot_on ? ""
          : " Сам он не собирается: включите «Сюжет до сих пор» в настройках дополнения или нажмите «Пересобрать»."}</p>
        <textarea class="memory-text" rows="6" value=${text} aria-label="Сюжет до сих пор"
          placeholder="Сюжета пока нет: он собирается из пересказов глав."
          onInput=${(e) => setDraft(e.target.value)}></textarea>
        <div class="memory-actions">
          <button disabled=${busy || draft === null || !text.trim()}
            onClick=${async () => { await call("plot_edit", { text }); setDraft(null); }}>Сохранить</button>
          <button disabled=${busy || !!(plot && plot.locked)} onClick=${() => call("plot_rebuild")}
            title=${plot && plot.locked ? "Сначала снимите замок" : "Собрать заново из пересказов глав"}>Пересобрать</button>
          <button disabled=${busy || !plot} aria-expanded=${!!versions} onClick=${toggleVersions}>Версии</button>
        </div>
        ${plot && html`<label class="memory-switch"><input type="checkbox" checked=${plot.locked} disabled=${busy}
          onChange=${(e) => call("plot_lock", { locked: e.target.checked })} /> 🔒 Под замком</label>`}
        ${versions && html`<ul class="memory-versions">${versions.map((v) => html`<li key=${v.id}>
          <p class="muted">${new Date(v.created_at).toLocaleString()} · ${v.by === "hand" ? "вручную" : "модель"}</p>
          <p class="memory-preview">${v.text}</p>
          ${plot && v.id !== plot.id && html`<button disabled=${busy} onClick=${async () => {
            await call("plot_restore", { text_id: v.id }); setVersions(null);
          }}>Вернуть эту</button>`}
        </li>`)}</ul>`}
      </div>`}
    </section>`;
  }

  // «Участники»: per member «Помнит всё» and «Не наслаивать»
  function Members({ ui, view, busy }) {
    const { html, useState } = ui;
    const [open, setOpen] = useState(false);
    const [members, setMembers] = useState(null);
    const list = members || view.members || [];
    if (list.length === 0) return null;
    const set = async (m, key, value) => {
      try {
        setMembers((await t.call("member_set", { chat_id: t.chatId(), character_id: m.character_id, [key]: value })).members);
      } catch (e) {
        ui.toast(e.message, "error");
      }
    };
    return html`<section class="memory-chapter">
      <button class="memory-row" aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="memory-row-main"><strong>Участники</strong><span class="muted"> · ${list.length}</span></span>
      </button>
      ${open && html`<div class="memory-sheet">
        <p class="muted">В групповом чате персонаж получает только главы, где он был. «Помнит всё»: все главы
          (например, рассказчику). «Не наслаивать»: без слоя «Развитие» и без запросов о нём.</p>
        ${list.map((m) => html`<div key=${m.character_id} class="memory-member">
          <strong>${m.name}</strong>
          <label class="memory-switch"><input type="checkbox" checked=${m.remembers_all} disabled=${busy}
            onChange=${(e) => set(m, "remembers_all", e.target.checked)} /> Помнит всё</label>
          <label class="memory-switch"><input type="checkbox" checked=${m.no_layer} disabled=${busy}
            onChange=${(e) => set(m, "no_layer", e.target.checked)} /> Не наслаивать</label>
        </div>`)}
      </div>`}
    </section>`;
  }

  // chapter borders above messages
  t.messageDivider((message) => {
    if (!mine() || !cache.view) return null;
    const c = cache.view.chapters.find((x) => x.first_id === message.id && x.state !== "deleted");
    if (!c) return null;
    return {
      label: `Глава ${c.number}${c.title ? " · " + c.title : ""}`,
      title: `${c.reason || "граница главы"}. Нажмите, чтобы открыть главу`,
      state: c.state === "stale" ? "stale" : "",
      onClick: () => { focusId = c.id; win.open(); },
    };
  });

  t.messageMenu({
    label: "Новая глава отсюда",
    icon: "🧠",
    show: (message) => !!(mine() && cache.state && cache.state.enabled) && message.role !== "system"
      && !(cache.view && cache.view.chapters.some((c) => c.first_id === message.id && c.state !== "deleted")),
    onClick: async (message) => {
      try {
        const view = await t.call("split", { chat_id: t.chatId(), message_id: message.id });
        cache = { ...cache, view };
        t.toast("Новая глава начинается здесь. Отменить: окно «Память» → ↶");
        reload();
      } catch (e) {
        t.toast(e.message);
      }
    },
  });

  // the progress chip
  t.status(() => {
    const job = mine() && cache.state && cache.state.enabled ? cache.job : null;
    if (!job || !(job.running || job.queued || job.error)) return null;
    const text = job.error ? "Память: ошибка" : job.paused ? "Память: пауза"
      : job.total ? `Память: ${job.done}/${job.total}${job.note ? " · " + job.note : ""}` : "Память: считаю…";
    return { text, title: job.error || "Фоновая работа памяти. Нажмите, чтобы открыть окно", state: job.error ? "error" : "wait",
      onClick: () => win.open() };
  });

  t.on("job", (event) => {
    if (event.owner !== t.id || event.key !== "chat:" + t.chatId()) return;
    cache = { ...cache, chatId: t.chatId(), job: event };
    if (cache.state) cache = { ...cache, state: { ...cache.state, job: event } };
    t.refresh();
    if (!event.running && !event.queued) reload();
  });
  t.on("chat", () => { cache = { chatId: "", view: null, state: null, job: null }; reload(); });
  t.on("message", () => { setTimeout(reload, 2500); });
  t.onNotice("chapters", () => reload());
  t.onNotice("layers", (data) => {
    if (!data || data.chat_id === t.chatId()) { layersTick += 1; t.refresh(); }
  });

  t.composerButton({
    icon: "🧠",
    title: "Память++: окно памяти этого чата",
    show: () => !!t.chatId(),
    onClick: () => { if (t.chatId()) win.open(); },
  });
  reload();
}
