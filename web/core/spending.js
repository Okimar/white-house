// Spending (stage 6е): the summary hook and how money and tokens are written.
// - useSpending(chatId): {summary, reload}; reloads by itself after every reply (spendingChanged()).
// - money(usd, settings): "$0.0042" or "0.42 ₽" at the user's rate; tokens(n): "1.2k".
// - usageLine(usage, settings): the small line under a message ("↑1.2k ↓350 · $0.0042").
import { useState, useEffect } from "../vendor/preact-htm.mjs";
import { api } from "./api.js";

const EVENT = "tavern:spending";

export function spendingChanged() {
  window.dispatchEvent(new Event(EVENT));
}

export function useSpending(chatId) {
  const [summary, setSummary] = useState(null);
  const reload = () => api.get("/spending" + (chatId ? `?chat=${encodeURIComponent(chatId)}` : ""))
    .then(setSummary).catch(() => {}); // the bar is secondary: no toast when it cannot load
  useEffect(() => {
    reload();
    window.addEventListener(EVENT, reload);
    return () => window.removeEventListener(EVENT, reload);
  }, [chatId]);
  return { summary, reload };
}

function round(n) {
  if (n === 0) return "0";
  if (n >= 1) return n.toFixed(2);
  if (n >= 0.01) return n.toFixed(3);
  return n.toFixed(5);
}

export function money(usd, settings) {
  const { code, rate } = settings.currency;
  return code ? `${round(usd * rate)} ${code}` : `$${round(usd)}`;
}

export function tokens(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

// A sum from the server ({prompt_tokens, completion_tokens, cost, unpriced, requests}) as one line.
export function sumLine(sum, settings) {
  const parts = [];
  if (settings.tokens) parts.push(`↑${tokens(sum.prompt_tokens)} ↓${tokens(sum.completion_tokens)}`);
  if (sum.cost || !sum.unpriced) parts.push(money(sum.cost || 0, settings) + (sum.unpriced ? "+?" : ""));
  return parts.join(" · ");
}

export function usageLine(usage, settings) {
  const parts = [];
  const guess = usage.estimated ? "≈" : "";
  if (settings.tokens) parts.push(`${guess}↑${tokens(usage.prompt_tokens)} ↓${tokens(usage.completion_tokens)}`);
  if (usage.cost !== null && usage.cost !== undefined) parts.push(guess + money(usage.cost, settings));
  return parts.join(" · ");
}
