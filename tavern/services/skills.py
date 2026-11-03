"""Skills: instructions the assistant reads for a kind of work (stage 10б), and the programmer's ones.

A skill is Markdown with a small header:

    ---
    name: lorebooks
    title: Лорбуки                       (shown in the «Инструкции» tab)
    description: When to use it (English; the assistant chooses by this line)
    audience: assistant | programmer | both
    ---
    Text every role reads (English).
    ## Plan    – only the planner reads this part (questions, plan)
    ## Write   – only the writer reads this part (doing the items)
    ## Check   – lines starting with "- " become extra checks for the critic
    ## More questions – the planner asks a second round of questions after the first answers,
                 reading the common text plus this part

Sources, by id:
- `builtin:<name>`  files in tavern/skills/ (read-only; a copy replaces one for the assistant)
- `<uuid>`          the user's own (table `skills`): new ones and copies (based_on = the built-in's name)
- `plugin:<plugin>:<name>`  files in skills/ of an active plugin
- `dev:<name>`      .claude/skills/<name>/SKILL.md, the programmer's guides (shown read-only; the
                    assistant reads only those marked `audience: both`)
Built-in, plugin and programmer skills are switched off by the setting "skills_off" (a list of ids).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tavern.context import AppContext
from tavern.errors import AppError, Conflict, NotFound
from tavern.paths import REPO_ROOT

BUILTIN_DIR = Path(__file__).resolve().parent.parent / "skills"
DEV_DIR = REPO_ROOT / ".claude" / "skills"
OFF_KEY = "skills_off"
AUDIENCES = ("assistant", "programmer", "both")
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_BODY = 60_000
ROLE_PARTS = {"plan": "planner", "write": "writer", "check": "critic", "more questions": "more"}


def parse(text: str) -> tuple[dict[str, str], str]:
    """The header fields and the body of a skill file."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text
    head, sep, body = text[3:].partition("\n---")
    if not sep:
        return {}, text
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon and key.strip() and not line.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip('"')
    return fields, body.lstrip("\n")


