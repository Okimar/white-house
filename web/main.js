// App shell: wires the sidebar, the chat view, the settings dialog and the active theme together.
// Elements carry data-slot names so themes can style them; keep those names stable.
import { html, render, useState, useEffect, useCallback, useRef } from "./vendor/preact-htm.mjs";
import { api } from "./core/api.js";
import { useChat } from "./core/chat.js";
import { SAFE_MODE, applyCachedLook, applyTheme, emit } from "./core/theme.js";
import { scriptAllowed } from "./components/Themes.js";
import { VirtualList } from "./components/VirtualList.js";
import { Message } from "./components/Message.js";
import { Composer } from "./components/Composer.js";
import { ProfileSwitch, PickVariants } from "./components/Profiles.js";
import { SpendingBar } from "./components/Spending.js";
import { useSpending } from "./core/spending.js";
import { Sidebar } from "./components/Sidebar.js";
import { Settings } from "./components/Settings.js";
import { Members } from "./components/Members.js";
import { CharacterEditor } from "./components/CharacterEditor.js";
import { PromptView } from "./components/PromptView.js";
import { MessageNotes, Notes, NotesByNumbers } from "./components/Notes.js";
import { ROLES } from "./components/roles.js";
import { pluginNotice, setPluginEnv, syncPlugins, usePluginUi } from "./core/plugins.js";
import { PanelButtons, Panels } from "./components/PluginTree.js";
import { PluginAsks } from "./components/Plugins.js";
import { Assistant } from "./components/Assistant.js";
import { BackupWarning } from "./components/Backups.js";

