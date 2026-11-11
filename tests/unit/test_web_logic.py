"""Pure logic of the page, run in Node (skipped where it is missing, like Termux): which buttons of a
message stay on its panel, and that every icon the page asks for exists (an unknown name draws nothing)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def run_module(code: str) -> object:
    result = subprocess.run([NODE, "--input-type=module", "-e", code], capture_output=True, timeout=30, cwd=WEB)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def split(available: list[str], options: dict) -> dict:
    return run_module(
        'import { splitActions } from "./core/messageActions.js";'
        f"console.log(JSON.stringify(splitActions({json.dumps(available)}, {json.dumps(options)})))"
    )


@needs_node
def test_by_default_only_regenerate_stays_on_the_panel() -> None:
    assert split(["edit", "regenerate", "delete"], {}) == {"bar": ["regenerate"], "menu": ["edit", "delete"]}


@needs_node
def test_always_keeps_everything_on_the_panel() -> None:
    assert split(["edit", "delete"], {"mode": "always", "pinned": []}) == {"bar": ["edit", "delete"], "menu": []}


@needs_node
def test_pinned_and_status_stay_on_the_panel_in_the_menu_mode() -> None:
    got = split(["edit", "exclude", "notes", "delete"], {"pinned": ["edit"], "status": ["notes"]})
    assert got == {"bar": ["edit", "notes"], "menu": ["exclude", "delete"]}


@needs_node
def test_every_icon_named_in_the_page_exists() -> None:
    known = set(run_module(
        'import { ICONS } from "./vendor/lucide-icons.mjs"; console.log(JSON.stringify(Object.keys(ICONS)))'
    ))
    pattern = re.compile(r'\bicon="([a-z0-9-]+)"|<\$\{Icon\} name="([a-z0-9-]+)"|\bicon: "([a-z0-9-]+)"')
    used: set[str] = set()
    for path in WEB.rglob("*.js"):
        if "vendor" in path.parts or "themes" in path.parts:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            used.add(next(g for g in match.groups() if g))
    assert used, "the pattern found no icons"
    assert not sorted(used - known), sorted(used - known)


def place(**args: object) -> dict:
    return run_module(
        'import { placeList } from "./core/popup.js";'
        f"console.log(JSON.stringify(placeList({json.dumps(args)})))"
    )


@needs_node
def test_list_above_a_field_stays_inside_the_visible_part_when_the_keyboard_pans_the_page() -> None:
    # Tablet, keyboard open: Chrome shows layout rows 500..1100; the field sits at 900..944, many models.
    got = place(box={"top": 900, "bottom": 944, "left": 20}, natural=4000, width=300,
                view={"top": 500, "height": 600}, pageWidth=768)
    assert got["side"] == "up"
    assert got["top"] >= 508  # never above the visible part (it was placed at 8 before)
    assert got["top"] + got["maxHeight"] <= 900


@needs_node
def test_the_chosen_side_is_kept_while_it_has_room() -> None:
    # Opened below; the keyboard then takes some room but three rows still fit under the field: no flip.
    got = place(box={"top": 300, "bottom": 344, "left": 20}, natural=4000, width=300,
                view={"top": 0, "height": 600}, pageWidth=768, side="down")
    assert got["side"] == "down" and got["top"] == 348
    # Without a chosen side the bigger room wins (above here: 288 against 244).
    assert place(box={"top": 300, "bottom": 344, "left": 20}, natural=4000, width=300,
                 view={"top": 0, "height": 600}, pageWidth=768)["side"] == "up"
    # Too little room left under it: it moves above.
    got = place(box={"top": 500, "bottom": 544, "left": 20}, natural=4000, width=300,
                view={"top": 0, "height": 600}, pageWidth=768, side="down")
    assert got["side"] == "up"


@needs_node
def test_list_never_goes_past_the_right_edge() -> None:
    got = place(box={"top": 100, "bottom": 144, "left": 700}, natural=200, width=300,
                view={"top": 0, "height": 1280}, pageWidth=768)
    assert got["left"] + 300 <= 768 - 8