def _from_file(path: Path, skill_id: str, source: str, default_audience: str) -> dict[str, Any] | None:
    try:
        fields, body = parse(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    name = fields.get("name") or path.stem
    audience = fields.get("audience", default_audience)
    return {"id": skill_id, "name": name, "title": fields.get("title") or name,
            "description": fields.get("description", ""), "body": body,
            "audience": audience if audience in AUDIENCES else default_audience,
            "source": source, "based_on": "", "readonly": True}


def _plugin_folders(ctx: AppContext) -> list[tuple[str, Path]]:
    plugins = getattr(ctx, "plugins", None)
    if plugins is None:
        return []
    try:
        active = {p["id"] for p in plugins.list() if p.get("active")}
        return [(pid, found.folder) for pid, found in plugins.discover().items() if pid in active]
    except Exception:  # noqa: BLE001 - a broken plugin folder must not hide the other skills
        return []


def _files(ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    if BUILTIN_DIR.is_dir():
        for path in sorted(BUILTIN_DIR.glob("*.md")):
            skill = _from_file(path, f"builtin:{path.stem}", "builtin", "assistant")
            if skill:
                out.append(skill)
    for plugin_id, folder in _plugin_folders(ctx):
        for path in sorted((folder / "skills").glob("*.md")):
            skill = _from_file(path, f"plugin:{plugin_id}:{path.stem}", "plugin", "assistant")
            if skill:
                out.append({**skill, "plugin": plugin_id})
    if DEV_DIR.is_dir():
        for path in sorted(DEV_DIR.glob("*/SKILL.md")):
            skill = _from_file(path, f"dev:{path.parent.name}", "dev", "programmer")
            if skill:
                out.append(skill)
    return out


def all_skills(ctx: AppContext) -> list[dict[str, Any]]:
    """Every skill with `enabled`, `replaced` (a copy stands in for it) and `used` (the assistant reads it)."""
    off = set(ctx.settings.get(OFF_KEY) or [])
    own = [{**s, "source": "own", "readonly": False} for s in ctx.skills.list()]
    replaced = {s["based_on"] for s in own if s["based_on"] and s["enabled"]}
    out = []
    for skill in [*_files(ctx), *own]:
        enabled = skill["enabled"] if skill["source"] == "own" else skill["id"] not in off
        by_copy = skill["source"] == "builtin" and skill["name"] in replaced
        used = enabled and not by_copy and skill["audience"] in ("assistant", "both")
        out.append({**skill, "enabled": enabled, "replaced": by_copy, "used": used})
    return out


def listing(ctx: AppContext) -> list[dict[str, Any]]:
    return [{k: v for k, v in s.items() if k != "body"} for s in all_skills(ctx)]


def get(ctx: AppContext, skill_id: str) -> dict[str, Any]:
    for skill in all_skills(ctx):
        if skill["id"] == skill_id:
            return skill
    raise NotFound("Инструкция не найдена")


def for_assistant(ctx: AppContext) -> list[dict[str, Any]]:
    """The skills the assistant may read now, one per name (the user's own wins)."""
    seen: dict[str, dict[str, Any]] = {}
    for skill in all_skills(ctx):
        if skill["used"] and (skill["name"] not in seen or skill["source"] == "own"):
            seen[skill["name"]] = skill
    return list(seen.values())


def find(ctx: AppContext, name: Any) -> dict[str, Any] | None:
    if not isinstance(name, str) or not name.strip():
        return None
    wanted = name.strip().lower()
    return next((s for s in for_assistant(ctx) if s["name"] == wanted), None)


def catalog(ctx: AppContext) -> str:
    """The list the models choose from: one line per skill ("" when there are none)."""
    return "\n".join(f"- {s['name']}: {s['description'] or s['title']}" for s in for_assistant(ctx))


def part(skill: dict[str, Any], role: str) -> str:
    """What one role reads of a skill: the common text plus its own part ("## Plan" / "## Write").
    A skill without role parts is read whole by the planner and the writer."""
    common: list[str] = []
    parts: dict[str, list[str]] = {}
    current = common
    for line in skill["body"].splitlines():
        heading = re.match(r"^##\s+(Plan|Write|Check|More questions)\s*$", line.strip(), re.IGNORECASE)
        if heading:
            current = parts.setdefault(ROLE_PARTS[" ".join(heading.group(1).lower().split())], [])
            continue
        current.append(line)
    if not parts:
        return skill["body"].strip()
    return "\n".join([*common, *parts.get(role, [])]).strip()


def asks_more(skill: dict[str, Any]) -> bool:
    """Whether the skill wants a second round of questions (it has a "## More questions" part)."""
    return any(re.match(r"^##\s+More\s+questions\s*$", line.strip(), re.IGNORECASE)
               for line in skill["body"].splitlines())


def checks(skill: dict[str, Any]) -> list[str]:
    """Extra checks for the critic: the "- " lines under "## Check" (indented lines continue one)."""
    out: list[str] = []
    inside = False
    for line in skill["body"].splitlines():
        heading = re.match(r"^##\s+(\w+)", line.strip())
        if heading:
            inside = heading.group(1).lower() == "check"
        elif inside and line.strip().startswith("- "):
            out.append(line.strip()[2:].strip())
        elif inside and line.startswith("  ") and line.strip() and out:
            out[-1] += " " + line.strip()
    return out


# --- changes ----------------------------------------------------------------------------------


def _clean(raw: Any, *, partial: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AppError("A skill must be an object")
    out: dict[str, Any] = {}
    if "name" in raw or not partial:
        name = str(raw.get("name") or "").strip().lower()
        if not NAME.match(name):
            raise AppError("Имя инструкции: латинские буквы, цифры и дефис, до 64 знаков (например, my-lorebooks)")
        out["name"] = name
    for key, limit in (("title", 200), ("description", 1000), ("body", MAX_BODY)):
        if key in raw:
            if not isinstance(raw[key], str) or len(raw[key]) > limit:
                raise AppError(f"{key}: text up to {limit} characters")
            out[key] = raw[key]
    if "audience" in raw:
        if raw["audience"] not in AUDIENCES:
            raise AppError("audience: assistant, programmer or both")
        out["audience"] = raw["audience"]
    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            raise AppError("enabled: true or false")
        out["enabled"] = raw["enabled"]
    return out


def _free_name(ctx: AppContext, base: str) -> str:
    taken = {s["name"] for s in ctx.skills.list()}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def create(ctx: AppContext, raw: Any) -> dict[str, Any]:
    """A new own skill, or a copy of any skill ({"from": id}); a copy of a built-in replaces it."""
    if isinstance(raw, dict) and raw.get("from"):
        source = get(ctx, str(raw["from"]))
        based_on = source["name"] if source["source"] == "builtin" else source.get("based_on", "")
        fields = {"name": _free_name(ctx, source["name"]), "title": f"{source['title']} (моя)",
                  "description": source["description"], "body": source["body"],
                  "audience": source["audience"], "based_on": based_on}
        return {**ctx.skills.create(fields), "source": "own"}
    fields = _clean(raw)
    if ctx.skills.by_name(fields["name"]):
        raise Conflict("Инструкция с таким именем уже есть", code="name_taken")
    fields.setdefault("title", fields["name"])
    return {**ctx.skills.create(fields), "source": "own"}


def update(ctx: AppContext, skill_id: str, raw: Any) -> dict[str, Any]:
    if ":" in skill_id:
        if isinstance(raw, dict) and set(raw) == {"enabled"} and isinstance(raw["enabled"], bool):
            return switch(ctx, skill_id, raw["enabled"])
        raise Conflict("Встроенную инструкцию нельзя менять: сделайте копию", code="readonly")
    fields = _clean(raw, partial=True)
    if "name" in fields:
        other = ctx.skills.by_name(fields["name"])
        if other and other["id"] != skill_id:
            raise Conflict("Инструкция с таким именем уже есть", code="name_taken")
    return {**ctx.skills.update(skill_id, fields), "source": "own"}


def switch(ctx: AppContext, skill_id: str, enabled: bool) -> dict[str, Any]:
    get(ctx, skill_id)
    off = [s for s in ctx.settings.get(OFF_KEY) or [] if s != skill_id]
    if not enabled:
        off.append(skill_id)
    ctx.settings.set(OFF_KEY, off)
    return get(ctx, skill_id)


def delete(ctx: AppContext, skill_id: str) -> None:
    if ":" in skill_id:
        raise Conflict("Встроенную инструкцию нельзя удалить; её можно выключить", code="readonly")
    ctx.skills.delete(skill_id)