function App() {
  const [characters, setCharacters] = useState([]);
  const [chats, setChats] = useState([]);
  const [chatId, setChatId] = useState(() => localStorage.getItem("tavern.chat") || null);
  const [settings, setSettings] = useState({});
  const [themes, setThemes] = useState({ themes: [], deleted: [] });
  const [showSettings, setShowSettings] = useState(false); // true, or the tab to open
  const [backupStatus, setBackupStatus] = useState(null);
  const [backupWarningHidden, setBackupWarningHidden] = useState(false);
  const [menu, setMenu] = useState(false);
  const [showAssistant, setShowAssistant] = useState(false);
  const pluginUi = usePluginUi(); // side panels of plugins
  const assistantRef = useRef({ changed() {}, show() {} }); // the event stream outlives renders: it calls the newest
  const [toast, setToast] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [branchResults, setBranchResults] = useState(null);
  const [role, setRole] = useState("assistant"); // who the model's next messages are saved as
  const [selecting, setSelecting] = useState(false); // checkbox mode for changing roles in bulk
  const [selected, setSelected] = useState(() => new Set());
  const [bulkRole, setBulkRole] = useState("user");
  const [numbers, setNumbers] = useState("");
  const [showMembers, setShowMembers] = useState(false);
  const [members, setMembers] = useState([]); // of the open chat: avatars by reply, "ответь ты" buttons
  const [editingCharacter, setEditingCharacter] = useState(undefined); // id, null = new, undefined = closed
  const [promptOf, setPromptOf] = useState(null); // message whose prompt is shown
  const [showNotes, setShowNotes] = useState(false);
  const [notesNumbers, setNotesNumbers] = useState(null); // "1-52 68-72" open in the window over the chat
  const [attached, setAttached] = useState([]); // notes kept after messages: hidden and visible
  const [showHidden, setShowHidden] = useState(() => localStorage.getItem("tavern.showHiddenNotes") === "1");
  const [notesOpen, setNotesOpen] = useState({}); // message id -> true/false after 📝 or ✕ there
  const [notesFor, setNotesFor] = useState(null); // {message, shown}: the drop-down with one message's notes
  const [plugins, setPlugins] = useState({ plugins: [], commands: [], generators: [] }); // GET /plugins
  const [asks, setAsks] = useState([]); // plugins asking to call a model (access setting "ask")

  const onError = useCallback((message) => {
    setToast(message);
    setTimeout(() => setToast(""), 6000);
  }, []);

  const chat = useChat(chatId, onError);

  // Plugins: the list (their web parts load from it) and the app-wide event stream for their questions.
  const loadPlugins = () => api.get("/plugins").then((d) => {
    setPlugins(d);
    setAsks(d.asks);
    syncPlugins(d.plugins);
  }).catch((e) => onError(e.message));
  const loadBackupStatus = () => api.get("/backups/status").then(setBackupStatus).catch(() => {});
  useEffect(() => {
    loadPlugins();
    loadBackupStatus();
    const source = new EventSource("/api/events");
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "plugins") loadPlugins();
      else if (event.type === "plugin_ask") setAsks((a) => [...a.filter((x) => x.id !== event.ask.id), event.ask]);
      else if (event.type === "plugin_asked") setAsks((a) => a.filter((x) => x.id !== event.id));
      else if (event.type === "plugin") pluginNotice(event);
      else if (event.type === "assistant_changed") assistantRef.current.changed();
      else if (event.type === "assistant_show") assistantRef.current.show(event);
      else if (event.type === "backups") { loadBackupStatus(); window.dispatchEvent(new Event("tavern:backups")); }
      else if (event.type === "restored") location.reload();
    };
    return () => source.close();
  }, []);
  // Google sends the browser back here after «Войти через Google» (stage 9б): hand the address to the
  // server, clean it from the address bar and show the «Копии» tab.
  useEffect(() => {
    const q = new URLSearchParams(location.search);
    if (!q.get("state") || !(q.get("code") || q.get("error"))) return;
    const address = location.href;
    history.replaceState(null, "", location.pathname);
    setShowSettings("backups");
    api.post("/backups/gdrive/finish", { address })
      .then(() => onError("✓ Google Диск подключён"))
      .catch((e) => onError(e.message));
  }, []);
  // "/roll 2d6": a plugin's command. Unknown commands are sent as ordinary text.
  const runCommand = async (text) => {
    if (!chatId) { onError("Сначала откройте чат"); return null; }
    try {
      const result = await api.post("/plugins/command", { chat_id: chatId, text });
      if (!result.handled) onError(`Нет такой команды: ${text.split(" ")[0]}`);
      else chat.reload();
      return result;
    } catch (e) {
      onError(e.message);
      return null;
    }
  };
  setPluginEnv({ toast: onError, chatId: () => chatId, reloadChat: () => chat.reload(), command: runCommand });
  const isCommand = (text) => text.startsWith("/") &&
    plugins.commands.some((c) => c.name === text.slice(1).split(/\s/)[0].toLowerCase());
  const { summary: spend, reload: reloadSpending } = useSpending(chatId);

  const refresh = async () => {
    try {
      const [c, l, s, t] = await Promise.all([
        api.get("/characters"), api.get("/chats"), api.get("/settings"), api.get("/themes"),
      ]);
      setCharacters(c.characters);
      setChats(l.chats);
      setSettings(s);
      setThemes(t);
    } catch (e) {
      onError(e.message);
    }
  };
  useEffect(() => { refresh(); }, []);
  // The assistant changed something (or undid it): lists, books, the open chat and its members reload
  // (the settings reload brings a new theme too). "show": it opens a chat or a character for the user.
  const assistantChanged = () => {
    refresh();
    window.dispatchEvent(new Event("tavern:lorebooks"));
    if (chatId) { chat.reload(); chat.refreshInfo(); loadMembers(); }
  };
  assistantRef.current = {
    changed: assistantChanged,
    show: (event) => {
      if (event.chat_id) open(event.chat_id);
      else if (event.character_id) setEditingCharacter(event.character_id);
    },
  };
  const loadMembers = () => chatId
    ? api.get(`/chats/${chatId}/members`).then((d) => setMembers(d.members)).catch((e) => onError(e.message))
    : setMembers([]);
  // After each reply too: a plugin or an agent may have brought a character into the scene.
  useEffect(() => { if (!chat.running) loadMembers(); }, [chatId, chat.running]);
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
  // Notes on messages: after opening a chat and after each reply (it may have placed notes).
  const loadAttached = () => {
    if (!chatId) { setAttached([]); return; }
    api.get(`/chats/${chatId}/message-notes`).then((d) => setAttached(d.notes)).catch((e) => onError(e.message));
  };
  useEffect(() => { if (!chat.running) loadAttached(); }, [chatId, chat.running]);
  useEffect(() => setNotesOpen({}), [chatId]);
  const chooseShowHidden = (on) => {
    setShowHidden(on);
    // "Show all" wins over notes hidden one by one; notes opened one by one stay open.
    setNotesOpen((o) => Object.fromEntries(Object.entries(o).filter(([, open]) => open)));
    localStorage.setItem("tavern.showHiddenNotes", on ? "1" : "0");
  };
  const clearHidden = () => api.del(`/chats/${chatId}/message-notes?kind=hidden`).then(loadAttached)
    .catch((e) => onError(e.message));

  // Theme: the open character's own theme, else the general one. Effects run only if allowed.
  const characterId = chatId && chat.chat ? chat.chat.character_id : null;
  const character = characters.find((c) => c.id === characterId) || null;
  const themeId = (characterId && (settings.character_themes || {})[characterId]) || settings.theme || "default";
  const theme = themes.themes.find((t) => t.id === themeId) || themes.themes.find((t) => t.id === "default");
  const runScript = scriptAllowed(theme, settings);
  useEffect(() => {
    if (theme) applyTheme(theme, { runScript, onScriptError: onError });
  }, [theme && theme.id, theme && theme.script_sha256, theme && theme.base, runScript]);

  // Events for theme effects. They are fired after the theme applied, so a fresh script sees them.
  useEffect(() => {
    emit("chat", { chatId, characterId, characterName: character ? character.name : null });
  }, [chatId, characterId, theme && theme.id]);
  const wasRunning = useRef(false);
  useEffect(() => {
    emit("generation", { running: chat.running });
    const last = chat.messages[chat.messages.length - 1];
    if (wasRunning.current && !chat.running && last) emit("message", { role: last.role, text: last.content });
    wasRunning.current = chat.running;
  }, [chat.running]);

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
  const avatar = character && character.avatar ? `/api/characters/${character.id}/avatar` : null;
  // Each reply shows the avatar of the character who wrote it (a chat may have many).
  const avatarOf = (m) => {
    if (m.role !== "assistant") return null;
    if (!m.character_id) return avatar;
    const who = characters.find((c) => c.id === m.character_id);
    return who && who.avatar ? `/api/characters/${who.id}/avatar` : null;
  };
  const cast = members.filter((m) => !m.is_me);
  const notesOf = {};
  for (const n of attached) (notesOf[n.message_id] ||= []).push(n);
  const lastDepth = chat.messages.length ? chat.messages[chat.messages.length - 1].depth : 0;
  const renderItem = (m) => html`<${Message} message=${m} notes=${notesOf[m.id]} showHidden=${showHidden}
    notesOpen=${notesOpen[m.id]} age=${lastDepth - m.depth}
    onNotesOpen=${(id, open) => setNotesOpen((o) => ({ ...o, [id]: open }))}
    notesDisplay=${settings.notes_display || "few"}
    onOpenNotes=${(message, shownNow) => setNotesFor({ message, shown: shownNow })}
    avatar=${avatarOf(m)} spend=${spend && spend.settings}
    streamText=${chat.streams[m.id] ? chat.streams[m.id].text : null}
    streamThinking=${chat.streams[m.id] ? chat.streams[m.id].thinking : null}
    busy=${chat.running} isLast=${m.id === lastId}
    selecting=${selecting} selected=${selected.has(m.id)} onSelect=${toggleSelected} onRole=${chat.setRoles}
    onEdit=${chat.edit} onSwipe=${chat.swipe} onRemove=${chat.remove} onRegenerate=${chat.regenerate}
    onBranches=${openBranches} onPrompt=${setPromptOf}
    onExclude=${(id, on) => api.put(`/messages/${id}`, { exclude_from_prompt: on }).then(() => chat.reload())
      .catch((e) => onError(e.message))} />`;

  const shown = chatId && chat.chat;
  return html`
    <div class="app" data-slot="app" data-chat-open=${shown ? "true" : "false"}>
      ${SAFE_MODE && html`<div class="safe-banner" role="status">Безопасный режим: темы и окна дополнений выключены.
        <a href="/">Выйти</a></div>`}
      ${!backupWarningHidden && html`<${BackupWarning} status=${backupStatus}
        onOpen=${() => setShowSettings("backups")} onClose=${() => setBackupWarningHidden(true)} />`}
      <${Sidebar} characters=${characters} chats=${chats} chatId=${chatId} open=${menu}
        onOpenChat=${open} onNewChat=${newChat} onImport=${importCard} onImportChat=${importChat}
        onExportCharacter=${exportCharacter} onDeleteChat=${deleteChat}
        onEditCharacter=${(id) => { setEditingCharacter(id); setMenu(false); }}
        onDeleteCharacter=${deleteCharacter} onSettings=${() => setShowSettings(true)}
        onAssistant=${() => { setShowAssistant(true); setMenu(false); }}
        onClose=${() => setMenu(false)} settings=${settings} onError=${onError} />
      <main class="main" data-slot="chat" data-running=${chat.running ? "true" : "false"}>
        <div class="scene" data-slot="scene" aria-hidden="true"></div>
        <div class="topbar" data-slot="topbar">
          <button class="only-mobile" title="Меню" onClick=${() => setMenu(true)}>☰</button>
          <strong class="title" data-slot="title">${shown ? chat.chat.title : "Tavern"}</strong>
          ${shown && html`<form class="search" onSubmit=${search}>
            <input name="query" placeholder="Поиск" aria-label="Поиск по чату" />
          </form>`}
          ${shown && html`<${SpendingBar} summary=${spend} />`}
          ${shown && html`<${ProfileSwitch} key=${String(showSettings)} onChanged=${refresh} onError=${onError} />`}
          ${shown && html`<${PanelButtons} panels=${pluginUi.panels} />`}
          ${shown && html`<button data-slot="members-button" title="Участники чата и пресет"
            onClick=${() => setShowMembers(true)}>👥</button>`}
          ${shown && html`<button data-slot="notes-button" title="Заметки автора"
            onClick=${() => setShowNotes(true)}>📝</button>`}
          ${shown && html`<button class="export-chat" title="Экспортировать чат JSONL" onClick=${exportChat}>↓ JSONL</button>`}
        </div>
        ${shown
          ? html`
            <${VirtualList} items=${chat.messages.filter((m) => !m.hidden)} renderItem=${renderItem}
              onReachTop=${chat.loadOlder} resetKey=${chatId} />
            <div class="chat-actions" data-slot="chat-actions">
              <button disabled=${chat.running} onClick=${() => chat.continue(role)}
                title="Модель напишет следующее сообщение от выбранной роли без вашей реплики">Продолжить</button>
              ${cast.length > 1 && html`<button data-slot="talk-button" disabled=${chat.running} onClick=${() => chat.talk()}
                title="Персонажи отвечают друг другу сами, пока не наберётся предел из «Кто говорит» (👥) или пока вы не нажмёте «Стоп»">Пусть поговорят</button>`}
              ${cast.length > 0 && html`<div class="speakers" data-slot="speakers" role="group" aria-label="Ответит">
                <span class="muted">Ответит:</span>
                ${cast.map((m) => html`<button key=${m.character_id} class=${m.muted ? "muted-member" : ""}
                  disabled=${chat.running} onClick=${() => chat.continue("assistant", m.character_id)}
                  title=${`Ответь ты: ${m.name}` + (m.muted ? " (молчит, но ответит, раз просите)" : "")}>
                  ${m.has_avatar && html`<img class="speaker-avatar" src=${`/api/characters/${m.character_id}/avatar`} alt="" />`}
                  ${m.name}</button>`)}
              </div>`}
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
              onSend=${(text, images) => {
                if (isCommand(text) && !images.length) return runCommand(text);
                emit("send", { text });
                return chat.send(text, role, images);
              }}
              onStop=${chat.stop} onError=${onError} />`
          : html`<div class="empty" data-slot="empty">
              <p>Выберите чат слева или начните новый с персонажем.</p>
              ${!settings.active_connection && html`<p>Сначала добавьте подключение к API:
                <button onClick=${() => setShowSettings(true)}>Настройки</button></p>`}
            </div>`}
        <div class="overlay-layer" data-slot="overlay"></div>
      </main>
      ${shown && html`<${Panels} panels=${pluginUi.panels} />`}
      ${showSettings && html`<${Settings} settings=${settings} themes=${themes.themes}
        initialTab=${typeof showSettings === "string" ? showSettings : undefined}
        deletedThemes=${themes.deleted} characters=${characters} onClose=${() => { setShowSettings(false); reloadSpending(); loadPlugins(); }}
        onChanged=${refresh} onError=${onError} />`}
      ${showMembers && shown && html`<${Members} chat=${chat.chat} characters=${characters} settings=${settings} generators=${plugins.generators}
        onClose=${() => setShowMembers(false)} onError=${onError}
        onChanged=${() => { refresh(); chat.refreshInfo(); loadMembers(); }}
        onAnswer=${(id) => { setShowMembers(false); chat.continue("assistant", id); }}
        onEditCharacter=${setEditingCharacter} />`}
      ${editingCharacter !== undefined && html`<${CharacterEditor} characterId=${editingCharacter}
        settings=${settings} onClose=${() => setEditingCharacter(undefined)} onError=${onError}
        onSaved=${() => { setEditingCharacter(undefined); refresh(); }} />`}
      ${chat.pick && shown && html`<${PickVariants} messageId=${chat.pick} onError=${onError} onClose=${chat.closePick}
        onPick=${async (index) => { await chat.swipe(chat.pick, index); chat.closePick(); }} />`}
      ${showNotes && shown && html`<${Notes} chat=${chat.chat} showHidden=${showHidden}
        onShowHidden=${chooseShowHidden} onClearHidden=${clearHidden} onChanged=${loadAttached}
        onEditNumbers=${(numbers) => { setShowNotes(false); setNotesNumbers(numbers); }}
        onClose=${() => setShowNotes(false)} onError=${onError} />`}
      ${notesNumbers && shown && html`<${NotesByNumbers} chatId=${chatId} numbers=${notesNumbers}
        onChanged=${loadAttached} onClose=${() => setNotesNumbers(null)} onError=${onError} />`}
      ${notesFor && shown && html`<${MessageNotes} chatId=${chatId} message=${notesFor.message}
        shown=${notesFor.shown} onChanged=${loadAttached} onClose=${() => setNotesFor(null)} onError=${onError}
        onShown=${(on) => {
          setNotesFor({ ...notesFor, shown: on });
          setNotesOpen((o) => ({ ...o, [notesFor.message.id]: on }));
        }} />`}
      ${promptOf && html`<${PromptView} message=${promptOf} onClose=${() => setPromptOf(null)} onError=${onError} />`}
      ${showAssistant && html`<${Assistant} chatId=${shown ? chatId : null} onError=${onError}
        onChanged=${assistantChanged} onClose=${() => setShowAssistant(false)} />`}
      ${asks.length > 0 && html`<${PluginAsks} asks=${asks} onError=${onError} />`}
      ${toast && html`<div class="toast" data-slot="toast" role="alert" onClick=${() => setToast("")}>${toast}</div>`}
    </div>
  `;
}

applyCachedLook();
render(html`<${App} />`, document.getElementById("root"));
