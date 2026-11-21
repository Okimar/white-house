// App shell: wires the sidebar, the chat view, the settings dialog and the active theme together.
// Elements carry data-slot names so themes can style them; keep those names stable.
import { Select } from "./components/Select.js";
import { html, render, useState, useEffect, useCallback, useRef } from "./vendor/preact-htm.mjs";
import { api } from "./core/api.js";
import { useChat } from "./core/chat.js";
import { applyCachedLook, applyTheme, emit, syncLookNotice } from "./core/theme.js";
import { scriptAllowed } from "./components/Themes.js";
import { VirtualList } from "./components/VirtualList.js";
import { Message } from "./components/Message.js";
import { useRegexDisplay } from "./core/regex.js";
import { textColorRules } from "./core/textColors.js";
import { ModelChip } from "./components/ModelChip.js";
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
import { pluginNotice, setPluginEnv, statusChips, syncPlugins, usePluginUi } from "./core/plugins.js";
import { PanelButtons, Panels } from "./components/PluginTree.js";
import { PluginWindows, StatusChips, WindowButtons } from "./components/PluginWindow.js";
import { PluginAsks } from "./components/Plugins.js";
import { Assistant } from "./components/Assistant.js";
import { Search } from "./components/Search.js";
import { BackupWarning } from "./components/Backups.js";
import { Icon, IconButton } from "./components/Icon.js";
import { MenuButton } from "./components/Menu.js";
import { DialogHost, ask, askText } from "./components/Dialogs.js";
import { EmptyNote, Pending } from "./components/State.js";
import { dismiss, notify, subscribe } from "./core/notify.js";
import { handleCapture } from "./core/capture.js";
import { useEscape } from "./core/keys.js";
import { useLayout, setMenuFolded } from "./core/layout.js";

// The page must open with the browser's storage blocked (private window, site data off): every access is guarded.
const store = {
  get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* storage off */ } },
  del(key) { try { localStorage.removeItem(key); } catch (e) { /* storage off */ } },
};

const FOUND_MS = 2500; // how long a found message stays marked

// The icons of the top bar (a phone's, or the menu bar of the top menu): each opens one section of the
// menu as a sheet / drop-down under the bar.
const SECTIONS = [
  ["chats", "message-square", "Чаты"],
  ["characters", "users", "Персонажи"],
  ["lorebooks", "book-open", "Лорбуки"],
];

// A sheet closes on Esc too (a component, because the hook must live only while the sheet is open).
function SheetKeys({ onClose }) {
  useEscape(onClose);
  return null;
}

