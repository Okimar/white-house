// The buttons of a chat message: which stay on its panel and which go into the "…" list.
// Settings: message_actions = "menu" (default) or "always"; message_pinned = ids kept on the panel in any mode.
export const MESSAGE_ACTIONS = [
  { id: "edit", label: "Изменить", icon: "pencil" },
  { id: "regenerate", label: "Другой вариант", icon: "refresh-cw" },
  { id: "branches", label: "Ветки", icon: "git-branch" },
  { id: "prompt", label: "Что видела модель", icon: "eye" },
  { id: "exclude", label: "Скрыть от модели", icon: "eye-off" },
  { id: "notes", label: "Заметки", icon: "notebook-pen" },
  { id: "delete", label: "Удалить", icon: "trash-2" },
];

export const DEFAULT_PINNED = ["regenerate"];

// available: the actions this message has now (in order). status: ids that show a state (the message has
// notes, it is hidden from the model): they stay on the panel, or the state could not be seen.
export function splitActions(available, { mode = "menu", pinned = DEFAULT_PINNED, status = [] } = {}) {
  if (mode === "always") return { bar: [...available], menu: [] };
  const onBar = (id) => pinned.includes(id) || status.includes(id);
  return { bar: available.filter(onBar), menu: available.filter((id) => !onBar(id)) };
}
