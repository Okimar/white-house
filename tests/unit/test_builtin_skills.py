"""The built-in skills shipped in tavern/skills/: each reads whole, with parts for every role."""

from __future__ import annotations

from pathlib import Path

import pytest

from tavern.services import skills

SHIPPED = Path(skills.__file__).resolve().parent.parent / "skills"


@pytest.mark.parametrize("path", sorted(SHIPPED.glob("*.md")), ids=lambda p: p.stem)
def test_a_shipped_skill_has_its_header_and_a_part_for_each_role(path: Path):
    fields, body = skills.parse(path.read_text(encoding="utf-8"))
    assert fields["name"] == path.stem and fields["title"] and fields["description"]
    assert fields["audience"] in skills.AUDIENCES
    skill = {"name": fields["name"], "body": body}
    planner, writer = skills.part(skill, "planner"), skills.part(skill, "writer")
    assert planner != writer and "## " not in planner + writer
    assert skills.checks(skill)


def test_the_lorebook_skill_carries_the_owners_rules():
    _, body = skills.parse((SHIPPED / "lorebooks.md").read_text(encoding="utf-8"))
    skill = {"name": "lorebooks", "body": body}
    writer = skills.part(skill, "writer")
    for rule in ("always", "If … →", "BANNED CLICHÉS", "try_phrase", "parent_id"):
        assert rule in writer
    assert "text RPG" in skills.part(skill, "planner")
    assert any("try_phrase" in check for check in skills.checks(skill))


def test_the_character_skill_carries_the_owners_rules_and_asks_twice():
    _, body = skills.parse((SHIPPED / "characters.md").read_text(encoding="utf-8"))
    skill = {"name": "characters", "body": body}
    assert skills.asks_more(skill) and "SECOND ROUND" in skills.part(skill, "more")
    writer = skills.part(skill, "writer")
    for rule in ("description", "alternate_greetings", "trigger → reaction", "1500 words", "try_phrase"):
        assert rule in writer
    assert "FIRST ROUND" in skills.part(skill, "planner") and "SECOND ROUND" not in skills.part(skill, "planner")
    assert any("{{user}}" in check for check in skills.checks(skill))


def test_the_lorebook_work_skill_sets_the_rules_for_existing_books():
    _, body = skills.parse((SHIPPED / "lorebook-work.md").read_text(encoding="utf-8"))
    skill = {"name": "lorebook-work", "body": body}
    writer, planner = skills.part(skill, "writer"), skills.part(skill, "planner")
    for rule in ("Never ask the user to send", "SWEEP", "LINKS", "cannot be understood without", "list_links",
                 "Link limit reached", "One card per person"):
        assert rule in writer
    assert '"over"' in planner and "No questions about how it is done" in planner
    checks = skills.checks(skill)
    assert any("N of N" in c for c in checks) and any("limit" in c for c in checks)


def test_the_new_lorebook_skill_says_links_are_few():
    _, body = skills.parse((SHIPPED / "lorebooks.md").read_text(encoding="utf-8"))
    skill = {"name": "lorebooks", "body": body}
    assert "link_bubbles" in skills.part(skill, "writer")
    assert any("Links: few or none" in c for c in skills.checks(skill))
