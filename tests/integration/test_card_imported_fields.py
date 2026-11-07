"""The card editor no longer asks for personality, scenario, own main prompt, dialogue examples and
post-history instructions, but a card that has them keeps them: saved by the editor, exported, shown
in the folded «Поля из импортированной карточки» section only when any is filled."""

from __future__ import annotations

from pathlib import Path

IMPORTED = {
    "personality": "Calm.", "scenario": "A tavern.", "mes_example": "<START>\n{{user}}: hi",
    "system_prompt": "Be {{char}}. {{original}}", "post_history_instructions": "Stay in character.",
}


async def test_an_editor_save_and_export_keep_the_imported_fields(client):
    ann = (await client.post("/api/characters", json={"name": "Ann", "description": "Kind.", **IMPORTED})).json()
    card = (await client.get(f"/api/characters/{ann['id']}")).json()["data"]
    assert {k: card[k] for k in IMPORTED} == IMPORTED
    # the editor sends the whole card back with only the visible fields changed
    saved = await client.put(f"/api/characters/{ann['id']}", json={**card, "description": "Cruel."})
    assert saved.status_code == 200, saved.text
    again = (await client.get(f"/api/characters/{ann['id']}")).json()["data"]
    assert again["description"] == "Cruel."
    assert {k: again[k] for k in IMPORTED} == IMPORTED
    exported = (await client.get(f"/api/characters/{ann['id']}/export.json")).json()["data"]
    assert {k: exported[k] for k in IMPORTED} == IMPORTED


def test_the_editor_form_lists_them_only_in_the_imported_section():
    source = (Path(__file__).resolve().parents[2] / "web/components/CharacterEditor.js").read_text(encoding="utf-8")
    main = source[source.index("const FIELDS = ["):source.index("const IMPORTED_FIELDS")]
    for key in IMPORTED:
        assert f'"{key}"' not in main
        assert f'["{key}"' in source[source.index("const IMPORTED_FIELDS"):]
    assert "Поля из импортированной карточки" in source
    assert "hasImported(card) || hasImported(savedCard)" in source
