// Who a message is from, as the model sees it. Shared by the composer menu and message headers.
export const ROLES = [
  { id: "user", icon: "user", label: "Вы" },
  { id: "assistant", icon: "bot", label: "Персонаж" },
  { id: "system", icon: "terminal", label: "Система" },
];

export const roleOf = (id) => ROLES.find((r) => r.id === id) || ROLES[0];
