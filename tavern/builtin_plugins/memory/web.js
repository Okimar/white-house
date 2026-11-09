// 🧠 Память++ (stage M1): one button beside the message box switches memory on or off for the open chat and
// says how far the vectors are. The «Память» window arrives with the chapters stage.
export default function setup(t) {
  const describe = (s) => {
    const parts = [s.enabled ? "Память в этом чате: включена" : "Память в этом чате: выключена"];
    if (s.enabled) {
      if (!s.connected) parts.push("нет подключения для векторов (Настройки → Промпт)");
      else parts.push(`векторы: ${s.vectors} из ${s.pieces} отрывков`);
      if (s.job && s.job.paused) parts.push("пауза: идёт ваш запрос");
      else if (s.job && s.job.running) parts.push("векторизую…");
      if (s.job && s.job.error) parts.push(s.job.error);
    }
    return parts.join(". ");
  };
  t.composerButton({
    icon: "🧠",
    title: "Память++: включить или выключить в этом чате",
    show: () => !!t.chatId(),
    onClick: async () => {
      const chatId = t.chatId();
      if (!chatId) return;
      const now = await t.call("state", { chat_id: chatId });
      const next = await t.call("set_enabled", { chat_id: chatId, enabled: !now.enabled });
      t.toast(describe(next));
    },
  });
}
