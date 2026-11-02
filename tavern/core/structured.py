"""Structured output: does a value fit a JSON Schema, and how to ask a model that has no JSON mode.

`problems(value, schema)` checks the subset of JSON Schema that models are asked to follow:
type (one or a list; "integer", "number", "string", "boolean", "array", "object", "null"), enum,
const, properties, required, additionalProperties (false or a schema), items, minItems, maxItems,
uniqueItems, minLength, maxLength, pattern, minimum, maximum, exclusiveMinimum, exclusiveMaximum,
anyOf, oneOf, allOf, and local "$ref": "#/$defs/..." (or "#/definitions/..."). Unknown keywords
are ignored, as the spec says. Pure Python: no jsonschema dependency (Termux).

Each problem is a short English line with the path, e.g. "$.items[2].name: required", fed back to
the model when it retries (LLMClient.generate_json).
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_PROBLEMS = 12

_TYPES: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def problems(value: Any, schema: dict[str, Any]) -> list[str]:
    """Why `value` does not fit `schema` ([] = it fits). At most MAX_PROBLEMS lines."""
    out: list[str] = []
    _check(value, schema, "$", schema, out)
    return out[:MAX_PROBLEMS]


def _resolve(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    seen = 0
    while isinstance(schema.get("$ref"), str) and seen < 32:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return {}  # remote refs are not followed
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(target, dict) else None
        schema = target if isinstance(target, dict) else {}
        seen += 1
    return schema


def _check(value: Any, schema: Any, path: str, root: dict[str, Any], out: list[str]) -> None:
    if schema is False:
        out.append(f"{path}: not allowed")
    if len(out) >= MAX_PROBLEMS or not isinstance(schema, dict):
        return
    schema = _resolve(schema, root)
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_TYPES.get(t, lambda v: True)(value) for t in names):
            out.append(f"{path}: must be {' or '.join(names)}, got {_kind(value)}")
            return
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: must be one of {json.dumps(schema['enum'], ensure_ascii=False)[:200]}")
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: must be {json.dumps(schema['const'], ensure_ascii=False)[:100]}")
    if isinstance(value, str):
        _strings(value, schema, path, out)
    elif _TYPES["number"](value):
        _numbers(value, schema, path, out)
    elif isinstance(value, list):
        _arrays(value, schema, path, root, out)
    elif isinstance(value, dict):
        _objects(value, schema, path, root, out)
    for sub in schema.get("allOf") or []:
        _check(value, sub, path, root, out)
    for key in ("anyOf", "oneOf"):
        options = schema.get(key)
        if options:
            fits = sum(1 for sub in options if not _sub(value, sub, path, root))
            if fits == 0:
                out.append(f"{path}: fits none of the allowed shapes")
            elif key == "oneOf" and fits > 1:
                out.append(f"{path}: fits more than one of the allowed shapes")


def _sub(value: Any, schema: Any, path: str, root: dict[str, Any]) -> list[str]:
    found: list[str] = []
    _check(value, schema, path, root, found)
    return found


def _strings(value: str, schema: dict[str, Any], path: str, out: list[str]) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        out.append(f"{path}: at least {schema['minLength']} characters")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        out.append(f"{path}: at most {schema['maxLength']} characters")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            if re.search(pattern, value) is None:
                out.append(f"{path}: must match {pattern}")
        except re.error:
            pass  # a pattern Python cannot read is not the model's fault


_BOUNDS = (
    ("minimum", lambda v, b: v < b, "at least"),
    ("maximum", lambda v, b: v > b, "at most"),
    ("exclusiveMinimum", lambda v, b: v <= b, "more than"),
    ("exclusiveMaximum", lambda v, b: v >= b, "less than"),
)


def _numbers(value: float, schema: dict[str, Any], path: str, out: list[str]) -> None:
    for key, bad, word in _BOUNDS:
        bound = schema.get(key)
        if _TYPES["number"](bound) and bad(value, bound):
            out.append(f"{path}: {word} {bound}")


def _arrays(value: list[Any], schema: dict[str, Any], path: str, root: dict[str, Any], out: list[str]) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        out.append(f"{path}: at least {schema['minItems']} items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        out.append(f"{path}: at most {schema['maxItems']} items")
    if schema.get("uniqueItems"):
        seen = [json.dumps(v, sort_keys=True) for v in value]
        if len(set(seen)) != len(seen):
            out.append(f"{path}: items must not repeat")
    items = schema.get("items")
    if isinstance(items, (dict, bool)):
        for i, item in enumerate(value):
            _check(item, items, f"{path}[{i}]", root, out)


def _objects(value: dict[str, Any], schema: dict[str, Any], path: str, root: dict[str, Any], out: list[str]) -> None:
    props = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name not in value:
            out.append(f"{path}.{name}: required")
    extra = schema.get("additionalProperties")
    for name, item in value.items():
        if name in props:
            _check(item, props[name], f"{path}.{name}", root, out)
        elif extra is False:
            out.append(f"{path}.{name}: not allowed here")
        elif isinstance(extra, dict):
            _check(item, extra, f"{path}.{name}", root, out)


def _kind(value: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[name](value):
            return name
    return type(value).__name__


def free_form(schema: Any) -> bool:
    """Does the schema hold an object that names none of its fields ({"type": "object"} alone)?

    A native JSON-schema mode keeps only listed fields: Gemini returns such an object empty
    (tool arguments came back as {}). Callers ask for plain JSON instead and check the schema
    themselves (LLMClient.generate_json)."""
    if isinstance(schema, list):
        return any(free_form(s) for s in schema)
    if not isinstance(schema, dict):
        return False
    types = schema.get("type")
    if (types == "object" or (isinstance(types, list) and "object" in types))             and not schema.get("properties") and not schema.get("required")             and schema.get("additionalProperties") is not False:
        return True
    for key in ("properties", "$defs", "definitions"):
        if isinstance(schema.get(key), dict) and any(free_form(s) for s in schema[key].values()):
            return True
    return any(free_form(schema.get(key)) for key in ("items", "additionalProperties", "anyOf", "oneOf", "allOf"))


def instruction(schema: dict[str, Any] | None) -> str:
    """The request in words: for backends without a JSON mode, and a reminder for all the others."""
    text = "Answer with one JSON value only: no comments, no code fences, no text around it."
    if schema:
        text += f" It must follow this JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
    return text


def retry_note(found: list[str]) -> str:
    """What the model is told when its answer did not fit."""
    if not found:
        return "That was not valid JSON. Answer again with the JSON value only."
    lines = "\n".join(f"- {p}" for p in found)
    return f"That JSON does not fit the required shape:\n{lines}\nAnswer again with the corrected JSON value only."
