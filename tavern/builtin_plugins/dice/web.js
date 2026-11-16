// 🎲 Кубики: a button beside the message box. It asks which dice and sends "/roll <formula>".
export default function setup(t) {
  let last = "";
  t.composerButton({
    icon: "🎲",
    title: "Бросить кубики (/roll 2d6+1)",
    onClick: async () => {
      const defaultValue = last || t.settings.default || "1d20";
      const formula = t.askText
        ? await t.askText("Какие кубики бросить? Например 2d6+1", { value: defaultValue })
        : defaultValue;
      if (!formula || !formula.trim()) return;
      last = formula.trim();
      await t.command(`/roll ${last}`);
    },
  });
}
