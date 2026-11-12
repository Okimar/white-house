"""File pickers of the page work on Android: its picker turns `accept` into MIME types, and a file whose
extension has no MIME type there (.jsonl, .tavern-theme, .tavern-plugin, .md) is greyed out and cannot be
chosen. So `accept` may name only extensions Android knows; the rest is checked after the choice."""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
# Extensions every Android version maps to a MIME type.
ANDROID_KNOWN = {".json", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".zip"}
FILE_INPUT = re.compile(r"<input\b[^>]*\btype=\"file\"[^>]*>", re.S)
ACCEPT = re.compile(r"\baccept=\"([^\"]*)\"")


def file_inputs() -> list[tuple[str, str]]:
    found = []
    for path in sorted(WEB.rglob("*.js")):
        if "vendor" in path.parts:
            continue
        for tag in FILE_INPUT.findall(path.read_text(encoding="utf-8")):
            found.append((path.relative_to(WEB).as_posix(), tag))
    return found


def test_file_inputs_are_found() -> None:
    assert len(file_inputs()) >= 10


def test_accept_names_only_extensions_android_knows() -> None:
    bad = []
    for name, tag in file_inputs():
        accept = ACCEPT.search(tag)
        if not accept:
            continue
        for token in (t.strip().lower() for t in accept.group(1).split(",")):
            if token.startswith(".") and token not in ANDROID_KNOWN:
                bad.append(f"{name}: {token}")
    assert not bad, bad


def test_imports_of_own_formats_have_no_accept() -> None:
    """Chat import (.jsonl), themes (.tavern-theme), plugins (.tavern-plugin), skills (.md): no filter at all."""
    expected = {
        "components/Sidebar.js": "chatFileInput",
        "components/Themes.js": "fileInput",
        "components/Plugins.js": "picker",
        "components/Skills.js": "fileInput",
    }
    tags = file_inputs()
    for name, ref in expected.items():
        mine = [tag for n, tag in tags if n == name and f"ref=${{{ref}}}" in tag]
        assert len(mine) == 1, name
        assert "accept=" not in mine[0], name


def test_skill_file_is_checked_after_the_choice() -> None:
    text = (WEB / "components" / "Skills.js").read_text(encoding="utf-8")
    assert 'text.includes("\\u0000")' in text
    assert "Это не текстовый файл" in text
