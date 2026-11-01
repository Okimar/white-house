// 🌐 Перевод: a 🌐 button on every message; the translation shows under the text.
// Translations of the open chat are loaded once per chat and after each finished reply
// (with "Сразу переводить новые ответы" on, the server translated it meanwhile).
export default function setup(t) {
  let chatId = null;
  let kept = {};             // "<message id>:<version>" -> {text, to}
  const hidden = new Set();  // translations the user folded with 🌐
  const busy = new Set();

  const keyOf = (m) => `${m.id}:${m.active_swipe}`;
  const load = async () => {
    if (!chatId) { kept = {}; t.refresh(); return; }
    const wanted = chatId;
    try {
      const all = await t.call("list", { chat_id: wanted });
      if (wanted === chatId) { kept = all || {}; t.refresh(); }
    } catch (e) { t.toast(e.message); }
  };

  t.on("chat", (d) => { chatId = d.chatId; hidden.clear(); load(); });
  t.on("generation", (d) => { if (!d.running) load(); });
  t.onNotice("translated", (d) => { if (d.chat_id === chatId) load(); });

  t.messageButton({
    icon: "🌐",
    title: (m) => (kept[keyOf(m)] && !hidden.has(keyOf(m)) ? "Скрыть перевод" : "Перевести"),
    onClick: async (m) => {
      const key = keyOf(m);
      if (kept[key]) {
        if (hidden.has(key)) hidden.delete(key); else hidden.add(key);
        t.refresh();
        return;
      }
      if (busy.has(key)) return;
      busy.add(key);
      t.refresh();
      try {
        const result = await t.call("translate", { message_id: m.id });
        kept[result.key] = { text: result.text, to: result.to };
      } catch (e) {
        t.toast(e.message);
      } finally {
        busy.delete(key);
        t.refresh();
      }
    },
  });

  t.messageFooter((m) => {
    const key = keyOf(m);
    if (busy.has(key)) return "🌐 перевожу…";
    const entry = kept[key];
    return entry && !hidden.has(key) ? `🌐 ${entry.text}` : null;
  });
}
