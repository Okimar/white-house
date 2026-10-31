// State and actions of one open chat: loading pages, sending, following a generation run.
import { useState, useRef, useCallback, useEffect } from "../vendor/preact-htm.mjs";
import { spendingChanged } from "./spending.js";
import { api, followRun } from "./api.js";

const PAGE = 50;
const EMPTY = { chat: null, messages: [], hasMore: false, running: false };

function upsert(list, message) {
  const i = list.findIndex((m) => m.id === message.id);
  if (i < 0) return [...list, message];
  const copy = list.slice();
  copy[i] = message;
  return copy;
}

export function useChat(chatId, onError) {
  const [state, setState] = useState(EMPTY);
  const [stream, setStream] = useState(null); // { id, text } of the reply being written
  const stateRef = useRef(state);
  stateRef.current = state;
  const closeRef = useRef(null);
  const loadingOlder = useRef(false);

  const stopFollowing = () => {
    if (closeRef.current) closeRef.current();
    closeRef.current = null;
  };

  const follow = useCallback((runId) => {
    stopFollowing();
    let buffer = "";
    let thinking = ""; // the model's reasoning, shown folded above the reply
    let scheduled = false;
    let replyId = null;
    // Deltas arrive faster than the screen refreshes: batch them into one render per frame.
    const flush = () => {
      scheduled = false;
      const text = buffer;
      const thought = thinking;
      buffer = "";
      thinking = "";
      if (text || thought) {
        setStream((s) => (s ? { ...s, text: s.text + text, thinking: s.thinking + thought } : s));
      }
    };
    const finish = (event) => {
      buffer = "";
      thinking = "";
      setStream(null);
      closeRef.current = null;
      setState((s) => ({
        ...s,
        running: false,
        messages: event.message
          ? upsert(s.messages, event.message)
          : s.messages.filter((m) => m.id !== replyId),
      }));
      if (event.type === "error") onError(event.error.message);
      spendingChanged();
      // Parallel requests in "pick" mode: show every version side by side to choose one.
      if (event.type === "done" && event.pick && event.message) setState((s) => ({ ...s, pick: event.message.id }));
    };
    closeRef.current = followRun(runId, (event) => {
      if (event.type === "start") {
        replyId = event.message.id;
        setStream({ id: replyId, text: "", thinking: "" });
        setState((s) => {
          let messages = s.messages;
          if (event.user_message) messages = upsert(messages, event.user_message);
          return { ...s, running: true, messages: upsert(messages, event.message) };
        });
      } else if (event.type === "delta" || event.type === "reasoning") {
        if (event.type === "delta") buffer += event.text;
        else thinking += event.text;
        if (!scheduled) {
          scheduled = true;
          requestAnimationFrame(flush);
        }
      } else if (event.type === "spend_warning") {
        onError(event.text);
      } else if (event.type === "variants_failed") {
        onError("Не ответили: " + event.failed.map((f) => `${f.label} (${f.error})`).join("; "));
      } else if (["done", "stopped", "error"].includes(event.type)) {
        finish(event);
      }
      // "trace" (progress of plugin generators) is ignored until the stage 8 progress panel.
    });
  }, [onError]);

  useEffect(() => {
    stopFollowing();
    setStream(null);
    setState(EMPTY);
    if (!chatId) return undefined;
    let dead = false;
    api.get(`/chats/${chatId}?limit=${PAGE}`).then((data) => {
      if (dead) return;
      setState({ chat: data.chat, messages: data.messages, hasMore: data.has_more, running: !!data.run });
      if (data.run) follow(data.run.id); // the page was reloaded mid-generation: replay it
    }).catch((e) => !dead && onError(e.message));
    return () => { dead = true; stopFollowing(); };
  }, [chatId]);

  const guard = (promise) => promise.catch((e) => { onError(e.message); });
  // After the active branch changed on the server: reload its newest page.
  const reload = () => api.get(`/chats/${chatId}?limit=${PAGE}`).then((d) =>
    setState((s) => ({ ...s, chat: d.chat, messages: d.messages, hasMore: d.has_more })));

  return {
    ...state,
    stream,
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
    send: (text, replyRole = "assistant", images = []) => guard(api.post(`/chats/${chatId}/send`,
      { text, reply_role: replyRole, images }).then((r) => follow(r.run_id))),
    continue: (role = "assistant") => guard(api.post(`/chats/${chatId}/continue`, { role })
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
    selectBranch: (messageId) => guard(api.post(`/chats/${chatId}/branch`, { leaf_id: messageId }).then(reload)),
    remove: (id) => guard(api.del(`/messages/${id}`).then(reload)),
  };
}
