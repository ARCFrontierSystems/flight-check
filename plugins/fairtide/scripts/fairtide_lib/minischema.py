"""A small JSON Schema validator for the subset of keywords Fairtide's schemas use.

Supported: type, properties, required, additionalProperties (boolean or schema),
items, enum, const, minLength, maxLength, minItems, maxItems, minimum, maximum,
pattern, and local $ref ("#/$defs/name"). Unsupported keywords raise an error so a
schema change can never be silently ignored.
"""

import re

SUPPORTED = {
    "$schema", "$id", "$ref", "$defs", "title", "description", "type", "properties",
    "required", "additionalProperties", "items", "enum", "const", "minLength",
    "maxLength", "minItems", "maxItems", "minimum", "maximum", "pattern",
}

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


class SchemaError(Exception):
    """Raised when a schema uses a keyword this validator does not implement."""


def _is_type(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected = _TYPES.get(name)
    if expected is None:
        raise SchemaError("unknown type %r" % name)
    if expected is dict or expected is list or expected is str:
        return isinstance(value, expected)
    return isinstance(value, expected)


def _resolve(root, ref):
    if not ref.startswith("#/"):
        raise SchemaError("only local $ref values are supported: %r" % ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(instance, schema, root=None, path="$"):
    """Return a list of human-readable error strings (empty when valid)."""
    root = schema if root is None else root
    errors = []
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise SchemaError("unsupported keywords at %s: %s" % (path, ", ".join(sorted(unknown))))
    if "$ref" in schema:
        return validate(instance, _resolve(root, schema["$ref"]), root, path)

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(instance, t) for t in types):
            return ["%s: expected %s, got %s" % (path, " or ".join(types), type(instance).__name__)]
    if "const" in schema and instance != schema["const"]:
        errors.append("%s: must equal %r" % (path, schema["const"]))
    if "enum" in schema and instance not in schema["enum"]:
        shown = ", ".join(repr(v) for v in schema["enum"][:12])
        errors.append("%s: %r is not one of [%s]" % (path, instance, shown))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: shorter than %d characters" % (path, schema["minLength"]))
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: longer than %d characters" % (path, schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: %r does not match pattern %s" % (path, instance, schema["pattern"]))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: less than %s" % (path, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: greater than %s" % (path, schema["maximum"]))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: needs at least %d item(s)" % (path, schema["minItems"]))
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("%s: has more than %d items" % (path, schema["maxItems"]))
        if "items" in schema:
            for i, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], root, "%s[%d]" % (path, i)))

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required property %r" % (path, key))
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = "%s.%s" % (path, key)
            if key in props:
                errors.extend(validate(value, props[key], root, child))
            elif additional is False:
                errors.append("%s: unexpected property %r" % (path, key))
            elif isinstance(additional, dict):
                errors.extend(validate(value, additional, root, child))
    return errors


def validate_def(instance, schema, def_name):
    """Validate against schema["$defs"][def_name] with refs resolved against the full schema."""
    return validate(instance, {"$ref": "#/$defs/%s" % def_name}, root=schema)
