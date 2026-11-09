// 🧠 Память++ (stages M1-M2): the composer button opens the «Память» window; its «Главы» tab lists the chapters of
// the open branch (split, merge, move the border, delete, restore, undo). Chapter borders are thin lines above
// messages, «Новая глава отсюда» is in a message's «⋯» menu, and a chip in the top bar shows the background work.
export default function setup(t) {
  let cache = { chatId: "", view: null, state: null, job: null };
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
        </div>
        ${cache.error && !view && html`<p class="muted" role="alert">${cache.error}</p>`}
        ${view && tab === "chapters" && html`<${Chapters} ui=${ui} view=${view} open=${open} setOpen=${setOpen} busy=${busy}
          call=${call} />`}
      </div>`;
    },
  });

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
    const chip = { raw: "", stale: "изменилась", deleted: "удалена" };
    return html`<div class="memory-chapters">
      <div class="memory-toolbar">
        <button disabled=${busy || !view.undo} onClick=${() => call("undo")}
          title="Отменить последнее ручное изменение глав">↶ ${view.undo ? "Отменить: " + view.undo : "Отменить"}</button>
      </div>
      ${view.chapters.length === 0 && html`<p class="muted">Глав пока нет. Глава закрывается, когда после неё накопилось больше
        ${view.size} сообщений и нашлась граница сцены (или втрое больше). Сообщений без главы: ${view.open.count}.</p>`}
      <ul class="memory-list">
        ${view.chapters.map((c) => html`<li key=${c.id} class=${"memory-chapter " + c.state} data-state=${c.state}>
          <button class="memory-row" aria-expanded=${open === c.id} onClick=${() => setOpen(open === c.id ? "" : c.id)}>
            <span class="memory-row-main"><strong>${c.number ? "Глава " + c.number : "Глава (удалена)"}</strong>
              <span class="muted"> · №${c.first_no}–№${c.last_no}${c.title ? " · " + c.title : ""}</span></span>
            ${chip[c.state] && html`<span class="memory-state">${chip[c.state]}</span>`}
          </button>
          ${open === c.id && html`<div class="memory-sheet">
            <p class="muted">${c.count} сообщений. Граница: ${c.reason || "—"}${c.by === "hand" ? " (вручную)" : c.by === "model" ? " (модель)" : ""}.</p>
            <p class="memory-preview">${c.preview}</p>
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

  t.composerButton({
    icon: "🧠",
    title: "Память++: окно памяти этого чата",
    show: () => !!t.chatId(),
    onClick: () => { if (t.chatId()) win.open(); },
  });
  reload();
}
