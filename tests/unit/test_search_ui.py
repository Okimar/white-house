"""The search window is reachable from every menu: the side panel, the top menu bar and the phone's bar."""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def test_search_icon_is_in_every_menu_and_results_open_by_the_existing_jump():
    main = (WEB / "main.js").read_text(encoding="utf-8")
    side = (WEB / "components" / "Sidebar.js").read_text(encoding="utf-8")
    assert 'data-slot="search-button"' in side and "onSearch" in side  # side menu
    for slot in ("menubar-search", "phone-search", "folded-search", "phone-folded-search"):  # top menu, phone
        assert f'data-slot="{slot}"' in main
    assert "showFound(hit)" in main and "setAssistantMsg" in main  # the in-chat jump is reused


def test_search_window_has_both_places_and_both_ways():
    text = (WEB / "components" / "Search.js").read_text(encoding="utf-8")
    for needle in ("Обычные чаты", "Разговоры с помощником", "Спросить нейронку", "AbortController", "/search/stop"):
        assert needle in text
