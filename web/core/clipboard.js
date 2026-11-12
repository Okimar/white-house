// Copy text to the clipboard. Over plain http (White House opened by a LAN address from a phone or a tablet)
// navigator.clipboard does not exist: the older way is a temporary text field and execCommand.
// Returns true when the text was copied.

function copyByField(text) {
  let field = null;
  try {
    field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.top = "0";
    field.style.opacity = "0";
    document.body.appendChild(field);
    field.select();
    return Boolean(document.execCommand("copy"));
  } catch {
    return false;
  } finally {
    if (field) field.remove();
  }
}

export async function copyText(text) {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard && globalThis.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* try the older way */ }
  return copyByField(text);
}
