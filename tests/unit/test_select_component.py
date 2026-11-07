"""The app has its own drop-down (web/components/Select.js); no native <select> is left in the page code."""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
NATIVE_ALLOWED: set[str] = set()  # file names that may keep a native <select>, with the reason in a comment


def test_component_exists_with_accessible_roles() -> None:
    src = (WEB / "components" / "Select.js").read_text(encoding="utf-8")
    for needle in ('aria-haspopup="listbox"', "aria-expanded", 'role="listbox"', 'role="option"',
                   "aria-selected", 'data-slot="select-list"', "Escape", "ArrowDown", "Enter"):
        assert needle in src, needle


def test_no_native_select_left() -> None:
    left = []
    for path in list(WEB.glob("*.js")) + list((WEB / "components").glob("*.js")) + list((WEB / "core").glob("*.js")):
        if path.name == "Select.js" or path.name in NATIVE_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        if "<select" in text or "</select>" in text:
            left.append(path.name)
    assert not left, f"native <select> in {left}: use Select from components/Select.js"


def test_theme_slots_documented() -> None:
    skill = (WEB.parent / ".claude" / "skills" / "make-theme" / "SKILL.md").read_text(encoding="utf-8")
    for slot in ("select-list", "select-option", "select-filter"):
        assert slot in skill
