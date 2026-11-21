// The model of the open chat, in the chat's header. One connection may serve many chats with different models:
// this chat 3.5, another one 3.8 (chat meta `models`, services/chat.py resolve_connection). A tap lists the models
// the connection knows, «как в подключении» (its own model) and «Другая модель…» (any name).
import { html, useEffect, useState } from "../vendor/preact-htm.mjs";
import { api } from "../core/api.js";
import { openMenu } from "./Menu.js";
import { askText } from "./Dialogs.js";

// «gemini-3.5-flash-lite» -> «3.5-flash-lite»: the name without the family when there is a version after it
function short(model) {
  if (!model) return "модель";
  const m = /^[a-z]+[-_](\d.*)$/i.exec(model);
  return m ? m[1] : model;
}

export function ModelChip({ chat, settings, onChanged, onError }) {
  const connectionId = (chat.meta && chat.meta.connection_id) || settings.active_connection || "";
  const [connection, setConnection] = useState(null);
  useEffect(() => {
    if (!connectionId) { setConnection(null); return; }
    api.get("/connections").then((d) => setConnection((d.connections || []).find((c) => c.id === connectionId) || null))
      .catch(() => setConnection(null));
  }, [connectionId]);
  if (!connection) return null;
  const own = ((chat.meta && chat.meta.models) || {})[connection.id] || "";
  const model = own || connection.model || "";
  const choose = (next) => api.put(`/chats/${chat.id}/meta`, { model: next })
    .then(onChanged).catch((e) => onError(e.message));
  const open = async (button) => {
    let known = [];
    try { known = (await api.get(`/connections/${connection.id}/model-info`)).models || []; } catch (e) { /* offline */ }
    const ids = [...new Set(known.map((m) => (typeof m === "string" ? m : m.id)).filter(Boolean))];
    openMenu(button, [
      { icon: own ? "circle" : "check", label: `Как в подключении: ${connection.model || "—"}`, onClick: () => choose("") },
      ...ids.map((id) => ({ icon: id === own ? "check" : "circle", label: id, onClick: () => choose(id) })),
      { icon: "pencil", label: "Другая модель…", onClick: async () => {
        const name = await askText("Модель для этого чата", { value: own || connection.model || "" });
        if (name !== null) choose(name.trim());
      } },
    ]);
  };
  return html`<button type="button" class=${"model-chip" + (own ? " own" : "")} data-slot="model-chip"
    aria-haspopup="menu" title=${`Модель этого чата: ${model}${own ? " (своя для этого чата)" : " (как в подключении)"}. Нажмите, чтобы сменить`}
    aria-label=${`Модель этого чата: ${model}. Сменить`}
    onClick=${(e) => open(e.currentTarget)}>${short(model)}</button>`;
}
