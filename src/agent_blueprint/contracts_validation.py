"""Package-level output-contract validation.

Mirrors the schema semantics of the generated runtime validator
(templates/langgraph/nodes.py.j2 `_validate_schema_value`) but collects every
violation instead of raising on the first one, so harness assertions can
report all problems at once. Generated code intentionally does NOT import
this module — generated projects stay standalone.
"""

from __future__ import annotations

from typing import Any

from agent_blueprint.models.contracts import OutputContractDef


def validate_output_contract(value: Any, contract: OutputContractDef) -> list[str]:
    """Validate a payload against an output contract.

    Returns a list of human-readable violation strings; an empty list means
    the payload satisfies the contract.
    """
    schema = contract.model_dump(by_alias=True)
    violations: list[str] = []
    _validate("output", value, schema, violations)
    return violations


def _validate(path: str, value: Any, schema: dict[str, Any], violations: list[str]) -> None:
    if value is None:
        if not schema.get("nullable", False):
            violations.append(f"{path} cannot be null")
        return

    expected_type = str(schema.get("type", "")).lower()

    if expected_type in {"string", "str"}:
        if not isinstance(value, str):
            violations.append(f"{path} must be of type '{schema['type']}'")
            return
    elif expected_type in {"integer", "int"}:
        if not (isinstance(value, int) and not isinstance(value, bool)):
            violations.append(f"{path} must be of type '{schema['type']}'")
            return
    elif expected_type in {"number", "float"}:
        if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
            violations.append(f"{path} must be of type '{schema['type']}'")
            return
    elif expected_type in {"boolean", "bool"}:
        if not isinstance(value, bool):
            violations.append(f"{path} must be of type '{schema['type']}'")
            return
    elif expected_type in {"list", "array"}:
        if not isinstance(value, list):
            violations.append(f"{path} must be of type '{schema['type']}'")
            return
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate(f"{path}[{index}]", item, item_schema, violations)
    elif expected_type in {"object", "dict"}:
        if not isinstance(value, dict):
            violations.append(f"{path} must be of type '{schema['type']}'")
            return
        _validate_object(path, value, schema, violations)
        return

    allowed_values = schema.get("enum")
    if allowed_values is not None and value not in allowed_values:
        violations.append(f"{path} must be one of {allowed_values}")


def _validate_object(
    path: str,
    value: dict[str, Any],
    schema: dict[str, Any],
    violations: list[str],
) -> None:
    properties = schema.get("properties", {})
    required_fields = schema.get("required", [])
    additional = schema.get("additionalProperties", schema.get("additional_properties", None))

    for field_name in required_fields:
        if field_name not in value:
            violations.append(f"{path}.{field_name} is required")

    for field_name, field_schema in properties.items():
        if field_name in value:
            _validate(f"{path}.{field_name}", value[field_name], field_schema, violations)

    extra_fields = sorted(set(value.keys()) - set(properties.keys()))
    if additional is False and extra_fields:
        violations.append(f"{path} has unknown field(s): {', '.join(extra_fields)}")
    elif isinstance(additional, dict):
        for field_name in extra_fields:
            _validate(f"{path}.{field_name}", value[field_name], additional, violations)
