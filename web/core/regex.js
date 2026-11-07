// How messages look on the screen after the person's regex rules (stage 11, ADR 0017).
// The server runs the rules (the patterns are JavaScript-style, the engine is Python, in a process it can stop)
// and answers {text, changed, nodes}: `nodes` is a cleaned tree when a rule wrote HTML; Message builds elements
// from it (components/Regex.js), never markup. This hook asks for the messages in view, remembers the answers,
// and while a reply is being written asks again every STREAM_MS, showing the last answer in between so the text
// does not jump back to the raw version. In safe mode (/?safe) the rules are not applied: the text is shown as stored.
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

// Returns shownFor(message, streamText) -> null (show the stored text) | {pending: true} | {text, nodes}.
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

  const on = Boolean(summary && summary.display > 0);
  const lastDepth = messages.length ? messages[messages.length - 1].depth : 0;
  const keyOf = (m, text) => `${summary.stamp}|${m.id}|${hash(text)}|${summary.depth_sensitive ? lastDepth - m.depth : 0}`;
  const textOf = (m) => (streams && streams[m.id] ? streams[m.id].text : m.content) || "";

  useEffect(() => {
    if (!on) return undefined;
    const wanted = [];
    for (const m of messages) {
      if (!SHOWN_ROLES.has(m.role)) continue;
      const text = textOf(m);
      const key = keyOf(m, text);
      if (done.current.has(key)) continue;
      wanted.push({ key, id: key, m, text, stream: Boolean(streams && streams[m.id]) });
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
  }, [on, summary && summary.stamp, messages, streams]);

  return (message, streamText) => {
    if (!on || failed.current || !SHOWN_ROLES.has(message.role)) return null;
    const text = streamText !== null && streamText !== undefined ? streamText : message.content || "";
    const found = done.current.get(keyOf(message, text));
    if (found) return found.changed ? found : null;
    const before = last.current.get(message.id);
    if (before && streamText !== null && streamText !== undefined) return before.changed ? before : null;
    return { pending: true };
  };
}
