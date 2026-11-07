"""Groups in the interface, checked at the source level (nothing here sees the screen)."""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def text(*parts: str) -> str:
    return (WEB.joinpath(*parts)).read_text(encoding="utf-8")


def test_every_kind_is_grouped_in_the_shared_sections():
    sidebar, books = text("components", "Sidebar.js"), text("components", "Lorebooks.js")
    for kind in ("chat", "character"):
        assert f'kind="{kind}"' in sidebar
    assert 'kind="lorebook"' in books
    # the top menu drop-downs and the side panel are the same Sidebar sections
    assert sidebar.count("<${GroupedItems}") == 2 and books.count("<${GroupedItems}") == 1
    # every item menu offers «В группу…» (works by tap, no drag needed)
    assert sidebar.count("В группу…") == 2 and "В группу…" in books


def test_list_and_bubble_views_switch_and_are_remembered_per_kind():
    groups = text("components", "Groups.js")
    assert "tavern.groupView.${kind}" in groups and "tavern.groupClosed.${kind}" in groups
    assert '"Показать пузырями"' in groups and '"Показать списком"' in groups
    # storage can be blocked: every access is guarded
    for line in groups.splitlines():
        if "localStorage" in line and not line.lstrip().startswith("//"):
            assert "try {" in line


def test_bubble_view_reuses_the_map_look_and_stays_light():
    groups, tree = text("components", "Groups.js"), text("components", "CharacterTree.js")
    for name in ("W", "H", "GAP", "MARGIN_PX", "MOVE_REDRAW", "FAR_ZOOM"):
        assert re.search(rf"^export const {name} = ", tree, re.M), name
        assert name in groups.split("from \"./CharacterTree.js\"")[0]
    for cls in ("tree-map", "tree-world", "tree-group", '"bubble group-item', "bubble-title"):
        assert cls in groups
    # only bubbles near the screen are in the page, the camera moves one layer
    assert "MARGIN_PX / camera.s" in groups and "world.current.style.transform" in groups
    # the map's own camera code is untouched
    assert "const applyCamera = " in tree and "const commitCamera = " in tree


def test_styles_cover_the_new_places():
    css = text("styles.css")
    for cls in (".group-map", ".group-fold", ".group-label", ".group-picker"):
        assert cls in css


def test_main_tells_the_lists_when_the_assistant_changed_things():
    assert 'new Event("tavern:groups")' in text("main.js")
    assert '"tavern:groups"' in text("components", "Groups.js")


def test_groups_can_be_dragged_by_finger_and_bubbles_size_to_their_text():
    groups = text("components", "Groups.js")
    # a long press picks an item up (a swipe still scrolls), on the map and in the list; edge auto-scroll
    assert "LONG_PRESS" in groups and "navigator.vibrate" in groups and "edgeScroll" in groups
    assert "useListDrag" in groups and "touchmove" in groups and "drop-target" in groups
    # the «В группу…» menu stays as the fallback
    assert "GroupPicker" in groups and "tools.move" in groups or "move: () => setMoving" in groups
    # bubble width follows the text between a minimum and a maximum
    assert "bubbleWidth" in groups and "BUB_MIN" in groups and "BUB_MAX" in groups
    css = text("styles.css")
    for cls in (".group-frame.drop-target", ".group-ghost", ".group.drop-target"):
        assert cls in css


def test_opening_a_window_does_not_remount_the_top_bar_picker():
    main = text("main.js")
    assert "<${ProfileSwitch} key=" not in main and "refreshKey=" in main
    assert "refreshKey" in text("components", "Profiles.js")


def test_tree_map_does_not_flip_looks_or_pop_lines_while_zooming():
    tree, css = text("components", "CharacterTree.js"), text("styles.css")
    assert "farNow" in tree and "FAR_ZOOM * 1.15" in tree  # dead band around the plain-look limit
    assert "const crosses = " in tree and "bubble-in" in css
