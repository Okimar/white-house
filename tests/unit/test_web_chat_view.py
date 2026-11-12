"""Source checks of the chat view «Пузыри» / «Документ» and of the tighter bubbles on narrow screens (nothing runs
in a browser here: the numbers are read from the CSS)."""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
CSS = (WEB / "styles.css").read_text(encoding="utf-8")
MAIN = (WEB / "main.js").read_text(encoding="utf-8")


def block(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, selector
    return match.group(1)


def test_margins_around_messages_are_variables_with_smaller_values_on_narrow_screens() -> None:
    assert "--chat-pad-x: 12px; --msg-gap: 8px; --msg-pad-x: 12px; --msg-pad-y: 8px;" in CSS
    assert "padding: 0 var(--chat-pad-x)" in block(".vlist")
    assert "var(--msg-gap)" in block(".vitem")
    assert "var(--msg-pad-x)" in block(".msg")
    narrow = re.search(r"@media \(max-width: 820px\) \{\s*:where\(:root\) \{([^}]*)\}", CSS)
    assert narrow, "narrow-screen values"
    values = dict(re.findall(r"(--[\w-]+):\s*(\d+)px", narrow.group(1)))
    assert int(values["--chat-pad-x"]) <= 4 and int(values["--msg-pad-x"]) <= 8 and int(values["--msg-gap"]) <= 4


def test_document_view_has_no_bubble_and_a_quiet_name_line() -> None:
    rule = re.search(r'\.main\[data-view="document"\] \.msg,[^{]*\{([^}]*)\}', CSS)
    assert rule, "document bubble reset"
    for part in ("background: none", "border: 0", "box-shadow: none", "padding: 0", "border-radius: 0"):
        assert part in rule.group(1)
    assert "display: none" in block('.main[data-view="document"] .msg-avatar')
    name = block('.main[data-view="document"] .msg-name')
    assert "var(--muted)" in name and "var(--text-sm)" in name  # 14px, not smaller
    assert "--msg-pad-x: 0px" in block('.main[data-view="document"]')


def test_document_header_keeps_44px_buttons_on_a_short_line() -> None:
    assert "min-height: var(--doc-line)" in block('.main[data-view="document"] .msg header')
    assert "margin-block: calc((var(--doc-line) - var(--tap)) / 2)" in CSS


def test_the_page_puts_the_view_on_the_chat_and_remeasures_the_list() -> None:
    assert "data-view=${chatView}" in MAIN
    assert "layoutKey=${chatView}" in MAIN
    assert 'chat_view: view' in MAIN and "Вид: как в настройках" in MAIN
    # the chat's own choice first, then the app setting, then bubbles
    assert re.search(r"chat\.chat\.meta\.chat_view, settings\.chat_view\]", MAIN)
    lst = (WEB / "components" / "VirtualList.js").read_text(encoding="utf-8")
    assert "layoutKey" in lst and 'querySelectorAll(".vitem")' in lst


def test_settings_offer_the_view() -> None:
    themes = (WEB / "components" / "Themes.js").read_text(encoding="utf-8")
    assert "chat_view: e.target.value" in themes and 'value="document"' in themes and 'value="bubbles"' in themes
