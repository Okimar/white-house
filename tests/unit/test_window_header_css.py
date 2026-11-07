"""Regression: in the top menu mode a window opened from the sidebar (lorebook, card) lost its header and close button."""
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2] / "web" / "styles.css").read_text(encoding="utf-8")


def test_top_menu_hides_only_the_sidebars_own_head() -> None:
    assert '.app[data-menu="top"] .sidebar > .side-head { display: none; }' in CSS
    assert '.app[data-menu="top"] .sidebar .side-head { display: none; }' not in CSS


def test_link_arcs_are_solid_while_the_map_moves() -> None:
    tree = (Path(__file__).resolve().parents[2] / "web" / "components" / "CharacterTree.js").read_text(encoding="utf-8")
    assert ".tree-map.moving .tree-lines path.tree-refs" in CSS
    assert 'classList.add("moving")' in tree
