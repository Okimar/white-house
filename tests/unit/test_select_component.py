"""The app has its own drop-down (web/components/Select.js); no native <select> is left in the page code."""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def test_no_native_select_left() -> None:
    left = []
    for path in list(WEB.glob("*.js")) + list((WEB / "components").glob("*.js")) + list((WEB / "core").glob("*.js")):
        if path.name == "Select.js":
            continue
        text = path.read_text(encoding="utf-8")
        if "<select" in text or "</select>" in text:
            left.append(path.name)
    assert not left, f"native <select> in {left}: use Select from components/Select.js"