function App() {
  const [characters, setCharacters] = useState([]);
  const [chats, setChats] = useState([]);
  const [chatId, setChatId] = useState(() => store.get("tavern.chat") || null);
  const [settings, setSettings] = useState({});
  const [themes, setThemes] = useState({ themes: [], deleted: [] });
  const [showSettings, setShowSettings] = useState(false); // true, or the tab to open
  const [backupStatus, setBackupStatus] = useState(null);
  const [backupWarningHidden, setBackupWarningHidden] = useState(false);
  const [loaded, setLoaded] = useState(false); // the first load of the lists and settings finished
  const [chatsLoaded, setChatsLoaded] = useState(false); // the chat list really came from the server
  const chatsLoadedRef = useRef(false);
  chatsLoadedRef.current = chatsLoaded;
  const [sheet, setSheet] = useState(null); // phone: the section of the left panel shown under the bar
  const [searching, setSearching] = useState(false); // phone: the search line is open
  const [showAssistant, setShowAssistant] = useState(false);
  const [showSearch, setShowSearch] = useState(false); // the search across all chats
  const [pendingFound, setPendingFound] = useState(null); // a result in another chat, shown once that chat is loaded
  const [assistantMsg, setAssistantMsg] = useState(null); // a conversation message to scroll to
  const [assistantConv, setAssistantConv] = useState(""); // a conversation to open (from the 🪄 mark)
  const [assistantState, setAssistantState] = useState({ running: [], waiting: [] });
  const { mode: menuMode, folded: menuFolded } = useLayout(); // menu on top / at the side, folded or not
  const pluginUi = usePluginUi(); // side panels of plugins
  const assistantRef = useRef({ changed() {}, show() {} }); // the event stream outlives renders: it calls the newest
  const [toast, setToast] = useState(null); // { text, kind } from core/notify.js
  const [toastClosing, setToastClosing] = useState(false);
  const toastExitTimer = useRef(null);
  const [searchResults, setSearchResults] = useState(null);
  const [jumpTo, setJumpTo] = useState(null); // { id } of the found message being shown
  const foundTimer = useRef(null);
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
  const [showHidden, setShowHidden] = useState(() => store.get("tavern.showHiddenNotes") === "1");
  const [notesOpen, setNotesOpen] = useState({}); // message id -> true/false after 📝 or ✕ there
  const [notesFor, setNotesFor] = useState(null); // {message, shown}: the drop-down with one message's notes
  const [plugins, setPlugins] = useState({ plugins: [], commands: [], generators: [] }); // GET /plugins
  const [asks, setAsks] = useState([]); // plugins asking to call a model (access setting "ask")

  const onError = useCallback((message) => notify(message, "error"), []);
  useEffect(() => subscribe((next) => {
    clearTimeout(toastExitTimer.current);
    if (next) {
      setToast(next);
      setToastClosing(false);
    } else {
      setToastClosing(true);
      toastExitTimer.current = setTimeout(() => {
        setToast(null);
        setToastClosing(false);
      }, 140);
    }
  }), []);

  const chat = useChat(chatId, onError);
  const regexShown = useRegexDisplay(chatId, chat.messages, chat.streams); // regex rules for the screen (stage 11)

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
    const loadAssistantState = () => api.get("/assistant/state").then(setAssistantState).catch(() => {});
    loadAssistantState();
    const source = new EventSource("/api/events");
    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (event.type === "regex") window.dispatchEvent(new Event("tavern:regex"));
      else if (event.type === "regex_skipped") {
        onError(`Регекс-правило «${event.rules.map((r) => r.name).join("», «")}» ${event.rules[0].why}, поэтому пропущено. ` +
          "Исправьте шаблон: Настройки → Регекс.");
      } else if (event.type === "plugins") loadPlugins();
      else if (event.type === "plugin_ask") setAsks((a) => [...a.filter((x) => x.id !== event.ask.id), event.ask]);
      else if (event.type === "plugin_asked") setAsks((a) => a.filter((x) => x.id !== event.id));
      else if (event.type === "plugin") pluginNotice(event);
      else if (event.type === "job") emit("job", event);
      else if (event.type === "assistant_changed") assistantRef.current.changed();
      else if (event.type === "assistant_show") assistantRef.current.show(event);
      else if (event.type === "assistant_capture") handleCapture(event);
      else if (event.type === "assistant_state") loadAssistantState();
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
      .then(() => notify("Google Диск подключён", "ok"))
      .catch((e) => onError(e.message));
  }, []);
  // "/roll 2d6": a plugin's command. Unknown commands are sent as ordinary text.
  const runCommand = async (text) => {
    if (!chatId) { onError("Сначала откройте чат"); return false; }
    try {
      const result = await api.post("/plugins/command", { chat_id: chatId, text });
      if (!result.handled) { onError(`Нет такой команды: ${text.split(" ")[0]}`); return false; }
      chat.reload();
      return result;
    } catch (e) {
      onError(e.message);
      return false;
    }
  };
  setPluginEnv({ toast: (text) => notify(text, "info"), chatId: () => chatId, reloadChat: () => chat.reload(), command: runCommand,
    showMessage: (message) => showFound(message),
    openCharacter: (id) => { refresh(); setEditingCharacter(id); } });
  const isCommand = (text) => text.startsWith("/") &&
    plugins.commands.some((c) => c.name === text.slice(1).split(/\s/)[0].toLowerCase());
  const { summary: spend, reload: reloadSpending } = useSpending(chatId);

  const retry = useRef(null);
  const refresh = async () => {
    clearTimeout(retry.current);
    try {
      const [c, l, s, t] = await Promise.all([
        api.get("/characters"), api.get("/chats"), api.get("/settings"), api.get("/themes"),
      ]);
      setCharacters(c.characters);
      setChats(l.chats);
      setChatsLoaded(true);
      setSettings(s);
      setThemes(t);
    } catch (e) {
      // The server is starting or the network blinked (a phone waking up): try again by itself, and never take
      // the remembered chat away because the list did not come.
      if (!chatsLoadedRef.current) retry.current = setTimeout(refresh, 3000);
      onError(e.message);
    } finally {
      setLoaded(true);
    }
  };
  useEffect(() => { refresh(); }, []);
  useEffect(() => { window.addEventListener("tavern:settings-changed", refresh); return () => window.removeEventListener("tavern:settings-changed", refresh); }, []);
  // The assistant changed something (or undid it): lists, books, the open chat and its members reload
  // (the settings reload brings a new theme too). "show": it opens a chat or a character for the user.
  const assistantChanged = () => {
    refresh();
    window.dispatchEvent(new Event("tavern:lorebooks"));
    window.dispatchEvent(new Event("tavern:groups"));
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
    ? api.get(`/chats/${chatId}/members`).then((d) => setMembers(d.members.filter((m) => m.character_id))) // empty places have no card and never speak
      .catch((e) => { if (e.status !== 404) onError(e.message); })
    : setMembers([]);
  // After each reply too: a plugin or an agent may have brought a character into the scene.
  useEffect(() => { if (!chat.running) loadMembers(); }, [chatId, chat.running]);
  useEffect(() => {
    if (chatId) store.set("tavern.chat", chatId);
    else store.del("tavern.chat");
  }, [chatId]);
  // A chat that no longer exists (deleted, other data dir): fall back to the empty screen. Only once the list
  // really came from the server: a failed first load must not forget the chat that was open.
  useEffect(() => {
    if (chatsLoaded && chatId && !chats.some((c) => c.id === chatId)) setChatId(null);
  }, [chatsLoaded, chats, chatId]);
  // Refresh the chat list (titles, order) after each finished generation.
  useEffect(() => { if (!chat.running) refresh(); }, [chat.running]);
  // Notes on messages: after opening a chat and after each reply (it may have placed notes).
  const loadAttached = () => {
    if (!chatId) { setAttached([]); return; }
    api.get(`/chats/${chatId}/message-notes`).then((d) => setAttached(d.notes)).catch((e) => { if (e.status !== 404) onError(e.message); }); // 404: a remembered chat that is gone
  };
  useEffect(() => { if (!chat.running) loadAttached(); }, [chatId, chat.running]);
  useEffect(() => setNotesOpen({}), [chatId]);
  const chooseShowHidden = (on) => {
    setShowHidden(on);
    // "Show all" wins over notes hidden one by one; notes opened one by one stay open.
    setNotesOpen((o) => Object.fromEntries(Object.entries(o).filter(([, open]) => open)));
    store.set("tavern.showHiddenNotes", on ? "1" : "0");
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
  // The notice «оформление отключено» follows the choice: a theme picked again by hand is on, so the notice goes.
  const themeChoice = JSON.stringify([settings.theme || "", settings.character_themes || {}]);
  const firstChoice = useRef(null);
  useEffect(() => {
    if (!loaded) return;
    if (firstChoice.current === null) { firstChoice.current = themeChoice; return; } // the notice was drawn for this one
    if (themeChoice !== firstChoice.current) { firstChoice.current = themeChoice; syncLookNotice().catch(() => {}); }
  }, [loaded, themeChoice]);

  // Events for theme effects. They are fired after the theme applied, so a fresh script sees them.
  useEffect(() => {
    emit("chat", { chatId, characterId, characterName: character ? character.name : null });
  }, [chatId, characterId, theme && theme.id]);
  const wasRunning = useRef(false);
  useEffect(() => {
    emit("generation", { running: chat.running });
    const last = chat.messages[chat.messages.length - 1];
    if (wasRunning.current && !chat.running && last) emit("message", { role: last.role, text: last.content, id: last.id, characterId: last.character_id || null });
    wasRunning.current = chat.running;
  }, [chat.running]);

  // Leaving a chat for the home page keeps the chat; the browser's Back does the same, and Forward returns.
  const open = (id) => {
    if (id && !chatId) { try { history.pushState({ chat: id }, ""); } catch (e) { /* no history */ } }
    else if (id) { try { history.replaceState({ chat: id }, ""); } catch (e) { /* no history */ } }
    setChatId(id);
    setSheet(null);
  };
  // A tap or a press outside the open drop-down (and outside the bar that opened it) closes it.
  useEffect(() => {
    if (!sheet) return undefined;
    const outside = (e) => {
      const t = e.target;
      if (t && t.closest && t.closest(".sidebar, .menubar, .phone-nav, .menu, .overlay, .toast, .dialog")) return;
      setSheet(null);
    };
    document.addEventListener("pointerdown", outside, true);
    return () => document.removeEventListener("pointerdown", outside, true);
  }, [sheet]);
  const goHome = () => {
    setSheet(null);
    if (history.state && history.state.chat) history.back(); // popstate closes the chat
    else setChatId(null);
  };
  useEffect(() => {
    // A chat restored from the last visit gets a "home" entry below it, so Back leads home, not away.
    try { if (chatId) { history.replaceState({ home: true }, ""); history.pushState({ chat: chatId }, ""); } } catch (e) { /* no history */ }
    const onPop = (e) => { setSheet(null); setChatId(e.state && e.state.chat ? e.state.chat : null); };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

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
        method: "POST", body: await file.text(),
        headers: { "Content-Type": "application/x-ndjson", "X-Filename": encodeURIComponent(file.name || "") },
      });
      if (!created.ok) {
        const error = await created.json().catch(() => null); // a proxy's error page is not JSON
        throw new Error(error?.error?.message || `Не удалось импортировать чат (ошибка ${created.status})`);
      }
      const data = await created.json();
      await refresh();
      open(data.chat.id);
      if (data.empty_members?.length) {
        const names = data.empty_members.map((n) => `«${n}»`).join(", ");
        const toMembers = await ask(`В чате были персонажи, которых нет или имя совпадает: ${names}. Они оставлены пустыми `
          + "местами. Нажмите «Заменить» в участниках чата и выберите своего персонажа: тогда прежние реплики станут его репликами.",
        { ok: "Открыть участников", cancel: "Позже" });
        if (toMembers) setShowMembers(true);
      }
    } catch (e) {
      onError(e.message);
    }
  };

  const deleteChat = async (id) => {
    try {
      await api.del(`/chats/${id}`);
      if (id === chatId) setChatId(null);
    } catch (e) {
      onError(e.message); // it still exists: stays open
    }
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
    setSearching(false);
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

  // A found message: bring it into the view and mark it for a moment. Another branch closes the
  // list (its marks of "this branch" are stale); on the same branch the list stays for the next find.
  const showFound = async (message) => {
    if (!message.on_path) setSearchResults(null);
    await chat.reveal(message);
    setJumpTo({ id: message.id });
    clearTimeout(foundTimer.current);
    foundTimer.current = setTimeout(() => setJumpTo(null), FOUND_MS);
  };

  // A result of the search across all chats: this chat at that message, or the assistant's conversation.
  const openFound = (hit) => {
    setShowSearch(false);
    if (hit.scope === "assistant") {
      setAssistantConv(hit.chat_id);
      setAssistantMsg({ conv: hit.chat_id, id: hit.id, stamp: Date.now() });
      openAssistant();
      return;
    }
    if (hit.chat_id !== chatId) open(hit.chat_id);
    setPendingFound(hit);
  };
  useEffect(() => {
    if (!pendingFound || !chat.chat || chat.chat.id !== pendingFound.chat_id) return;
    const hit = pendingFound;
    setPendingFound(null);
    showFound(hit).catch((e) => onError(e.message));
  }, [pendingFound, chat.chat && chat.chat.id]);

  const branchFrom = async (messageId) => {
    setBranchResults(null);
    await chat.branchFrom(messageId);
  };

  const renameChat = async (id, title) => {
    try {
      await api.put(`/chats/${id}`, { title });
      await refresh();
      if (id === chatId) chat.refreshInfo();
    } catch (e) {
      onError(e.message);
    }
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
  const textColors = textColorRules(settings); // «Цветной текст»: "quotes" and the person's own marks
  const renderItem = (m) => html`<${Message} message=${m} notes=${notesOf[m.id]} showHidden=${showHidden}
    notesOpen=${notesOpen[m.id]} age=${lastDepth - m.depth}
    onNotesOpen=${(id, open) => setNotesOpen((o) => ({ ...o, [id]: open }))}
    notesDisplay=${settings.notes_display || "few"} thinkingDisplay=${settings.thinking_display || "folded"}
    actionsMode=${settings.message_actions || "menu"} pinned=${settings.message_pinned}
    onOpenNotes=${(message, shownNow) => setNotesFor({ message, shown: shownNow })}
    avatar=${avatarOf(m)} spend=${spend && spend.settings}
    streamText=${chat.streams[m.id] ? chat.streams[m.id].text : null}
    streamThinking=${chat.streams[m.id] ? chat.streams[m.id].thinking : null}
    shown=${regexShown(m, chat.streams[m.id] ? chat.streams[m.id].text : null)}
    textColors=${textColors}
    shownThinking=${regexShown.thinking ? regexShown.thinking(m, chat.streams[m.id] ? chat.streams[m.id].thinking : null) : null}
    onRegexUndo=${(id) => api.post(`/regex/messages/${id}/original`).then(() => chat.reload()).catch((e) => onError(e.message))}
    busy=${chat.running} isLast=${m.id === lastId} found=${jumpTo && jumpTo.id === m.id}
    selecting=${selecting} selected=${selected.has(m.id)} onSelect=${toggleSelected} onRole=${chat.setRoles}
    onEdit=${chat.edit} onSwipe=${chat.swipe} onRemove=${chat.remove} onRegenerate=${(m) => (m.role === "user" ? chat.continue(role) : chat.regenerate())} onContinueMessage=${chat.continueMessage}
    onBranches=${openBranches} onPrompt=${setPromptOf}
    onExclude=${(id, on) => api.put(`/messages/${id}`, { exclude_from_prompt: on }).then(() => chat.reload())
      .catch((e) => onError(e.message))} />`;

  const shown = chatId && chat.chat;
  const openAssistant = () => { setSheet(null); setShowAssistant(true); };
  const openSearch = () => { setSheet(null); setShowSearch(true); };
  const openSettings = (tab = true) => { setSheet(null); setShowSettings(tab); };
  const assistantBusy = assistantState.waiting.length > 0 || assistantState.running.length > 0;
  const openAssistantMark = () => {
    setAssistantConv(assistantState.waiting[0] || assistantState.running[0]);
    openAssistant();
  };
  const toggleSelecting = () => { setSelecting(!selecting); setSelected(new Set()); };
  // How messages look: this chat's own choice (chat meta) or the app setting; «Пузыри» when neither is set.
  const chatView = [chat.chat && chat.chat.meta && chat.chat.meta.chat_view, settings.chat_view].find((v) => v === "document" || v === "bubbles") || "bubbles";
  const setChatView = (view) => api.put(`/chats/${chatId}/meta`, { chat_view: view })
    .then(() => chat.refreshInfo()).catch((e) => onError(e.message));
  const ownView = (chat.chat && chat.chat.meta && chat.chat.meta.chat_view) || "";
  const viewItem = (view, label) => ({ icon: ownView === view ? "check" : view === "document" ? "book-open" : "message-square", label: "Вид: " + label,
    disabled: ownView === view, onClick: () => setChatView(view) });
  const chatMenu = () => [
    { icon: "search", label: "Поиск по чату", onClick: () => setSearching(true), phone: true },
    { icon: "users", label: "Участники и пресет", onClick: () => setShowMembers(true), phone: true },
    { icon: "notebook-pen", label: "Заметки автора", onClick: () => setShowNotes(true), phone: true },
    { icon: "user", label: selecting ? "Закончить смену ролей" : "Сменить роли", onClick: toggleSelecting },
    viewItem("bubbles", "Пузыри"),
    viewItem("document", "Документ"),
    { icon: "undo-2", label: "Вид: как в настройках", disabled: !ownView, onClick: () => setChatView("") },
    { icon: "download", label: "Скачать чат", href: `/api/chats/${chatId}/export.jsonl` },
  ].filter((item) => !item.phone || window.matchMedia("(max-width: 720px)").matches);
  const onTitleClick = async () => {
    if (!shown || !chat.chat) return;
    const current = (chat.chat.title || "").trim();
    const next = ((await askText("Название чата", {
      value: current,
      placeholder: "Без названия",
      ok: "Сохранить",
      hint: current ? `Текущее название: «${current}»` : "Введите название чата",
    })) || "").trim();
    if (next && next !== current) renameChat(chatId, next);
  };
  return html`
    <div class="app" data-slot="app" data-chat-open=${shown ? "true" : "false"} data-sheet=${sheet ? "open" : "closed"}
      data-menu=${menuMode} data-menu-folded=${menuFolded ? "true" : "false"}>
      ${!backupWarningHidden && !showSettings && html`<${BackupWarning} status=${backupStatus}
        onOpen=${() => openSettings("backups")} onClose=${() => setBackupWarningHidden(true)} />`}
      <${Sidebar} characters=${characters} chats=${chats} chatId=${chatId} section=${sheet}
        onOpenChat=${open} onHome=${goHome} onNewChat=${newChat} onImport=${importCard} onImportChat=${importChat}
        onExportCharacter=${exportCharacter} onDeleteChat=${deleteChat} onRenameChat=${renameChat}
        onEditCharacter=${(id) => { setEditingCharacter(id); setSheet(null); }}
        onDeleteCharacter=${deleteCharacter} onSettings=${() => openSettings()}
        onAssistant=${openAssistant} onSearch=${openSearch} settings=${settings} onError=${onError}
        mode=${menuMode} onFold=${menuMode === "side" ? () => { setSheet(null); setMenuFolded(true); } : null} />
      ${sheet && html`<${SheetKeys} onClose=${() => setSheet(null)} />`}
      <main class="main" data-slot="chat" data-view=${chatView} data-running=${chat.running ? "true" : "false"}
        data-density=${settings.chat_density === "roomy" ? "roomy" : "compact"}>
        <div class="scene" data-slot="scene" aria-hidden="true"></div>
        ${menuMode === "top" && !menuFolded && html`<nav class="menubar only-wide" data-slot="menubar" aria-label="Меню">
          <${IconButton} icon="chevron-down" label="Свернуть меню" class="fold-menu" data-slot="fold-menu"
            onClick=${() => { setSheet(null); setMenuFolded(true); }} />
          <button class="brand brand-home" data-slot="menubar-home" title="На главную: чат останется в списке"
            aria-label="На главную" onClick=${goHome}>White House</button>
          <span class="menubar-sections">
            ${SECTIONS.map(([id, icon, label]) => html`<${IconButton} key=${id} icon=${icon} label=${label}
              data-slot=${"menubar-" + id} aria-pressed=${sheet === id} aria-haspopup="true" aria-expanded=${sheet === id}
              onClick=${() => setSheet(sheet === id ? null : id)} />`)}
          </span>
          <span class="spacer"></span>
          <${IconButton} icon="search" label="Поиск по всем чатам" data-slot="menubar-search" onClick=${openSearch} />
          <${IconButton} icon="wand" label=${assistantBusy ? "Помощник: есть работа" : "Помощник"}
            class=${assistantBusy ? "has-mark" : ""} data-slot="menubar-assistant"
            onClick=${assistantBusy ? openAssistantMark : openAssistant} />
          <${IconButton} icon="settings" label="Настройки" data-slot="menubar-settings" onClick=${() => openSettings()} />
        </nav>`}
        <div class="topbar" data-slot="topbar" data-folded=${menuFolded ? "true" : "false"}>
          ${menuFolded && html`<span class="phone-folded" data-slot="phone-folded">
            <${IconButton} icon="menu" label="Развернуть верхнюю панель" data-slot="phone-unfold" onClick=${() => setMenuFolded(false)} />
            <button class="brand brand-home icon-btn" data-slot="phone-folded-home" title="На главную: чат останется в списке"
              aria-label="На главную" onClick=${goHome}><${Icon} name="home" /></button>
            <${IconButton} icon="search" label="Поиск по всем чатам" data-slot="phone-folded-search" onClick=${openSearch} />
            <${IconButton} icon="settings" label="Настройки" data-slot="phone-folded-settings" onClick=${() => openSettings()} />
          </span>`}
          ${menuFolded && html`<span class="menu-folded only-wide" data-slot="menu-folded">
            <${IconButton} icon="menu" label="Развернуть меню" data-slot="unfold-menu" onClick=${() => setMenuFolded(false)} />
            <button class="brand brand-home icon-btn" data-slot="folded-home" title="На главную: чат останется в списке"
              aria-label="На главную" onClick=${goHome}><${Icon} name="home" /></button>
            <${IconButton} icon="search" label="Поиск по всем чатам" data-slot="folded-search" onClick=${openSearch} />
            <${IconButton} icon="settings" label="Настройки" data-slot="folded-settings" onClick=${() => openSettings()} />
          </span>`}
          ${shown && html`<${IconButton} icon="chevron-left" label="На главную (чат останется в списке)" class="home-back"
            data-slot="home-back" onClick=${goHome} />`}
          <strong class=${"title" + (shown ? " title-interactive" : "")} data-slot="title"
            role=${shown ? "button" : null} tabindex=${shown ? "0" : null}
            aria-label=${shown ? `Чат: ${((chat.chat.title || "").trim() || "Без названия")}. Нажмите, чтобы переименовать` : "White House"}
            title=${shown ? `${((chat.chat.title || "").trim() || "Без названия")} (нажмите, чтобы изменить)` : ""}
            onClick=${shown ? onTitleClick : null}
            onKeyDown=${shown ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onTitleClick(); } } : null}>
            ${shown ? ((chat.chat.title || "").trim() || "Без названия") : "White House"}
          </strong>
          <span class="spacer"></span>
          <nav class="phone-nav only-mobile" data-slot="phone-nav" aria-label="Разделы">
            <${IconButton} icon="chevron-down" label="Свернуть верхнюю панель" class="fold-top" data-slot="phone-fold"
              onClick=${() => { setSheet(null); setMenuFolded(true); }} />
            ${SECTIONS.map(([id, icon, label]) => html`<${IconButton} key=${id} icon=${icon} label=${label}
              aria-pressed=${sheet === id} aria-haspopup="true" aria-expanded=${sheet === id}
              onClick=${() => setSheet(sheet === id ? null : id)} />`)}
            <${IconButton} icon="search" label="Поиск по всем чатам" data-slot="phone-search" onClick=${openSearch} />
            <${IconButton} icon="wand" label=${assistantBusy ? "Помощник: есть работа" : "Помощник"}
              class=${assistantBusy ? "has-mark" : ""}
              onClick=${assistantBusy ? openAssistantMark : openAssistant} />
            <${IconButton} icon="settings" label="Настройки" onClick=${() => openSettings()} />
          </nav>
          ${shown && html`<form class=${"search" + (searching ? " open" : "")} onSubmit=${search} role="search">
            <${Icon} name="search" />
            <input name="query" type="search" enterkeyhint="search" placeholder="Поиск" aria-label="Поиск по чату" />
          </form>`}
          ${assistantBusy && html`<button class=${"assistant-mark only-wide" + (assistantState.waiting.length ? " waiting" : "")}
            data-slot="assistant-mark" title="Открыть помощника" onClick=${openAssistantMark}>
            <${Icon} name="wand" /> ${assistantState.waiting.length ? "Ждёт вас" : "Работает"}</button>`}
          ${shown && html`<${SpendingBar} summary=${spend} />`}
          ${shown && html`<${ProfileSwitch} refreshKey=${String(showSettings)} onChanged=${refresh} onError=${onError} />`}
          ${shown && html`<${ModelChip} chat=${chat.chat} settings=${settings} onChanged=${chat.refreshInfo} onError=${onError} />`}
          ${shown && regexShown.waiting > 0 && html`<button type="button" class="plugin-chip error" data-slot="regex-waiting"
            title=${`Регекс-правила этого чата пришли с карточкой или пресетом и не работают, пока вы их не разрешите (${regexShown.waiting}). Открыть «Регекс»`}
            onClick=${() => openSettings("regex")}>Регекс ждёт: ${regexShown.waiting}</button>`}
          ${shown && html`<${StatusChips} chips=${statusChips()} />`}
          ${shown && html`<${WindowButtons} windows=${pluginUi.windows} />`}
          ${shown && html`<${PanelButtons} panels=${pluginUi.panels} />`}
          ${shown && html`<${IconButton} icon="users" label="Участники чата и пресет" class="only-wide"
            data-slot="members-button" onClick=${() => setShowMembers(true)} />`}
          ${shown && html`<${IconButton} icon="notebook-pen" label="Заметки автора" class="only-wide"
            data-slot="notes-button" onClick=${() => setShowNotes(true)} />`}
          ${shown && html`<${MenuButton} label="Действия с чатом" class="export-chat" items=${chatMenu} />`}
        </div>
        ${shown
          ? html`
            ${chat.messages.filter((m) => !m.hidden).length === 0 && html`<div class="empty" data-slot="empty">
              <${EmptyNote} icon="message-square-text"
                title=${character ? `Беседа с «${character.name}»` : "Сообщений пока нет"}
                hint=${character
                  ? "Напишите реплику в поле ввода или нажмите «Начать беседу», чтобы персонаж заговорил первым."
                  : "Напишите первое сообщение в поле ввода внизу."}>
                ${character && html`<div class="empty-actions">
                  <button type="button" class="primary empty-btn" disabled=${chat.running}
                    onClick=${() => chat.continue(role)}>Начать беседу</button>
                </div>`}
              <//></div>`}
            <${VirtualList} items=${chat.messages.filter((m) => !m.hidden)} renderItem=${renderItem}
              onReachTop=${chat.loadOlder} resetKey=${chatId} layoutKey=${chatView} jumpTo=${jumpTo}
              stickTo=${(chat.messages.find((m) => m.pending) || {}).vkey || null}
              status=${chat.starting ? "Собираю запрос…" : ""} />
            <div class="chat-actions" data-slot="chat-actions">
              ${(() => {
                // As SillyTavern's Continue: the model goes on with its last reply (the same message). After your own
                // message it simply answers. A new message from someone is the «Ответит: …» buttons.
                const last = chat.messages.filter((m) => !m.hidden && !m.pending).slice(-1)[0];
                const extend = last && last.role === "assistant";
                return html`<button disabled=${chat.running}
                  onClick=${() => (extend ? chat.continueMessage() : chat.continue(role))}
                  title=${extend ? "Модель допишет свой последний ответ (как Continue в SillyTavern)"
                    : "Модель ответит на ваше сообщение"}>Продолжить</button>`;
              })()}
              ${cast.length > 1 && html`<button data-slot="talk-button" disabled=${chat.running} onClick=${() => chat.talk()}
                title="Персонажи отвечают друг другу сами, пока не наберётся предел из «Кто говорит» или пока вы не нажмёте «Стоп»">Пусть поговорят</button>`}
              ${cast.length > 0 && html`<div class="speakers" data-slot="speakers" role="group" aria-label="Ответит">
                <span class="muted">Ответит:</span>
                ${cast.map((m) => html`<button key=${m.character_id} class=${m.muted ? "muted-member" : ""}
                  disabled=${chat.running} onClick=${() => chat.continue("assistant", m.character_id)}
                  title=${`Пусть ответит ${m.name}` + (m.muted ? " (молчит, но ответит, раз просите)" : "")}>
                  ${m.has_avatar && html`<img class="speaker-avatar" src=${`/api/characters/${m.character_id}/avatar`} alt="" />`}
                  ${m.name}</button>`)}
              </div>`}
              ${selecting && html`<div class="bulk-roles" role="group" aria-label="Смена ролей у нескольких сообщений">
                <span>Отмечено: ${selected.size}</span>
                <input value=${numbers} placeholder="Номера: 1-5, 8" aria-label="Номера сообщений"
                  onInput=${(e) => setNumbers(e.target.value)} />
                <${Select} value=${bulkRole} aria-label="Новая роль" onChange=${(e) => setBulkRole(e.target.value)}>
                  ${ROLES.map((r) => html`<option key=${r.id} value=${r.id}>${r.label}</option>`)}
                </${Select}>
                <button class="primary" disabled=${chat.running} onClick=${() => applyRoles(false)}>Применить</button>
                <button disabled=${chat.running}
                  onClick=${async () => {
                    const roleLabel = ROLES.find((r) => r.id === bulkRole)?.label || bulkRole;
                    if (await ask("Сменить роль у всех сообщений текущей ветки?", {
                      ok: "Сменить",
                      hint: `Все реплики активной ветки получат роль «${roleLabel}». Прежние роли будут заменены.`,
                    })) applyRoles(true);
                  }}>Всем сообщениям</button>
                <button onClick=${toggleSelecting}>Готово</button>
              </div>`}
              ${searchResults && html`<div class="search-results" data-slot="search-results">
                <div class="panel-head"><strong>${searchResults.length ? `Найдено: ${searchResults.length}` : "Ничего не найдено"}</strong>
                  <span class="spacer"></span>
                  <${IconButton} icon="x" label="Закрыть" onClick=${() => { setSearchResults(null); setSearching(false); }} /></div>
                ${searchResults.map((m) => html`<button key=${m.id} class="result-line" disabled=${chat.running} data-slot="search-result"
                  title=${m.on_path ? "Показать это сообщение" : "Сообщение в другой ветке: откроется она"}
                  onClick=${() => showFound(m)}><span class="msg-num">${m.depth + 1}</span>
                  ${!m.on_path && html`<${Icon} name="git-branch" />`} <span class="result-text">${m.content.slice(0, 80)}</span></button>`)}
              </div>`}
              ${branchResults && html`<div class="branch-results" data-slot="branch-results">
                <div class="panel-head"><strong>Ветки: ${branchResults.branches.length}</strong>
                  <span class="spacer"></span>
                  <${IconButton} icon="x" label="Закрыть" onClick=${() => setBranchResults(null)} /></div>
                ${!branchResults.branches.length && branchResults.messageId === lastId && html`<span class="branch-hint muted">
                  У последнего сообщения других веток нет. Чтобы начать ветку, откройте «Ветки» у более раннего сообщения.</span>`}
                ${branchResults.branches.map((m) => html`<button key=${m.id} class="result-line" disabled=${chat.running}
                  onClick=${() => selectBranch(m.id)}><span class="result-text">${m.content.slice(0, 80)}</span></button>`)}
                ${branchResults.messageId !== lastId && html`<button class="primary" data-slot="branch-from-here"
                  disabled=${chat.running} onClick=${() => branchFrom(branchResults.messageId)}>Новая ветка отсюда</button>
                  <span class="branch-hint muted">Чат продолжится от этого сообщения. Всё, что было после него, останется в старой ветке, она будет в этом же списке.</span>`}
              </div>`}
            </div>
            <${Composer} running=${chat.running} role=${role} onRole=${chooseRole}
              onSend=${(text, images) => {
                if (isCommand(text) && !images.length) return runCommand(text);
                emit("send", { text });
                // The name the server will give the message (the chat's «me» character, else the setting), so the
                // temporary message is replaced without a visible change.
                const me = members.find((m) => m.is_me);
                return chat.send(text, role, images, (me && me.name) || settings.user_name || "User");
              }}
              onStop=${chat.stop} onError=${onError} />`
          : html`<div class="empty" data-slot="empty">
              ${chatId && !chat.failed && html`<${Pending} />`}
              ${chatId && chat.failed && html`<${Pending} error=${chat.failed} onRetry=${() => location.reload()} />`}
              ${!chatId && !loaded && html`<${Pending} />`}
              ${!chatId && loaded && !settings.active_connection && html`<${EmptyNote} icon="settings" title="Подключите модель"
                hint="White House общается через вашу модель. Укажите адрес и ключ доступа в настройках подключений.">
                <div class="empty-actions">
                  <button class="primary empty-btn" onClick=${() => openSettings("connections")}>Добавить подключение</button>
                </div><//>`}
              ${!chatId && loaded && settings.active_connection && chats.length === 0 && characters.length === 0 && html`<${EmptyNote}
                icon="sparkles" title="Добро пожаловать в White House"
                hint="Создайте первого персонажа или загрузите готовую карточку (PNG или JSON), чтобы начать ролевую беседу.">
                <div class="empty-actions">
                  <button class="primary empty-btn" onClick=${() => setEditingCharacter(null)}>Создать персонажа</button>
                  <button class="empty-btn" onClick=${() => {
                    const input = document.createElement("input");
                    input.type = "file";
                    input.accept = ".json,.png,application/json,image/png";
                    input.onchange = (e) => { const f = e.target.files[0]; if (f) importCard(f); };
                    input.click();
                  }}>Импорт карточки</button>
                  <button class="empty-btn" onClick=${() => newChat(null)}>Чат без персонажа</button>
                </div><//>`}
              ${!chatId && loaded && settings.active_connection && chats.length === 0 && characters.length > 0 && html`<${EmptyNote}
                icon="message-square" title="Начните первую беседу"
                hint="Персонажи уже созданы. Начните диалог с любым из них или создайте свободный чат.">
                <div class="empty-actions">
                  <button class="primary empty-btn" onClick=${() => {
                    if (window.matchMedia("(max-width: 720px)").matches) setSheet("characters");
                    else newChat(characters[0].id);
                  }}>${window.matchMedia("(max-width: 720px)").matches ? "Выбрать персонажа" : `Начать с «${characters[0].name}»`}</button>
                  <button class="empty-btn" onClick=${() => newChat(null)}>Чат без персонажа</button>
                </div><//>`}
              ${!chatId && loaded && settings.active_connection && chats.length > 0 && html`<${EmptyNote}
                icon="message-square" title="Выберите чат"
                hint="Откройте беседу из списка или начните новую историю.">
                <div class="empty-actions">
                  <button class="primary empty-btn" onClick=${() => {
                    if (window.matchMedia("(max-width: 720px)").matches) setSheet("chats");
                    else newChat(null);
                  }}>${window.matchMedia("(max-width: 720px)").matches ? "Список чатов" : "Новый чат"}</button>
                  <button class="empty-btn" onClick=${() => {
                    if (window.matchMedia("(max-width: 720px)").matches) setSheet("characters");
                    else setEditingCharacter(null);
                  }}>${window.matchMedia("(max-width: 720px)").matches ? "Персонажи" : "Новый персонаж"}</button>
                </div><//>`}
            </div>`}
        <div class="overlay-layer" data-slot="overlay"></div>
      </main>
      ${shown && html`<${Panels} panels=${pluginUi.panels} />`}
      ${shown && html`<${PluginWindows} windows=${pluginUi.windows} />`}
      ${showSettings && html`<${Settings} settings=${settings} themes=${themes.themes}
        initialTab=${typeof showSettings === "string" ? showSettings : undefined}
        deletedThemes=${themes.deleted} characters=${characters} onClose=${() => { setShowSettings(false); reloadSpending(); loadPlugins(); }}
        onChanged=${refresh} onError=${onError}
        onOpenAssistant=${(id) => { setAssistantConv(id); setShowSettings(false); openAssistant(); }} />`}
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
      ${showSearch && html`<${Search} characters=${characters} onOpen=${openFound} onError=${onError}
        onClose=${() => setShowSearch(false)} />`}
      ${showAssistant && html`<${Assistant} chatId=${shown ? chatId : null} openConv=${assistantConv} openMsg=${assistantMsg} onError=${onError}
        onOpenSkills=${() => setShowSettings("skills")}
        onChanged=${assistantChanged} onClose=${() => setShowAssistant(false)} />`}
      ${asks.length > 0 && html`<${PluginAsks} asks=${asks} onError=${onError} />`}
      <${DialogHost} />
      ${toast && html`<div class=${"toast" + (toastClosing ? " closing" : "")} data-slot="toast" data-kind=${toast.kind}
        role=${toast.kind === "error" ? "alert" : "status"} tabindex="0"
        onKeyDown=${(e) => { if (e.key === "Enter" || e.key === " " || e.key === "Escape") { e.preventDefault(); dismiss(); } }}
        onClick=${(e) => { if (!e.target.closest(".toast-action")) dismiss(); }}>
        ${toast.kind === "ok" && html`<${Icon} name="check" />`}
        ${toast.kind === "error" && html`<${Icon} name="triangle-alert" />`}
        ${toast.kind === "info" && html`<${Icon} name="info" />`}
        <span class="toast-text">${toast.text}</span>
        ${toast.action && html`<button type="button" class="toast-action"
          onClick=${(e) => { e.stopPropagation(); toast.action.onClick(); dismiss(); }}>${toast.action.label}</button>`}
        <button type="button" class="toast-close" aria-label="Закрыть"
          onClick=${(e) => { e.stopPropagation(); dismiss(); }}><${Icon} name="x" size="16" /></button></div>`}
    </div>
  `;
}

applyCachedLook();
render(html`<${App} />`, document.getElementById("root"));
