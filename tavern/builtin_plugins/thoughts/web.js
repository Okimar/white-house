// 🌳 Дерево мыслей: the thinkers' tree, live while they think and kept with each version of a reply
// (swipe_meta.thoughts). Shown folded under the reply, in a side panel, or both (setting "show").
export default function setup(t) {
  const live = new Map(); // reply id -> {rounds: [[entry]], writing}
  let chatId = null;
  let latest = null; // {depth, thoughts} of the newest reply with thoughts drawn in this chat

  const where = () => t.settings.show || "under";

  function tree(thoughts, running) {
    const rounds = (thoughts && thoughts.rounds) || [];
    if (!rounds.length) return null;
    const item = (e) => ({ title: e.name, text: e.state === "error" ? `не ответил: ${e.error || "ошибка"}` : e.text,
      state: running || e.state === "error" ? e.state : "" });
    const last = rounds[rounds.length - 1];
    const done = last.filter((e) => e.state !== "wait").length;
    const title = running && !thoughts.writing ? `🌳 Думают… ${done} из ${last.length}` : `🌳 Мысли (${last.length})`;
    const items = rounds.length === 1 ? last.map(item)
      : rounds.map((r, i) => ({ title: `Круг ${i + 1}`, text: "", state: "", children: r.map(item) }));
    return { title, open: running && !thoughts.writing, items };
  }

  const panel = t.panel({
    icon: "🌳",
    title: "Дерево мыслей",
    render: () => {
      if (where() === "under") return null;
      const running = [...live.values()].pop();
      if (running) return tree(running, true);
      return latest ? tree(latest.thoughts, false) : null;
    },
  });

  t.on("chat", (d) => { if (d.chatId !== chatId) { chatId = d.chatId; latest = null; live.clear(); t.refresh(); } });
  t.on("generation", (d) => { if (!d.running && live.size) { live.clear(); t.refresh(); } });
  t.on("trace", (e) => {
    if (e.plugin !== t.id || e.chatId !== chatId) return;
    let entry = live.get(e.id);
    if (!entry) {
      entry = { rounds: [], writing: false };
      live.set(e.id, entry);
      if (where() === "panel") panel.open();
    }
    if (e.kind === "thought") {
      const round = (entry.rounds[e.round] ||= []);
      round[e.index] = { name: e.name, state: e.state, text: e.text, error: e.error };
    } else if (e.kind === "thought_reply") {
      entry.writing = true;
    }
    t.refresh();
  });

  t.messageTree((m) => {
    const running = live.get(m.id);
    const thoughts = running || (m.swipe_meta && m.swipe_meta.thoughts);
    if (!thoughts) return null;
    if (!running && (!latest || m.depth >= latest.depth) && latest?.thoughts !== thoughts) {
      latest = { depth: m.depth, thoughts };
      queueMicrotask(t.refresh); // the panel shows the newest one
    }
    return where() === "panel" ? null : tree(thoughts, !!running);
  });
}
