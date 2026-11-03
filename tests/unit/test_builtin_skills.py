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
