// State and actions of one open chat: loading pages, sending, following a generation run.
import { useState, useRef, useCallback, useEffect } from "../vendor/preact-htm.mjs";
import { spendingChanged } from "./spending.js";
import { api, followRun } from "./api.js";
import { emit } from "./theme.js";
import { notify } from "./notify.js";
import { dropPending, makePending, settle, upsertById } from "./pending.js";

const PAGE = 50;
const EMPTY = { chat: null, messages: [], hasMore: false, running: false };

const upsert = upsertById;

export function useChat(chatId, onError) {
  const [state, setState] = useState(EMPTY);
  // Replies being written: id -> { text, thinking }. Several at once when everyone answers together.
  const [streams, setStreams] = useState({});
  const reloadRef = useRef(null);
  const stateRef = useRef(state);
  stateRef.current = state;
  const closeRef = useRef(null);
  const loadingOlder = useRef(false);
  const chatRef = useRef(chatId);
  chatRef.current = chatId;

  const stopFollowing = () => {
    if (closeRef.current) closeRef.current();
    closeRef.current = null;
  };

  const follow = useCallback((runId) => {
    stopFollowing();
    const buffers = {}; // id -> { text, thinking } not drawn yet
    let scheduled = false;
    let replyId = null; // the reply of the last "start": events without an id belong to it
    // Deltas arrive faster than the screen refreshes: batch them into one render per frame.
    const flush = () => {
      scheduled = false;
      const pending = Object.entries(buffers);
      for (const [id] of pending) delete buffers[id];
      if (!pending.length) return;
      setStreams((s) => {
        const next = { ...s };
        for (const [id, b] of pending) {
          if (next[id]) next[id] = { text: next[id].text + b.text, thinking: next[id].thinking + b.thinking };
        }
        return next;
      });
    };
    const drop = (id) => {
      delete buffers[id];
      setStreams((s) => { const { [id]: _, ...rest } = s; return rest; });
    };
    const finish = (event) => {
      for (const id of Object.keys(buffers)) delete buffers[id];
      setStreams({});
      closeRef.current = null;
      if (event.reload) {
        // A turn of several replies: a round may have reordered or joined them on the server.
        setState((s) => ({ ...s, running: false }));
        if (reloadRef.current) reloadRef.current().catch((e) => onError(e.message));
      } else {
        setState((s) => ({
          ...s,
          running: false,
          messages: event.message
            ? upsert(dropPending(s.messages), event.message)
            : dropPending(s.messages).filter((m) => m.id !== replyId),
        }));
      }
      if (event.type === "error") onError(event.error.message);
      spendingChanged();
      // Parallel requests in "pick" mode: show every version side by side to choose one.
      if (event.type === "done" && event.pick && event.message) setState((s) => ({ ...s, pick: event.message.id }));
    };
    closeRef.current = followRun(runId, (event) => {
      if (event.type === "turn_start") {
        setState((s) => ({ ...s, running: true,
          messages: event.user_message ? settle(s.messages, event.user_message) : s.messages }));
      } else if (event.type === "start") {
        replyId = event.message.id;
        setStreams((s) => ({ ...s, [replyId]: { text: "", thinking: "" } }));
        setState((s) => {
          let messages = s.messages;
          if (event.user_message) messages = settle(messages, event.user_message);
          return { ...s, running: true, messages: upsert(messages, event.message) };
        });
      } else if (event.type === "delta" || event.type === "reasoning") {
        const b = (buffers[event.id || replyId] ||= { text: "", thinking: "" });
        if (event.type === "delta") b.text += event.text;
        else b.thinking += event.text;
        if (!scheduled) {
          scheduled = true;
          requestAnimationFrame(flush);
        }
      } else if (event.type === "reply_done") {
        drop(event.message.id);
        setState((s) => ({ ...s, messages: upsert(s.messages, event.message) }));
      } else if (event.type === "reply_error") {
        drop(event.id);
        onError(event.error.message);
      } else if (event.type === "spend_warning") {
        notify(event.text, "info");
      } else if (event.type === "meaning_failed") {
        notify(event.text, "error"); // meaning search failed; the reply goes on by keywords
      } else if (event.type === "variants_failed") {
        onError("Не ответили: " + event.failed.map((f) => `${f.label} (${f.error})`).join("; "));
      } else if (["done", "stopped", "error"].includes(event.type)) {
        finish(event);
      }
      if (event.type === "trace") emit("trace", { ...event, chatId }); // progress of plugin reply writers
    });
  }, [onError]);

  useEffect(() => {
    stopFollowing();
    setStreams({});
    setState(EMPTY);
    if (!chatId) return undefined;
    let dead = false;
    api.get(`/chats/${chatId}?limit=${PAGE}`).then((data) => {
      if (dead) return;
      setState({ chat: data.chat, messages: data.messages, hasMore: data.has_more, running: !!data.run });
      if (data.run) follow(data.run.id); // the page was reloaded mid-generation: replay it
    }).catch((e) => {
      if (dead) return;
      // A remembered chat that was deleted meanwhile: the shell drops it once the chat list is in, no error to show.
      if (e.status !== 404) onError(e.message);
      setState((s) => ({ ...s, failed: e.message }));
    });
    return () => { dead = true; stopFollowing(); };
  }, [chatId]);

  // false tells the caller it failed (the error is already shown), so a draft can be kept.
  const guard = (promise) => promise.catch((e) => { onError(e.message); return false; });
  // After the active branch changed on the server: reload its newest page.
  const reload = () => api.get(`/chats/${chatId}?limit=${PAGE}`).then((d) =>
    setState((s) => ({ ...s, chat: d.chat, messages: d.messages, hasMore: d.has_more })));
  reloadRef.current = reload;

  return {
    ...state,
    streams,
    loadOlder: useCallback(async () => {
      const s = stateRef.current;
      if (loadingOlder.current || !s.hasMore || !s.messages.length) return;
      loadingOlder.current = true;
      try {
        const page = await api.get(
          `/chats/${chatId}/messages?limit=${PAGE}&before_depth=${s.messages[0].depth}`);
        setState((cur) => ({ ...cur, hasMore: page.has_more, messages: [...page.messages, ...cur.messages] }));
      } catch (e) {
        onError(e.message);
      } finally {
        loadingOlder.current = false;
      }
    }, [chatId, onError]),
    // replyRole: who the generated message is saved as (user / assistant / system).
    // images: picture names from the library, sent with the message for the model to see.
    // author: the name shown on the message while the server has not confirmed it.
    // The message appears at once as a temporary one (core/pending.js); the server's replaces it in place, and a
    // failure removes it (the caller gets false and puts the text back).
    // With two or more characters and the order "manual" only the message is saved (no run): the user picks who answers.
    send: async (text, replyRole = "assistant", images = [], author = "") => {
      const temp = makePending(stateRef.current.messages, { text, images, name: author });
      const sentIn = chatId;
      setState((s) => ({ ...s, messages: [...s.messages, temp] }));
      const result = await guard(api.post(`/chats/${chatId}/send`,
        { text, reply_role: replyRole, images }).then((r) => {
        if (chatRef.current !== sentIn) return undefined; // another chat is open now: its page reattaches to the run
        if (r.run_id) return follow(r.run_id);
        return setState((s) => ({ ...s, messages: settle(s.messages, r.message) }));
      }));
      if (result === false && chatRef.current === sentIn) {
        setState((s) => ({ ...s, messages: dropPending(s.messages, temp.id) }));
      }
      return result;
    },
    // speaker: the character who writes it ("ответь ты"); default the chat's order.
    continue: (role = "assistant", speaker = null) => guard(api.post(`/chats/${chatId}/continue`,
      speaker ? { role, speaker } : { role }).then((r) => follow(r.run_id))),
    // «Пусть поговорят»: the characters answer one another (count: how many replies; default the chat's).
    talk: (count = null) => guard(api.post(`/chats/${chatId}/talk`, count ? { count } : {})
      .then((r) => follow(r.run_id))),
    regenerate: () => guard(api.post(`/chats/${chatId}/regenerate`).then((r) => follow(r.run_id))),
    stop: () => guard(api.post(`/chats/${chatId}/stop`)),
    closePick: () => setState((s) => ({ ...s, pick: null })),
    search: (query) => api.get(`/chats/${chatId}/search?q=${encodeURIComponent(query)}`),
    branches: (messageId) => api.get(`/messages/${messageId}/branches`),
    edit: (id, content) => guard(api.put(`/messages/${id}`, { content }).then((m) =>
      setState((s) => ({ ...s, messages: upsert(s.messages, m) })))),
    swipe: (id, index) => guard(api.put(`/messages/${id}/swipe`, { index }).then((m) =>
      setState((s) => ({ ...s, messages: upsert(s.messages, m) })))),
    // target: { ids } | { numbers: "1-5, 8" } | { all: true }. Changed messages replace loaded ones.
    setRoles: (role, target) => guard(api.put(`/chats/${chatId}/roles`, { role, ...target }).then((r) =>
      setState((s) => ({ ...s, messages: r.messages.reduce(
        (list, m) => (list.some((x) => x.id === m.id) ? upsert(list, m) : list), s.messages) })))),
    // Chat fields (main character, preset) changed elsewhere: refresh them without touching messages.
    refreshInfo: () => guard(api.get(`/chats/${chatId}?limit=1`).then((d) =>
      setState((s) => ({ ...s, chat: d.chat })))),
    // Something else added messages (a plugin's command): show the newest page.
    reload: () => guard(reload()),
    selectBranch: (messageId) => guard(api.post(`/chats/${chatId}/branch`, { leaf_id: messageId }).then(reload)),
    // A new branch from this message: the view ends here, what comes next starts beside the old one.
    branchFrom: (messageId) => guard(api.post(`/chats/${chatId}/branch`, { leaf_id: messageId, exact: true }).then(reload)),
    // Make a found message part of the view: its branch if another one is open, older pages if not loaded.
    reveal: (message) => guard((async () => {
      let oldest = stateRef.current.messages[0]?.depth;
      if (!message.on_path) {
        await api.post(`/chats/${chatId}/branch`, { leaf_id: message.id });
        const d = await api.get(`/chats/${chatId}?limit=${PAGE}`);
        setState((s) => ({ ...s, chat: d.chat, messages: d.messages, hasMore: d.has_more }));
        oldest = d.messages[0]?.depth;
      }
      if (oldest !== undefined && message.depth < oldest) {
        const page = await api.get(`/chats/${chatId}/messages?limit=${oldest - message.depth + 5}&before_depth=${oldest}`);
        setState((s) => ({ ...s, hasMore: page.has_more, messages: [...page.messages, ...s.messages] }));
      }
    })()),
    remove: (id) => guard(api.del(`/messages/${id}`).then(reload)),
  };
}
