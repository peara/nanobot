from __future__ import annotations

import json
from typing import Any

_SUPPORTED_PRIMITIVE_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
CREATE_SCRIPT_SCHEMA_ERROR = "schema.type is required"
CREATE_SCRIPT_REQUIRED_FIELDS = {
    "name",
    "description",
    "code",
    "params_schema",
    "output_schema",
    "embedding_text",
    "created_by",
}


def default_create_script_params_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "description": "Repository issues URL"},
            "max_pages": {"type": "integer", "default": 5},
        },
    }


def default_create_script_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["issues"],
        "properties": {
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["title", "url"],
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                    },
                },
            }
        },
    }


def default_create_script_selector_manifest() -> dict[str, list[str]]:
    return {
        "issue_row": ["div[id^='issue_']", ".js-issue-row", "[data-testid='list-view-item']"],
        "issue_title_link": ["a[data-hovercard-type='issue']", "a.js-navigation-open", "a.Link--primary"],
        "next_page": ["a.next_page", "a[rel='next']", "a[aria-label='Next Page']"],
    }


def normalize_create_script_args(args: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    params_schema = normalized.get("params_schema")
    if not isinstance(params_schema, dict) or "type" not in params_schema:
        normalized["params_schema"] = default_create_script_params_schema()
    output_schema = normalized.get("output_schema")
    if not isinstance(output_schema, dict) or "type" not in output_schema:
        normalized["output_schema"] = default_create_script_output_schema()
    selector_manifest = normalized.get("selector_manifest")
    if not isinstance(selector_manifest, dict):
        normalized["selector_manifest"] = default_create_script_selector_manifest()
    return normalized


def is_create_script_schema_error(result_text: str) -> bool:
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    if str(error.get("type", "")) != "PARAMS_VALIDATION_ERROR":
        return False
    message = str(error.get("message", ""))
    return CREATE_SCRIPT_SCHEMA_ERROR in message


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
