"""A small JSON-schema validator for Claude's structured output.

The schemas themselves live in the topic modules (ews_news/topics/*.py); the
same schema is sent to the Claude API (structured outputs), so the model is
constrained to that shape. `validate()` re-checks it locally (and in tests).
"""

_PY_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _type_ok(value, t: str) -> bool:
    if t in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _PY_TYPES[t])


def validate(value, schema: dict, path: str = "$") -> list:
    """Return a list of error strings (empty list = valid)."""
    errors = []
    types = schema.get("type")
    if types:
        types = types if isinstance(types, list) else [types]
        if not any(_type_ok(value, t) for t in types):
            return [f"{path}: expected {types}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing '{key}'")
        for key, v in value.items():
            if key in props:
                errors.extend(validate(v, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected '{key}'")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errors.extend(validate(v, schema["items"], f"{path}[{i}]"))
    return errors
