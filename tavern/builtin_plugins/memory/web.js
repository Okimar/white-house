// 🧠 Память++ (stages M1-M6): the composer button opens the «Память» window; its «Главы» tab lists the chapters of
// the open branch (split, merge, move the border, delete, restore, undo) with their summaries (edit = lock, lock,
// «Пересказать заново», versions), «Сюжет до сих пор» on top, and a folded «Участники» list (M3). Chapter borders are thin lines above
// messages, «Новая глава отсюда» is in a message's «⋯» menu, and a chip in the top bar shows the background work.
// M4: the «Развитие» tab: a card per character of the chat and «Я» (the layer «how they are now»: edit = lock, lock,
// versions, «Перенести в другой чат…», «Не наслаивать»), and the branch's «Сейчас:» lines of lorebook bubbles (edit,
// «Удалить», «Записать в лорбук насовсем»).
// M5: the «Слово и нити» tab: records of the open branch grouped by who gave the word, with status chips, a filter by
// status and kind, the record sheet (quote, kind, to whom, importance, who knows a secret: saving locks it; status;
// lock; «Удалить» / «Вернуть»; «→ к сообщению №N» closes the window and shows the quoted message) and «+ Добавить».
// M6: the «Лица» tab: mini-cards of side characters (this chat's, or all with a search): name, aliases and card (saving
// locks it), ★ «Любимое», «Использовать в этом чате», lock, versions, «Объединить с…», «Сделать настоящим персонажем»
// (asks first, then opens the new card through t.openCharacter), «Удалить» / «Вернуть».
// M7: «Разобрать весь чат…» (the price first, then progress). M8: the «Документы» tab (add a file, price, attach to this chat,
// chapters with «Вся глава», delete).
export default function setup(t) {
  let cache = { chatId: "", view: null, state: null, job: null };
  let layersTick = 0; // bumped by the notice «layers»: the «Развитие» tab loads again
  let threadsTick = 0; // bumped by the notice «threads»: the «Слово и нити» tab loads again
  let facesTick = 0; // bumped by the notice «faces»: the «Лица» tab loads again
  let docsTick = 0; // bumped by the notice «docs»: the «Документы» tab loads again
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
        ${state && html`<${Bulk} ui=${ui} chatId=${chatId} state=${state} busy=${busy} setBusy=${setBusy} />`}
        <div class="tabs" role="tablist">
          <button role="tab" aria-selected=${tab === "chapters"} onClick=${() => setTab("chapters")}>Главы</button>
          <button role="tab" aria-selected=${tab === "layers"} onClick=${() => setTab("layers")}>Развитие</button>
          <button role="tab" aria-selected=${tab === "threads"} onClick=${() => setTab("threads")}>Слово и нити</button>
          <button role="tab" aria-selected=${tab === "faces"} onClick=${() => setTab("faces")}>Лица</button>
          <button role="tab" aria-selected=${tab === "docs"} onClick=${() => setTab("docs")}>Документы</button>
        </div>
        ${cache.error && !view && html`<p class="muted" role="alert">${cache.error}</p>`}
        ${view && tab === "chapters" && html`<${Chapters} ui=${ui} view=${view} open=${open} setOpen=${setOpen} busy=${busy}
          call=${call} />`}
        ${tab === "layers" && html`<${Layers} ui=${ui} chatId=${chatId} tick=${layersTick} />`}
        ${tab === "threads" && html`<${Threads} ui=${ui} chatId=${chatId} tick=${threadsTick} />`}
        ${tab === "faces" && html`<${Faces} ui=${ui} chatId=${chatId} tick=${facesTick} />`}
        ${tab === "docs" && html`<${Docs} ui=${ui} chatId=${chatId} tick=${docsTick} />`}
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
        <span class="memory-row-main"><strong>${m.is_me ? "Я · " + m.name : m.is_face ? "Лицо · " + m.name : m.name}</strong>
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

  // «Слово и нити»: records of the open branch, grouped by who gave the word
  function Threads({ ui, chatId, tick }) {
    const { html, useState, useEffect, Select } = ui;
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [status, setStatus] = useState("open");
    const [kind, setKind] = useState("");
    const [adding, setAdding] = useState(false);
    const load = async () => {
      try {
        const got = await t.call("threads", { chat_id: chatId });
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
        if (got && got.groups) setData(got);
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
    const shown = (i) => (status === "deleted" ? i.deleted : !i.deleted && (status === "all"
      || (status === "open" ? i.status === "active" : i.status === status))) && (!kind || i.kind === kind);
    const groups = data.groups.map((g) => ({ ...g, items: g.items.filter(shown) })).filter((g) => g.items.length);
    return html`<div class="memory-chapters" data-slot="memory-threads">
      <div class="memory-toolbar">
        <button disabled=${busy || !data.undo} onClick=${undo}
          title="Отменить последнее ручное изменение">↶ ${data.undo ? "Отменить: " + data.undo : "Отменить"}</button>
        <button aria-expanded=${adding} onClick=${() => setAdding(!adding)}>+ Добавить</button>
      </div>
      <p class="muted memory-hint">${data.on
        ? "После каждой закрытой главы модель выписывает, кто что обещал, в чём клялся, что сказал о себе или о мире, кто кому должен и какие тайны доверил, и отмечает, что выполнено или нарушено. Персонаж получает свои клятвы всегда, остальное — когда к месту; тайну — только если знает. Своё на каждой ветке."
        : "«Слово и нити» выключены в настройках дополнения: новые записи не появляются и в промпт не идут."}</p>
      ${adding && html`<${ThreadAdd} ui=${ui} data=${data} busy=${busy} call=${call} done=${() => setAdding(false)} />`}
      <div class="memory-filter">
        <${Select} value=${status} onChange=${(e) => setStatus(e.target.value)} aria-label="Какие записи показать">
          <option value="open">В силе</option>
          <option value="all">Все</option>
          ${data.statuses.filter(([s]) => s !== "active").map(([s, label]) => html`<option value=${s}>${label}</option>`)}
          <option value="deleted">Удалённые</option>
        <//>
        <${Select} value=${kind} onChange=${(e) => setKind(e.target.value)} aria-label="Вид записей">
          <option value="">Любые</option>
          ${data.kinds.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
        <//>
      </div>
      ${groups.length === 0 && html`<p class="muted">${data.groups.length ? "Под этот выбор записей нет." : "Записей пока нет."}</p>`}
      ${groups.map((g) => html`<section key=${g.who}>
        <h3 class="memory-label">${g.is_me ? "Я · " + g.name : g.name}</h3>
        <ul class="memory-list">
          ${g.items.map((i) => html`<${ThreadItem} key=${i.id} ui=${ui} i=${i} data=${data} busy=${busy} call=${call} />`)}
        </ul>
      </section>`)}
    </div>`;
  }

  const IMPORTANCE = [[1, "мелочь"], [2, "важно"], [3, "ключевое"]];

  function PersonSelect({ ui, people, value, onChange, label, anyone }) {
    const { html, Select } = ui;
    return html`<${Select} value=${value} onChange=${(e) => onChange(e.target.value)} aria-label=${label}>
      ${anyone && html`<option value="">всем / миру</option>`}
      ${people.map((p) => html`<option value=${p.target}>${p.is_me ? "Я · " + p.name : p.is_face ? "Лицо · " + p.name : p.name}</option>`)}
    <//>`;
  }

  function ThreadItem({ ui, i, data, busy, call }) {
    const { html, useState, Select } = ui;
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState({});
    const value = (key) => (key in draft ? draft[key] : i[key]);
    const set = (key, v) => setDraft({ ...draft, [key]: v });
    const differs = (k) => JSON.stringify(draft[k]) !== JSON.stringify(i[k]);
    const changed = Object.keys(draft).some(differs);
    const save = async () => {
      const payload = {};
      for (const k of Object.keys(draft)) if (differs(k)) payload[k] = draft[k];
      if (await call("thread_edit", { thread_id: i.id, ...payload })) setDraft({});
    };
    const knows = value("knows");
    const toggleKnows = (target, on) => set("knows", on ? [...knows, target] : knows.filter((k) => k !== target));
    const show = () => {
      win.close();
      t.showMessage({ id: i.message_id, depth: i.message_no - 1 });
    };
    return html`<li class=${"memory-chapter" + (i.deleted ? " deleted" : "")}>
      <button class="memory-row" aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="memory-row-main"><strong>${i.kind_label}</strong>${i.to_name ? " → " + i.to_name : ""}
          <span class="muted"> · «${i.quote}»</span></span>
        <span class="memory-state">${i.locked ? "🔒 " : ""}${i.deleted ? "удалена" : i.status_label}</span>
      </button>
      ${open && html`<div class="memory-sheet">
        ${i.message_no && i.message_id && html`<button class="memory-link" onClick=${show}>→ к сообщению №${i.message_no}</button>`}
        <label class="memory-label" for=${"thr-" + i.id}>Цитата</label>
        <textarea id=${"thr-" + i.id} class="memory-text" rows="3" value=${value("quote")}
          onInput=${(e) => set("quote", e.target.value)}></textarea>
        <div class="memory-grid">
          <label>Вид<${Select} value=${value("kind")} onChange=${(e) => set("kind", e.target.value)} aria-label="Вид записи">
            ${data.kinds.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
          <//></label>
          <label>Кому<${PersonSelect} ui=${ui} people=${data.people} value=${value("to_whom")} anyone=${true}
            onChange=${(v) => set("to_whom", v)} label="Кому дано слово" /></label>
          <label>Важность<${Select} value=${String(value("importance"))} aria-label="Важность"
            onChange=${(e) => set("importance", Number(e.target.value))}>
            ${IMPORTANCE.map(([n, label]) => html`<option value=${String(n)}>${label}</option>`)}
          <//></label>
        </div>
        ${i.kind === "secret" && html`<fieldset class="memory-knows"><legend>Знают тайну</legend>
          ${data.people.map((p) => html`<label key=${p.target} class="memory-switch"><input type="checkbox"
            checked=${knows.includes(p.target)} disabled=${busy} onChange=${(e) => toggleKnows(p.target, e.target.checked)} />
            ${p.is_me ? "Я · " + p.name : p.name}</label>`)}
        </fieldset>`}
        <p class="muted memory-hint">Своя правка ставит замок: модель эту запись больше не меняет.</p>
        <div class="memory-actions">
          <button disabled=${busy || !changed} onClick=${save}>Сохранить</button>
          ${i.deleted
            ? html`<button disabled=${busy} onClick=${() => call("thread_restore", { thread_id: i.id })}>Вернуть</button>`
            : html`<button class="danger-text" disabled=${busy} onClick=${() => call("thread_delete", { thread_id: i.id })}>Удалить</button>`}
        </div>
        <label class="memory-grid-label">Статус на этой ветке
          <${Select} value=${i.status} aria-label="Статус на этой ветке"
            onChange=${(e) => e.target.value !== i.status && call("thread_status", { thread_id: i.id, status: e.target.value })}>
            ${data.statuses.map(([s, label]) => html`<option value=${s}>${label}</option>`)}
          <//></label>
        <label class="memory-switch"><input type="checkbox" checked=${i.locked} disabled=${busy}
          onChange=${(e) => call("thread_lock", { thread_id: i.id, locked: e.target.checked })} /> 🔒 Под замком</label>
        ${i.history.length > 0 && html`<ul class="memory-versions">
          ${i.history.map((h) => html`<li key=${h.created_at + h.status}><p class="muted">
            ${new Date(h.created_at).toLocaleString()} · ${h.status_label}${h.message_no ? " · №" + h.message_no : ""}
            · ${h.by === "hand" ? "вручную" : "модель"}${h.note ? " · " + h.note : ""}</p></li>`)}
        </ul>`}
      </div>`}
    </li>`;
  }

  function ThreadAdd({ ui, data, busy, call, done }) {
    const { html, useState, Select } = ui;
    const [kind, setKind] = useState("promise");
    const [who, setWho] = useState(data.people[0] ? data.people[0].target : "");
    const [to, setTo] = useState("");
    const [quote, setQuote] = useState("");
    const [number, setNumber] = useState("");
    const add = async () => {
      const n = parseInt(number, 10);
      const payload = { kind, who, to_whom: to, quote, ...(Number.isInteger(n) && n > 0 ? { number: n } : {}) };
      if (await call("thread_add", payload)) done();
    };
    return html`<div class="memory-sheet memory-chapter">
      <div class="memory-grid">
        <label>Вид<${Select} value=${kind} onChange=${(e) => setKind(e.target.value)} aria-label="Вид записи">
          ${data.kinds.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
        <//></label>
        <label>Кто<${PersonSelect} ui=${ui} people=${data.people} value=${who} onChange=${setWho} label="Кто дал слово" /></label>
        <label>Кому<${PersonSelect} ui=${ui} people=${data.people} value=${to} onChange=${setTo} anyone=${true}
          label="Кому дано слово" /></label>
      </div>
      <textarea class="memory-text" rows="3" value=${quote} aria-label="Цитата" placeholder="Точные слова из чата"
        onInput=${(e) => setQuote(e.target.value)}></textarea>
      <div class="memory-field">
        <input type="text" inputmode="numeric" value=${number} placeholder="№ сообщения (можно пусто)"
          aria-label="Номер сообщения" onInput=${(e) => setNumber(e.target.value.replace(/\D/g, ""))} />
        <button disabled=${busy || !who || quote.trim().length < 3} onClick=${add}>Добавить</button>
      </div>
    </div>`;
  }

  // «Лица»: mini-cards of side characters (app-wide; this chat's by default, all of them with a search)
  function Faces({ ui, chatId, tick }) {
    const { html, useState, useEffect, Select } = ui;
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [all, setAll] = useState(false);
    const [query, setQuery] = useState("");
    const load = async () => {
      try {
        const got = await t.call("faces", { chat_id: chatId, all, query });
        if (t.chatId() === chatId) { setData(got); setError(""); }
      } catch (e) {
        setError(e.message);
      }
    };
    useEffect(() => {
      const timer = setTimeout(load, query ? 250 : 0); // typing in the search waits for a pause
      return () => clearTimeout(timer);
    }, [chatId, tick, all, query]);
    const call = async (action, payload = {}) => {
      setBusy(true);
      try {
        const got = await t.call(action, { chat_id: chatId, all, query, ...payload });
        if (got && got.faces) setData(got);
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
    return html`<div class="memory-chapters" data-slot="memory-faces">
      <div class="memory-toolbar">
        <button disabled=${busy || !data.undo} onClick=${undo}
          title="Отменить последнее ручное изменение">↶ ${data.undo ? "Отменить: " + data.undo : "Отменить"}</button>
      </div>
      <p class="muted memory-hint">${data.on
        ? `Персонажи без карточки, которые встречаются снова (трактирщик, стражник). После каждой закрытой главы модель пишет и дополняет их мини-карточку (до ${data.tokens} токенов). В ответ она идёт, когда лицо названо в последних сообщениях или подходит по смыслу. «★ Любимое» — и в других чатах, когда его там назовут.`
        : "«Второстепенные лица» выключены в настройках дополнения: новые мини-карточки не появляются и в промпт не идут."}</p>
      <div class="memory-filter">
        <input type="search" class="memory-search" value=${query} placeholder="Поиск по имени и тексту"
          aria-label="Поиск лиц" onInput=${(e) => setQuery(e.target.value)} />
        <${Select} value=${all ? "all" : "chat"} onChange=${(e) => setAll(e.target.value === "all")} aria-label="Какие лица показать">
          <option value="chat">В этом чате</option>
          <option value="all">Все лица (${data.total})</option>
        <//>
      </div>
      ${data.faces.length === 0 && html`<p class="muted">${query ? "Ничего не нашлось." : all ? "Лиц пока нет."
        : "В этом чате лиц пока нет. Они появляются после закрытых глав, когда второстепенный персонаж встречается снова."}</p>`}
      <ul class="memory-list">
        ${data.faces.map((f) => html`<${FaceItem} key=${f.id} ui=${ui} f=${f} faces=${data.faces} busy=${busy} call=${call} />`)}
      </ul>
    </div>`;
  }

  function FaceItem({ ui, f, faces, busy, call }) {
    const { html, useState, Select } = ui;
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState({});
    const [versions, setVersions] = useState(null);
    const [into, setInto] = useState("");
    const aliasText = (list) => list.join(", ");
    const original = (key) => (key === "aliases" ? aliasText(f.aliases) : f[key]);
    const value = (key) => (key in draft ? draft[key] : original(key));
    const set = (key, v) => setDraft({ ...draft, [key]: v });
    const changed = Object.keys(draft).some((k) => draft[k] !== original(k));
    const others = faces.filter((x) => x.id !== f.id && !x.deleted);
    const save = async () => {
      const payload = {};
      if ("name" in draft) payload.name = draft.name;
      if ("card" in draft) payload.card = draft.card;
      if ("aliases" in draft) payload.aliases = draft.aliases.split(",").map((a) => a.trim()).filter(Boolean);
      if (await call("face_edit", { face_id: f.id, ...payload })) setDraft({});
    };
    const toggleVersions = async () => {
      if (versions) { setVersions(null); return; }
      try { setVersions((await t.call("face_versions", { chat_id: t.chatId(), face_id: f.id })).versions); }
      catch (e) { ui.toast(e.message, "error"); }
    };
    const merge = async () => {
      const target = others.find((x) => x.id === into);
      if (!target) return;
      const sure = await ui.ask(`Объединить «${f.name}» с «${target.name}»? Останется одна мини-карточка «${target.name}»: `
        + "имя и другие имена этой станут её другими именами, связи с чатами, развитие и записи «Слова и нитей» перейдут к ней. "
        + "Отменить можно кнопкой ↶.", { ok: "Объединить", danger: true });
      if (sure && await call("face_merge", { face_id: f.id, into_id: target.id })) setInto("");
    };
    const makeReal = async () => {
      const sure = await ui.ask(`Сделать «${f.name}» настоящим персонажем? Появится карточка персонажа с этим именем и текстом `
        + "мини-карточки в описании; мини-карточка останется и будет с ней связана.", { ok: "Сделать" });
      if (!sure) return;
      const got = await call("face_make_real", { face_id: f.id });
      if (got && got.made) {
        ui.toast(`Персонаж «${got.made.name}» создан`);
        win.close();
        t.openCharacter(got.made.character_id);
      }
    };
    const chips = [f.favourite && "★", f.locked && "🔒", f.deleted ? "удалено" : !f.in_use && "не в этом чате",
      f.character_name && "персонаж"].filter(Boolean).join(" ");
    return html`<li class=${"memory-chapter" + (f.deleted ? " deleted" : "")}>
      <button class="memory-row" aria-expanded=${open} onClick=${() => setOpen(!open)}>
        <span class="memory-row-main"><strong>${f.name}</strong>
          <span class="muted">${f.aliases.length ? " · " + f.aliases.join(", ") : ""} · ${f.card}</span></span>
        ${chips && html`<span class="memory-state">${chips}</span>`}
      </button>
      ${open && html`<div class="memory-sheet">
        <p class="muted">${f.by === "hand" ? "вручную" : "модель"} · чатов: ${f.chats}${f.first_no ? " · здесь впервые №" + f.first_no : ""}${f.character_name ? " · настоящий персонаж: " + f.character_name : ""}</p>
        <div class="memory-grid">
          <label>Имя<input value=${value("name")} aria-label="Имя" onInput=${(e) => set("name", e.target.value)} /></label>
          <label>Другие имена<input value=${value("aliases")} aria-label="Другие имена через запятую"
            placeholder="через запятую" onInput=${(e) => set("aliases", e.target.value)} /></label>
        </div>
        <textarea class="memory-text" rows="5" value=${value("card")} aria-label=${"Мини-карточка: " + f.name}
          onInput=${(e) => set("card", e.target.value)}></textarea>
        <p class="muted memory-hint">Своя правка ставит замок: модель эту карточку больше не меняет.</p>
        <div class="memory-actions">
          <button disabled=${busy || !changed || !String(value("name")).trim() || !String(value("card")).trim()}
            onClick=${save}>Сохранить</button>
          <button disabled=${busy} aria-expanded=${!!versions} onClick=${toggleVersions}>Версии</button>
          ${f.character_id
            ? html`<button disabled=${busy} onClick=${() => { win.close(); t.openCharacter(f.character_id); }}>Открыть персонажа</button>`
            : html`<button disabled=${busy || f.deleted} onClick=${makeReal}>Сделать настоящим персонажем</button>`}
          ${f.deleted
            ? html`<button disabled=${busy} onClick=${() => call("face_undelete", { face_id: f.id })}>Вернуть</button>`
            : html`<button class="danger-text" disabled=${busy} onClick=${() => call("face_delete", { face_id: f.id })}>Удалить</button>`}
        </div>
        <label class="memory-switch"><input type="checkbox" checked=${f.favourite} disabled=${busy}
          onChange=${(e) => call("face_favourite", { face_id: f.id, favourite: e.target.checked })} /> ★ Любимое: во всех чатах, где его назовут</label>
        <label class="memory-switch"><input type="checkbox" checked=${f.in_use} disabled=${busy}
          onChange=${(e) => call("face_use", { face_id: f.id, in_use: e.target.checked })} /> Использовать в этом чате</label>
        <label class="memory-switch"><input type="checkbox" checked=${f.locked} disabled=${busy}
          onChange=${(e) => call("face_lock", { face_id: f.id, locked: e.target.checked })} /> 🔒 Под замком</label>
        ${others.length > 0 && html`<div class="memory-transfer">
          <${Select} value=${into} onChange=${(e) => setInto(e.target.value)} aria-label="С каким лицом объединить">
            <option value="">Объединить с…</option>
            ${others.map((x) => html`<option value=${x.id}>${x.name}</option>`)}
          <//>
          <button disabled=${busy || !into} onClick=${merge}>Объединить</button>
        </div>`}
        ${versions && html`<ul class="memory-versions">
          ${versions.length === 0 && html`<li class="muted">Версий нет.</li>`}
          ${versions.map((v, i) => html`<li key=${v.id}>
            <p class="muted">${new Date(v.created_at).toLocaleString()} · ${v.by === "hand" ? "вручную" : "модель"}${i === 0 ? " · сейчас" : ""}</p>
            <p class="memory-preview"><strong>${v.name}</strong>${v.aliases.length ? " (" + v.aliases.join(", ") + ")" : ""}: ${v.card}</p>
            ${i > 0 && html`<button disabled=${busy} onClick=${async () => {
              if (await call("face_restore", { face_id: f.id, version_id: v.id })) setVersions(null);
            }}>Вернуть эту</button>`}
          </li>`)}
        </ul>`}
      </div>`}
    </li>`;
  }

  // M7: «Разобрать весь чат»: the price first, then the work in the background with progress.
  function Help({ ui, text }) {
    const { html } = ui;
    return html`<details class="plugin-help"><summary>Что это?</summary><div class="muted">${text}</div></details>`;
  }

  function priceLines(e) {
    const lines = [];
    if (e.steps) for (const s of e.steps) lines.push(`${s.name}: ${s.count} запр.`);
    if (e.embed_tokens) lines.push(`векторы: ${e.embed_tokens} токенов`);
    return lines;
  }

  function Bulk({ ui, chatId, state, busy, setBusy }) {
    const { html, useState, useEffect } = ui;
    const [info, setInfo] = useState(null);
    const load = async () => {
      try { const got = await t.call("bulk_state", { chat_id: chatId }); if (t.chatId() === chatId) setInfo(got); } catch (e) { /* the line stays empty */ }
    };
    useEffect(() => { load(); }, [chatId, state && state.job && state.job.running, state && state.job && state.job.done]);
    const start = async () => {
      setBusy(true);
      try {
        const e = await t.call("bulk_estimate", { chat_id: chatId });
        const body = [
          `В чате ${e.messages} сообщений. Сначала нужно разобрать: ${e.chapters_new + e.chapters_now} глав.`,
          ...priceLines(e),
          `Цена: ${e.price_text}${e.model ? ` (модель ${e.model})` : ""}. Это оценка, настоящая сумма будет видна по ходу.`,
          e.off.length ? `Не включено и не тратит ничего: ${e.off.join(", ")}.` : "",
        ].filter(Boolean).join("\n");
        if (!(await ui.ask(body, { ok: "Разобрать", cancel: "Не сейчас" }))) return;
        setInfo(await t.call("bulk_start", { chat_id: chatId, confirmed: true }));
        reload();
      } catch (e) {
        ui.toast(e.message, "error");
      } finally {
        setBusy(false);
      }
    };
    const stop = async () => {
      setBusy(true);
      try { setInfo(await t.call("bulk_stop", { chat_id: chatId })); reload(); } catch (e) { ui.toast(e.message, "error"); } finally { setBusy(false); }
    };
    const running = info && info.on;
    return html`<div class="memory-bulk" data-slot="memory-bulk">
      ${!running && html`<button disabled=${busy} onClick=${start}>Разобрать весь чат…</button>`}
      ${running && html`<div class="memory-actions"><button disabled=${busy} onClick=${stop}>Остановить разбор</button></div>`}
      ${info && info.steps.length > 0 && (running || info.finished) && html`<p class="muted memory-hint" role="status">
        ${running ? "Разбор идёт в фоне. " : "Разбор закончен. "}${info.steps.map((s) => `${s.name}: ${s.done} из ${s.total}`).join("; ")}.
        ${info.spent ? ` Потрачено: ${info.spent.text} (${info.spent.requests} запр.), прикидка была ${info.estimate}.` : ""}</p>`}
      <${Help} ui=${ui} text="Кнопка проходит по всему старому или импортированному чату: режет его на главы, пересказывает их и ведёт всё остальное, что вы включили в настройках дополнения. Перед началом вы увидите цену. Пример: чат на 500 сообщений, вы нажали кнопку, прочитали «около 0,4 $» и согласились. Работа идёт сама и уступает вам: когда вы пишете, она ждёт." />
    </div>`;
  }

  // M8: documents given to the memory
  function toBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(binary);
  }

  function Docs({ ui, chatId, tick }) {
    const { html, useState, useEffect } = ui;
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [open, setOpen] = useState("");
    const load = async () => {
      try {
        const got = await t.call("docs", { chat_id: chatId });
        if (t.chatId() === chatId) { setData(got); setError(""); }
      } catch (e) { setError(e.message); }
    };
    useEffect(() => { load(); }, [chatId, tick]);
    const call = async (action, payload = {}) => {
      setBusy(true);
      try { const got = await t.call(action, { chat_id: chatId, ...payload }); await load(); return got || {}; }
      catch (e) { ui.toast(e.message, "error"); return null; }
      finally { setBusy(false); }
    };
    const price = async (doc, e) => {
      const body = [`«${doc.title}»: ${e.chapters} глав, около ${e.tokens} токенов.`,
        `Пересказы: ${e.summary_requests} запросов к модели${e.model ? ` (${e.model})` : ""}; векторы: ${e.embed_tokens} токенов.`,
        `Цена: ${e.price_text}. Это оценка.`].join("\n");
      return ui.ask(body, { ok: "Разобрать", cancel: "Пока не надо" });
    };
    const begin = async (doc) => {
      const e = await call("doc_estimate", { doc_id: doc.id });
      if (e && await price(doc, e)) await call("doc_start", { doc_id: doc.id, confirmed: true });
    };
    const add = async (event) => {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (!file) return;
      setBusy(true);
      try {
        const got = await t.call("doc_add", { chat_id: chatId, name: file.name, data: toBase64(await file.arrayBuffer()) });
        await load();
        if (await price(got.doc, got.estimate)) await t.call("doc_start", { doc_id: got.doc.id, confirmed: true });
        await load();
      } catch (e) { ui.toast(e.message, "error"); } finally { setBusy(false); }
    };
    const remove = async (doc) => {
      if (await ui.ask(`Удалить «${doc.title}» из памяти? Прикрепления и вся работа над ним пропадут.`, { ok: "Удалить", danger: true })) {
        await call("doc_delete", { doc_id: doc.id });
      }
    };
    if (!data) return html`<p class="muted" role=${error ? "alert" : "status"}>${error || "Загрузка…"}</p>`;
    return html`<div class="memory-chapters" data-slot="memory-docs">
      <p class="muted memory-hint">Книга или длинный текст, из которого чат может вспоминать. Файл .txt, .md или .docx режется на главы, главы пересказываются; чат видит только те документы, которые прикреплены к нему.</p>
      <label class="memory-file"><span>Добавить документ…</span><input type="file" accept=".txt,.md,.docx" disabled=${busy} onChange=${add} /></label>
      <${Help} ui=${ui} text="Документ можно прикрепить к нескольким чатам: он лежит один раз, а ищется из каждого. Пока документ не разобран, он ищется только по словам; после разбора ещё и по смыслу. Пример: вы отдали памяти роман, прикрепили к чату про его героев, и когда в разговоре всплывёт название города, в запрос к модели попадёт пересказ нужной главы. Разбор стоит денег, сначала вы увидите цену." />
      ${data.docs.length === 0 && html`<p class="muted">Документов пока нет.</p>`}
      <ul class="memory-list">
        ${data.docs.map((d) => html`<li key=${d.id} class="memory-chapter">
          <div class="memory-sheet">
            <strong>${d.title}</strong>
            <p class="muted">${d.chapters} глав, пересказов ${d.summarized} из ${d.chapters}. ${{
              cut: d.started ? "Ждёт разбора." : "Нарезан, не разобран (ищется по словам).",
              working: d.job && d.job.paused ? "Пауза: идёт ваш запрос." : "Разбирается в фоне…",
              ready: "Готов.", failed: "Не всё удалось.",
            }[d.state] || ""} ${d.error || ""}</p>
            <label class="memory-field"><input type="checkbox" checked=${d.attached} disabled=${busy}
              onChange=${(e) => call(e.target.checked ? "doc_attach" : "doc_detach", { doc_id: d.id })} /> Показывать в этом чате</label>
            <div class="memory-actions">
              ${(!d.started || d.state === "failed") && html`<button disabled=${busy} onClick=${() => begin(d)}>Разобрать…</button>`}
              ${d.started && d.state === "working" && html`<button disabled=${busy} onClick=${() => call("doc_stop", { doc_id: d.id })}>Остановить</button>`}
              <button disabled=${busy} aria-expanded=${open === d.id} onClick=${() => setOpen(open === d.id ? "" : d.id)}>Главы</button>
              <button disabled=${busy} onClick=${() => remove(d)}>Удалить</button>
            </div>
            ${open === d.id && html`<${DocChapters} ui=${ui} docId=${d.id} tick=${tick + d.summarized} />`}
          </div></li>`)}
      </ul>
    </div>`;
  }

  function DocChapters({ ui, docId, tick }) {
    const { html, useState, useEffect } = ui;
    const [view, setView] = useState(null);
    useEffect(() => { t.call("doc_view", { doc_id: docId }).then(setView).catch((e) => ui.toast(e.message, "error")); }, [docId, tick]);
    if (!view) return html`<p class="muted" role="status">Загрузка…</p>`;
    const whole = async (c, on) => {
      try { setView(await t.call("doc_chapter", { chapter_id: c.id, whole: on })); } catch (e) { ui.toast(e.message, "error"); }
    };
    return html`<ul class="memory-versions">
      ${view.chapters.map((c) => html`<li key=${c.id}>
        <strong>${c.number}. ${c.title || "без названия"}</strong>
        <span class="muted">${c.tokens} токенов</span>
        <p class="muted">${c.summary || (c.error ? c.error : "Пересказа пока нет.")}</p>
        <label class="memory-field"><input type="checkbox" checked=${c.whole} onChange=${(e) => whole(c, e.target.checked)} /> Вся глава</label>
        <${Help} ui=${ui} text="Когда глава нашлась, в запрос идёт её пересказ или нужный отрывок. С этой галочкой идёт вся глава целиком (до 3 000 токенов). Пример: глава с важным письмом, которое надо цитировать слово в слово." />
      </li>`)}
    </ul>`;
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
  t.onNotice("threads", (data) => {
    if (!data || data.chat_id === t.chatId()) { threadsTick += 1; t.refresh(); }
  });
  t.onNotice("docs", () => { docsTick += 1; t.refresh(); });
  t.onNotice("faces", () => { facesTick += 1; t.refresh(); }); // faces are app-wide: every open chat reloads them

  t.composerButton({
    icon: "🧠",
    title: "Память++: окно памяти этого чата",
    show: () => !!t.chatId(),
    onClick: () => { if (t.chatId()) win.open(); },
  });
  reload();
}
