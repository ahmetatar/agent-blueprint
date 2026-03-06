"""YAML loader with ${...} variable interpolation."""

import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from agent_blueprint.exceptions import BlueprintValidationError

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

yaml = YAML()
yaml.preserve_quotes = True


def _get_nested(data: dict[str, Any], path: str) -> Any:
    """Traverse a dot-separated path like 'settings.default_model'."""
    parts = path.strip().split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict):
            raise KeyError(f"Cannot access '{part}' on a non-dict value")
        current = current[part]
    return current


def _interpolate_value(value: Any, root: dict[str, Any]) -> Any:
    """Recursively resolve ${...} expressions in a value."""
    if isinstance(value, str):
        def replace_match(m: re.Match) -> str:  # type: ignore[type-arg]
            path = m.group(1)
            try:
                resolved = _get_nested(root, path)
            except (KeyError, TypeError) as e:
                raise BlueprintValidationError(
                    f"Variable interpolation failed: '${{{path}}}' could not be resolved: {e}"
                ) from e
            return str(resolved)

        return _VAR_PATTERN.sub(replace_match, value)
    elif isinstance(value, (CommentedMap, dict)):
        return {k: _interpolate_value(v, root) for k, v in value.items()}
    elif isinstance(value, (CommentedSeq, list)):
        return [_interpolate_value(item, root) for item in value]
    return value


def _to_plain(value: Any) -> Any:
    """Convert ruamel.yaml objects to plain Python dicts/lists."""
    if isinstance(value, (CommentedMap, dict)):
        return {str(k): _to_plain(v) for k, v in value.items()}
    elif isinstance(value, (CommentedSeq, list)):
        return [_to_plain(item) for item in value]
    return value


def load_blueprint_yaml(path: Path) -> dict[str, Any]:
    """Load a blueprint YAML file, resolve variables, and return a plain dict."""
    if not path.exists():
        raise BlueprintValidationError(f"Blueprint file not found: {path}")
    if path.suffix not in {".yml", ".yaml"}:
        raise BlueprintValidationError(
            f"Expected a .yml or .yaml file, got: {path.suffix}"
        )

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.load(f)

    if raw is None:
        raise BlueprintValidationError(f"Blueprint file is empty: {path}")

    plain: dict[str, Any] = _to_plain(raw)
    # Interpolate variables using the full document as context
    interpolated: dict[str, Any] = _interpolate_value(plain, plain)  # type: ignore[assignment]
    return interpolated
