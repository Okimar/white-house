// App shell: wires the sidebar, the chat view and the settings dialog together.
import { html, render, useState, useEffect, useCallback } from "./vendor/preact-htm.mjs";
import { api } from "./core/api.js";
import { useChat } from "./core/chat.js";
import { VirtualList } from "./components/VirtualList.js";
import { Message } from "./components/Message.js";
import { Composer } from "./components/Composer.js";
import { Sidebar } from "./components/Sidebar.js";
import { Settings } from "./components/Settings.js";
import { ROLES } from "./components/roles.js";

function App() {
  const [characters, setCharacters] = useState([]);
  const [chats, setChats] = useState([]);
  const [chatId, setChatId] = useState(() => localStorage.getItem("tavern.chat") || null);
  const [settings, setSettings] = useState({});
  const [showSettings, setShowSettings] = useState(false);
  const [menu, setMenu] = useState(false);
  const [toast, setToast] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [branchResults, setBranchResults] = useState(null);
  const [role, setRole] = useState("assistant"); // who the model's next messages are saved as
  const [selecting, setSelecting] = useState(false); // checkbox mode for changing roles in bulk
  const [selected, setSelected] = useState(() => new Set());
  const [bulkRole, setBulkRole] = useState("user");
  const [numbers, setNumbers] = useState("");

  const onError = useCallback((message) => {
    setToast(message);
    setTimeout(() => setToast(""), 6000);
  }, []);

  const chat = useChat(chatId, onError);

  const refresh = async () => {
    try {
      const [c, l, s] = await Promise.all([api.get("/characters"), api.get("/chats"), api.get("/settings")]);
      setCharacters(c.characters);
      setChats(l.chats);
      setSettings(s);
    } catch (e) {
      onError(e.message);
    }
  };
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (chatId) localStorage.setItem("tavern.chat", chatId);
    else localStorage.removeItem("tavern.chat");
  }, [chatId]);
  // A chat that no longer exists (deleted, other data dir): fall back to the empty screen.
  useEffect(() => {
    if (chatId && chats.length && !chats.some((c) => c.id === chatId)) setChatId(null);
  }, [chats]);
  // Refresh the chat list (titles, order) after each finished generation.
  useEffect(() => { if (!chat.running) refresh(); }, [chat.running]);

  const open = (id) => { setChatId(id); setMenu(false); };

  const newChat = async (characterId) => {
    try {
      const created = await api.post("/chats", { character_id: characterId });
      await refresh();
      open(created.chat.id);
    } catch (e) {
      onError(e.message);
    }
  };

  const importCard = async (file) => {
    try {
      if (file.type === "image/png" || file.name.toLowerCase().endsWith(".png")) {
        await api.postBinary("/characters/import/png", file, "image/png");
      } else {
        const card = JSON.parse(await file.text());
        await api.post("/characters", card);
      }
      await refresh();
    } catch (e) {
      onError(e instanceof SyntaxError ? "Файл не является корректным JSON" : e.message);
    }
  };

  const exportCharacter = (id) => {
    window.location.href = `/api/characters/${id}/export.png`;
  };

  const exportChat = () => {
    if (chatId) window.location.href = `/api/chats/${chatId}/export.jsonl`;
  };

  const importChat = async (file) => {
    try {
      const created = await fetch("/api/chats/import.jsonl", {
        method: "POST", headers: { "Content-Type": "application/x-ndjson" }, body: await file.text(),
      });
      if (!created.ok) {
        const error = await created.json();
        throw new Error(error.error?.message || "Не удалось импортировать чат");
      }
      const data = await created.json();
      await refresh();
      open(data.chat.id);
    } catch (e) {
      onError(e.message);
    }
  };

  const deleteChat = async (id) => {
    await api.del(`/chats/${id}`).catch((e) => onError(e.message));
    if (id === chatId) setChatId(null);
    refresh();
  };

  const deleteCharacter = async (id) => {
    await api.del(`/characters/${id}`).catch((e) => onError(e.message));
    refresh();
  };

  // Search, branch and selection panels belong to one chat; the reply role is remembered per chat.
  useEffect(() => {
    setSearchResults(null);
    setBranchResults(null);
    setSelecting(false);
    setSelected(new Set());
    setNumbers("");
    let saved = null;
    try { saved = chatId && localStorage.getItem("tavern.replyRole." + chatId); } catch (e) { /* storage off */ }
    setRole(ROLES.some((r) => r.id === saved) ? saved : "assistant");
  }, [chatId]);

  const chooseRole = (value) => {
    setRole(value);
    try { localStorage.setItem("tavern.replyRole." + chatId, value); } catch (e) { /* storage off */ }
  };

  const toggleSelected = (id) => setSelected((s) => {
    const next = new Set(s);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  // Picked checkboxes and typed numbers are combined; "Все" covers the whole active branch on the server.
  const applyRoles = async (everything) => {
    const target = everything ? { all: true } : { ids: [...selected], numbers };
    if (!everything && !selected.size && !numbers.trim()) {
      onError("Отметьте сообщения или введите номера, например 1-5, 8");
      return;
    }
    await chat.setRoles(bulkRole, target);
    setSelected(new Set());
    setNumbers("");
  };

  const search = async (event) => {
    event.preventDefault();
    const query = event.currentTarget.elements.query.value.trim();
    if (!query || !chatId) return;
    try {
      const result = await chat.search(query);
      setSearchResults(result.messages);
    } catch (e) {
      onError(e.message);
    }
  };

  const openBranches = async (messageId) => {
    try {
      const result = await chat.branches(messageId);
      setBranchResults({ messageId, branches: result.branches });
    } catch (e) {
      onError(e.message);
    }
  };

  // Switches to the branch through this message (and its newest continuation).
  const selectBranch = async (messageId) => {
    setBranchResults(null);
    setSearchResults(null);
    await chat.selectBranch(messageId);
  };

  const lastId = chat.messages.length ? chat.messages[chat.messages.length - 1].id : null;
  const renderItem = (m) => html`<${Message} message=${m}
    streamText=${chat.stream && chat.stream.id === m.id ? chat.stream.text : null}
    busy=${chat.running} isLast=${m.id === lastId}
    selecting=${selecting} selected=${selected.has(m.id)} onSelect=${toggleSelected} onRole=${chat.setRoles}
    onEdit=${chat.edit} onSwipe=${chat.swipe} onRemove=${chat.remove} onRegenerate=${chat.regenerate}
    onBranches=${openBranches} />`;

  const shown = chatId && chat.chat;
  return html`
    <div class="app">
      <${Sidebar} characters=${characters} chats=${chats} chatId=${chatId} open=${menu}
        onOpenChat=${open} onNewChat=${newChat} onImport=${importCard} onImportChat=${importChat}
        onExportCharacter=${exportCharacter} onDeleteChat=${deleteChat}
        onDeleteCharacter=${deleteCharacter} onSettings=${() => setShowSettings(true)}
        onClose=${() => setMenu(false)} />
      <main class="main">
        <div class="topbar">
          <button class="only-mobile" title="Меню" onClick=${() => setMenu(true)}>☰</button>
          <strong>${shown ? chat.chat.title : "Tavern"}</strong>
          ${shown && html`<form class="search" onSubmit=${search}>
            <input name="query" placeholder="Поиск" aria-label="Поиск по чату" />
          </form>`}
          ${shown && html`<button class="export-chat" title="Экспортировать чат JSONL" onClick=${exportChat}>↓ JSONL</button>`}
        </div>
        ${shown
          ? html`
            <${VirtualList} items=${chat.messages} renderItem=${renderItem}
              onReachTop=${chat.loadOlder} resetKey=${chatId} />
            <div class="chat-actions">
              <button disabled=${chat.running} onClick=${() => chat.continue(role)}
                title="Модель напишет следующее сообщение от выбранной роли без вашей реплики">Продолжить</button>
              <button aria-pressed=${selecting} onClick=${() => { setSelecting(!selecting); setSelected(new Set()); }}>
                ${selecting ? "Готово" : "Сменить роли…"}</button>
              ${selecting && html`<div class="bulk-roles" role="group" aria-label="Массовая смена ролей">
                <span>Отмечено: ${selected.size}</span>
                <input value=${numbers} placeholder="Номера: 1-5, 8" aria-label="Номера сообщений"
                  onInput=${(e) => setNumbers(e.target.value)} />
                <select value=${bulkRole} aria-label="Новая роль" onChange=${(e) => setBulkRole(e.target.value)}>
                  ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.icon} ${r.label}</option>`)}
                </select>
                <button class="primary" disabled=${chat.running} onClick=${() => applyRoles(false)}>Применить</button>
                <button disabled=${chat.running}
                  onClick=${() => confirm("Сменить роль у всех сообщений текущей ветки?") && applyRoles(true)}>Всем</button>
              </div>`}
              ${searchResults && html`<div class="search-results">
                <strong>Результаты: ${searchResults.length}</strong>
                ${searchResults.map((m) => html`<button key=${m.id} disabled=${chat.running}
                  onClick=${() => selectBranch(m.id)}>${m.content.slice(0, 80)}</button>`)}
                <button onClick=${() => setSearchResults(null)}>Закрыть</button>
              </div>`}
              ${branchResults && html`<div class="branch-results">
                <strong>Ветки: ${branchResults.branches.length}</strong>
                ${branchResults.branches.map((m) => html`<button key=${m.id} disabled=${chat.running}
                  onClick=${() => selectBranch(m.id)}>${m.content.slice(0, 80)}</button>`)}
                <button onClick=${() => setBranchResults(null)}>Закрыть</button>
              </div>`}
            </div>
            <${Composer} running=${chat.running} role=${role} onRole=${chooseRole}
              onSend=${(text) => chat.send(text, role)} onStop=${chat.stop} />`
          : html`<div class="empty">
              <p>Выберите чат слева или начните новый с персонажем.</p>
              ${!settings.active_connection && html`<p>Сначала добавьте подключение к API:
                <button onClick=${() => setShowSettings(true)}>Настройки</button></p>`}
            </div>`}
      </main>
      ${showSettings && html`<${Settings} settings=${settings} onClose=${() => setShowSettings(false)}
        onChanged=${refresh} onError=${onError} />`}
      ${toast && html`<div class="toast" role="alert" onClick=${() => setToast("")}>${toast}</div>`}
    </div>
  `;
}

render(html`<${App} />`, document.getElementById("root"));
