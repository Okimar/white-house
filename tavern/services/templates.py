"""Instruct templates: built-in ones and the user's, choosing one for a model, ST files in and out."""

from __future__ import annotations

from typing import Any

from tavern.context import AppContext
from tavern.core import templates as T


def all_templates(ctx: AppContext) -> dict[str, dict[str, Any]]:
    """Every template by id: the user's first (so "auto" prefers them), then the built-in ones."""
    out = {t["id"]: t for t in ctx.templates.list()}
    out.update({tid: {"id": tid, "builtin": True, **t} for tid, t in T.BUILTIN.items()})
    return out


def resolve(ctx: AppContext, template_id: str, model: str) -> dict[str, Any]:
    """The template a connection uses: a chosen id, or "auto" = by the model's name."""
    templates = all_templates(ctx)
    if template_id and template_id != "auto" and template_id in templates:
        return templates[template_id]
    return templates[T.detect(model, templates)]
