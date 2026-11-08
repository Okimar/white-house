"""Source-level guards for interface rules that broke once (nothing here sees the screen)."""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def text(*parts: str) -> str:
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


def test_browser_storage_is_always_guarded():
    """With storage blocked an unguarded access leaves a blank page or a broken list."""
    for name in ("components/Groups.js", "main.js"):
        for number, line in enumerate(text(*name.split("/")).splitlines(), 1):
            if "localStorage" in line and not line.lstrip().startswith("//"):
                assert "try {" in line, f"{name}:{number} uses localStorage outside try/catch"


def test_opening_a_window_does_not_remount_the_top_bar_picker():
    assert "<${ProfileSwitch} key=" not in text("main.js")


def test_tree_map_does_not_flip_looks_or_pop_lines_while_zooming():
    tree, css = text("components", "CharacterTree.js"), text("styles.css")
    assert "farNow" in tree and "FAR_ZOOM * 1.15" in tree  # dead band around the plain-look limit
    assert "const crosses = " in tree and "bubble-in" in css
