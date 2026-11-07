"""The menu can sit on top or at the side and fold; theme slots stay. Source-level checks (no browser)."""
from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_settings_has_menu_switch_defaulting_to_top():
    assert 'data-slot="menu-mode"' in read("components/Settings.js")
    layout = read("core/layout.js")
    assert 'read(KEYS.mode) === "side" ? "side" : "top"' in layout  # top unless chosen otherwise
    assert "catch" in layout  # storage may be off


def test_old_slots_are_kept():
    sidebar, main = read("components/Sidebar.js"), read("main.js")
    for slot in ('data-slot="sidebar"', 'data-slot="home-button"', 'data-slot="assistant-button"',
                 'data-slot="chats"', 'data-slot="characters"', 'data-slot="chat-rename"'):
        assert slot in sidebar
    for slot in ('data-slot="phone-nav"', 'data-slot="home-back"', 'data-slot="topbar"', 'data-slot="menubar"',
                 'data-slot="menu-folded"'):
        assert slot in main


def test_old_safe_banner_is_gone_and_folded_menu_keeps_home_and_settings():
    main = read("main.js")
    assert "safe-banner" not in main and "SAFE_MODE" not in main
    assert "safe-banner" not in read("styles.css")
    folded = main[main.index('data-slot="menu-folded"'):]
    folded = folded[: folded.index("</span>`}")]
    assert 'data-slot="folded-home"' in folded and 'data-slot="folded-settings"' in folded


def test_side_panels_can_fold():
    tree = read("components/PluginTree.js")
    assert 'data-slot="fold-panel"' in tree and "setPanelFolded" in tree
    assert ".plugin-panel.folded" in read("styles.css")


def test_phone_top_bar_folds_and_keeps_home_and_settings():
    main, css = read("main.js"), read("styles.css")
    assert 'data-slot="phone-fold"' in main and 'data-slot="phone-unfold"' in main
    folded = main[main.index('data-slot="phone-folded"'):]
    folded = folded[: folded.index("</span>`}")]
    assert 'data-slot="phone-folded-home"' in folded and 'data-slot="phone-folded-settings"' in folded
    assert "setMenuFolded(true)" in main[main.index('data-slot="phone-fold"') - 200: main.index('data-slot="phone-fold"') + 200]
    # in the flow (never position: fixed/absolute), only at phone width
    block = css[css.index("Phone: the top bar folds"):]
    assert "position:" not in block and "max-width: 720px" in block


def test_bubble_map_side_panel_folds_without_touching_the_map():
    tree, css = read("components/CharacterTree.js"), read("styles.css")
    assert tree.count("useFoldPanel()") == 3  # definition + the bubble panel and the root panel, each re-renders alone
    assert 'data-slot="fold-panel"' in tree and "setPanelFolded(PANEL_KEY" in tree
    assert ".tree-panel.folded" in css
    # the fold state is read in the panels, never in the map component (it would redraw every bubble)
    map_part = tree[tree.index("export function CharacterTree("):]
    assert "useLayout" not in map_part and "panelFolded" not in map_part


def test_settings_remember_inner_choices_safely():
    choice = read("core/choice.js")
    assert "tavern.settingsChoice" in choice and choice.count("catch") >= 2
    for name, key in (("Connections", "connection"), ("Presets", "preset"), ("Skills", "skill"),
                      ("Templates", "template"), ("Plugins", "plugin")):
        assert f'"{key}"' in read(f"components/{name}.js") and "core/choice.js" in read(f"components/{name}.js")


def test_changes_tab_does_not_claim_deleted_conversations_lose_their_changes():
    changes = read("components/Changes.js")
    assert "удалить, её изменения" not in changes and "беседа удалена" in changes
    assert "onOpenAssistant=" in read("main.js")
