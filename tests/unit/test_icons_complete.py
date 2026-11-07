"""Every vendored icon element carries its attributes (a bare `line` draws nothing, the icon stays blank)."""
from __future__ import annotations

from pathlib import Path

ICONS = Path(__file__).resolve().parents[2] / "web" / "vendor" / "lucide-icons.mjs"


def test_no_icon_element_without_attributes() -> None:
    text = ICONS.read_text(encoding="utf-8")
    assert ',{}]' not in text, "an icon element has no attributes and draws nothing"


def test_main_never_touches_storage_unguarded() -> None:
    """With browser storage blocked, an unguarded access at start leaves a blank page."""
    main = ICONS.parents[1] / "main.js"
    for number, line in enumerate(main.read_text(encoding="utf-8").splitlines(), 1):
        if "localStorage" in line and not line.lstrip().startswith("//"):
            assert "try {" in line, f"main.js:{number} uses localStorage outside try/catch"
