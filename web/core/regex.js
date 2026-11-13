// How messages look on the screen after the person's regex rules (stage 11, ADR 0017).
// The server runs the rules (the patterns are JavaScript-style, the engine is Python, in a process it can stop)
// and answers {text, changed, nodes}: `nodes` is a cleaned tree when a rule wrote HTML; Message builds elements
// from it (components/Regex.js), never markup. This hook asks for the messages in view, remembers the answers,
// and while a reply is being written asks again every STREAM_MS, showing the last answer in between so the text
// does not jump back to the raw version. With the setting «Применять сразу, пока модель пишет» the server also
// applies the rules that change the saved text to the reply being written (summary.live): a chat with only such
// rules asks about streaming replies alone, the finished messages are shown as stored. In safe mode (/?safe) the rules are not applied: the text is shown as stored.
import { useEffect, useRef, useState } from "../vendor/preact-htm.mjs";
import { api } from "./api.js";
import { SAFE_MODE } from "./theme.js";

const STREAM_MS = 350;
const BATCH = 150;
const SHOWN_ROLES = new Set(["user", "assistant"]);

function hash(text) {
  let h = 5381;
  for (let i = 0; i < text.length; i += 1) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
  return `${text.length}.${h}`;
}

// Returns shownFor(message, streamText) -> null (show the stored text) | {pending: true} | {text, nodes};
// shownFor.waiting = how many rules of the chat wait for «Разрешить».
export function useRegexDisplay(chatId, messages, streams) {
  const [summary, setSummary] = useState(null);
  const [, bump] = useState(0);
  const done = useRef(new Map());   // key -> result
  const last = useRef(new Map());   // message id -> the latest result (shown while a new one is on its way)
  const failed = useRef(false);
  const token = useRef(0);

  useEffect(() => {
    done.current = new Map();
    last.current = new Map();
    failed.current = false;
    if (SAFE_MODE || !chatId) { setSummary(null); return undefined; }
    const load = () => api.get(`/regex/summary?chat_id=${encodeURIComponent(chatId)}`)
      .then((s) => { setSummary(s); }).catch(() => setSummary(null));
    load();
    window.addEventListener("tavern:regex", load);
    return () => window.removeEventListener("tavern:regex", load);
  }, [chatId]);

  const screen = Boolean(summary && summary.display > 0);
  const on = screen || Boolean(summary && summary.live);
  const lastDepth = messages.length ? messages[messages.length - 1].depth : 0;
  const keyOf = (m, text) => `${summary.stamp}|${m.id}|${hash(text)}|${summary.depth_sensitive ? lastDepth - m.depth : 0}`;
  const textOf = (m) => (streams && streams[m.id] ? streams[m.id].text : m.content) || "";

  useEffect(() => {
    if (!on) return undefined;
    const wanted = [];
    for (const m of messages) {
      if (!SHOWN_ROLES.has(m.role)) continue;
      const text = textOf(m);
      const stream = Boolean(streams && streams[m.id]);
      if (!screen && !stream) continue; // only «live» rules: finished messages are shown as stored
      const key = keyOf(m, text);
      if (done.current.has(key)) continue;
      wanted.push({ key, id: key, m, text, stream });
    }
    if (!wanted.length) return undefined;
    const mine = ++token.current;
    const timer = setTimeout(async () => {
      try {
        for (let i = 0; i < wanted.length; i += BATCH) {
          const part = wanted.slice(i, i + BATCH);
          const answer = await api.post("/regex/display", {
            chat_id: chatId,
            items: part.map((w) => ({ id: w.key, text: w.text, role: w.m.role, depth: lastDepth - w.m.depth, stream: w.stream })),
          });
          if (mine !== token.current) return;
          for (const w of part) {
            const result = answer.results[w.key];
            if (!result) continue;
            done.current.set(w.key, result);
            last.current.set(w.m.id, result);
          }
        }
        if (done.current.size > 2000) done.current = new Map([...done.current].slice(-1000));
        bump((n) => n + 1);
      } catch (e) {
        failed.current = true; // the rules cannot be reached: show the stored text rather than nothing
        bump((n) => n + 1);
      }
    }, wanted.some((w) => w.stream) ? STREAM_MS : 30);
    return () => clearTimeout(timer);
  }, [on, screen, summary && summary.stamp, messages, streams]);

  const shown = (message, streamText) => {
    if (!on || failed.current || !SHOWN_ROLES.has(message.role)) return null;
    const streaming = streamText !== null && streamText !== undefined;
    if (!screen && !streaming) return null;
    const text = streaming ? streamText : message.content || "";
    const found = done.current.get(keyOf(message, text));
    if (found) return found.changed ? found : null;
    const before = last.current.get(message.id);
    if (before && streaming) return before.changed ? before : null;
    return { pending: true };
  };
  // Rules of this chat that are on but wait for «Разрешить» (came with a card or a preset): the top bar says so.
  shown.waiting = (summary && summary.pending) || 0;
  return shown;
}
