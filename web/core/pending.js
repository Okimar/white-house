// A message the user just sent, shown at once and before the server has confirmed it (an "optimistic" message).
// Pure helpers (no imports), so the logic is tested in Node (tests/unit/test_web_pending.py).
// The temporary message has the id "pending:N" and `pending: true`; its `vkey` is the list identity (VirtualList keys
// and measures items by `vkey || id`). The real message that replaces it inherits that `vkey`, so the row is not
// remounted and its measured height stays: nothing jumps.

let counter = 0;

export function makePending(messages, { text, images = [], name = "" }) {
  counter += 1;
  const last = messages.length ? messages[messages.length - 1] : null;
  const id = `pending:${counter}`;
  return {
    id, vkey: id, pending: true, role: "user", name, content: text, character_id: null,
    depth: last ? last.depth + 1 : 0, parent_id: last ? last.id : null,
    swipe_count: 1, active_swipe: 0, swipe_meta: images.length ? { images } : {},
    hidden: false, exclude_from_prompt: false,
  };
}

export const isPending = (message) => Boolean(message && message.pending);

// The server's user message arrives (event `start` / `turn_start`, or the answer of a send with no run): it takes the
// place of the temporary one; without one it is added or replaced by id as before.
export function settle(messages, real) {
  const at = messages.findIndex((m) => m.pending);
  if (at < 0 || real.role !== "user") return upsertById(messages, real);
  const copy = messages.filter((m, i) => i === at || m.id !== real.id);
  copy[copy.indexOf(messages[at])] = { ...real, vkey: messages[at].vkey };
  return copy;
}

export function upsertById(list, message) {
  const i = list.findIndex((m) => m.id === message.id);
  if (i < 0) return [...list, message];
  const copy = list.slice();
  copy[i] = message;
  return copy;
}

// The send failed or the run ended without a confirmation: the temporary message goes away.
export function dropPending(messages, id = null) {
  if (!messages.some((m) => m.pending && (id === null || m.id === id))) return messages;
  return messages.filter((m) => !(m.pending && (id === null || m.id === id)));
}
