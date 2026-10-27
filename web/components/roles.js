// Who a message is from, as the model sees it. Shared by the composer menu and message headers.
export const ROLES = [
  { id: "user", icon: "👤", label: "Пользователь" },
  { id: "assistant", icon: "🤖", label: "Ассистент" },
  { id: "system", icon: "⚙", label: "Система" },
];

export const roleOf = (id) => ROLES.find((r) => r.id === id) || ROLES[0];
