"""A small JSON Schema checker for the draft schemas (stdlib only).

    errors(document, schema) -> ["$.items[2].id: does not match ^[a-z]...", ...]   # [] = valid

Supports the subset the draft schemas use: `type` (a name or a list of names),
`properties`, `required`, `additionalProperties` (false or a schema), `items`,
`minItems`, `maxItems`, `enum`, `pattern` (searched, so anchor it), `minLength`,
`maxLength`, `minimum`, `maximum`. The same schema dicts are sent with each
request, so the model is asked for exactly what is checked here.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def errors(document: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    out: list[str] = []
    _check(document, schema, path, out)
    return out


def _check(v: Any, schema: Mapping[str, Any], path: str, out: list[str]) -> None:
    kinds = schema.get("type")
    if kinds is not None:
        names = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(_TYPES[n](v) for n in names):
            out.append(f"{path}: expected {' or '.join(names)}, got {_kind(v)}")
            return
    if "enum" in schema and v not in schema["enum"]:
        out.append(f"{path}: {v!r} is not one of {', '.join(map(repr, schema['enum']))}")
    if isinstance(v, str):
        if "minLength" in schema and len(v) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(v) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], v):
            out.append(f"{path}: {v!r} does not match {schema['pattern']}")
    if _TYPES["number"](v):
        if "minimum" in schema and v < schema["minimum"]:
            out.append(f"{path}: {v} is below {schema['minimum']}")
        if "maximum" in schema and v > schema["maximum"]:
            out.append(f"{path}: {v} is above {schema['maximum']}")
    if isinstance(v, list):
        if "minItems" in schema and len(v) < schema["minItems"]:
            out.append(f"{path}: needs at least {schema['minItems']} entries, got {len(v)}")
        if "maxItems" in schema and len(v) > schema["maxItems"]:
            out.append(f"{path}: allows at most {schema['maxItems']} entries, got {len(v)}")
        if "items" in schema:
            for i, x in enumerate(v):
                _check(x, schema["items"], f"{path}[{i}]", out)
    if isinstance(v, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", ()):
            if key not in v:
                out.append(f"{path}: missing `{key}`")
        extra = schema.get("additionalProperties", True)
        for key, x in v.items():
            if key in props:
                _check(x, props[key], f"{path}.{key}", out)
            elif extra is False:
                out.append(f"{path}: unexpected key `{key}`")
            elif isinstance(extra, Mapping):
                _check(x, extra, f"{path}.{key}", out)


def _kind(v: Any) -> str:
    for name in ("null", "boolean", "integer", "number", "string", "array", "object"):
        if _TYPES[name](v):
            return name
    return type(v).__name__
