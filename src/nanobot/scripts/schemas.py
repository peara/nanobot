from __future__ import annotations

from typing import Any

_SUPPORTED_PRIMITIVE_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def validate_schema_definition(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(schema, dict):
        return ["schema must be an object"]
    schema_type = schema.get("type")
    if schema_type is None:
        errors.append("schema.type is required")
    elif schema_type not in _SUPPORTED_PRIMITIVE_TYPES:
        errors.append(f"unsupported schema type: {schema_type}")

    if schema_type == "object":
        properties = schema.get("properties", {})
        if properties is not None and not isinstance(properties, dict):
            errors.append("schema.properties must be an object")
        required = schema.get("required", [])
        if required is not None and not isinstance(required, list):
            errors.append("schema.required must be an array")
        for name, value in (properties or {}).items():
            if not isinstance(value, dict):
                errors.append(f"property '{name}' schema must be an object")
            else:
                errors.extend([f"property '{name}': {item}" for item in validate_schema_definition(value)])

    if schema_type == "array":
        items = schema.get("items")
        if items is None:
            errors.append("array schema.items is required")
        elif not isinstance(items, dict):
            errors.append("array schema.items must be an object")
        else:
            errors.extend([f"array items: {item}" for item in validate_schema_definition(items)])
    return errors


def validate_data_against_schema(data: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")

    if schema_type == "object":
        if not isinstance(data, dict):
            return [f"{path} must be object"]
        required = schema.get("required", []) or []
        for field in required:
            if field not in data:
                errors.append(f"{path}.{field} is required")
        properties = schema.get("properties", {}) or {}
        for key, value_schema in properties.items():
            if key in data:
                errors.extend(validate_data_against_schema(data[key], value_schema, f"{path}.{key}"))
        return errors

    if schema_type == "array":
        if not isinstance(data, list):
            return [f"{path} must be array"]
        item_schema = schema.get("items", {})
        for idx, item in enumerate(data):
            errors.extend(validate_data_against_schema(item, item_schema, f"{path}[{idx}]"))
        return errors

    if schema_type == "string":
        if not isinstance(data, str):
            return [f"{path} must be string"]
        return []

    if schema_type == "number":
        if not isinstance(data, (float, int)) or isinstance(data, bool):
            return [f"{path} must be number"]
        return []

    if schema_type == "integer":
        if not isinstance(data, int) or isinstance(data, bool):
            return [f"{path} must be integer"]
        return []

    if schema_type == "boolean":
        if not isinstance(data, bool):
            return [f"{path} must be boolean"]
        return []

    if schema_type == "null":
        if data is not None:
            return [f"{path} must be null"]
        return []

    return []
