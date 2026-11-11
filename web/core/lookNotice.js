// The words of the notice about a look an update switched off (core/theme.js draws it). Pure, so a test can run it.
// The server (services/look.py) reports only what is still off and what is on instead, so the notice never
// claims the standard look while another theme shows.

export function parkedText(p) {
  if (!p) return "";
  const what = p.theme ? `«${p.theme_name || p.theme}»` : p.characters ? "свои темы персонажей" : "стили сторонних дополнений";
  const now = p.now_theme && p.now_theme !== "default"
    ? `сейчас включена тема «${p.now_theme_name || p.now_theme}».`
    : "сейчас включён стандартный вид.";
  return `White House обновился, и ваше прежнее оформление ${what} отключено: `
    + `оно писалось под старый вид и могло сломать меню. Ничего не удалено, ${now}`;
}

// Whether the notice should be on the screen for this answer of GET /api/look.
export function parkedShown(look) {
  return !!(look && look.parked && !look.parked.seen);
}
