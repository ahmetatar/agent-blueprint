"""YAML loader with ${...} variable interpolation."""

import os
import re
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from agent_blueprint.exceptions import BlueprintValidationError

_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

yaml = YAML()
yaml.preserve_quotes = True


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _resolve_relative_file_ref(value: Any, base_dir: Path) -> Any:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _resolve_harness_paths(harness: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    resolved = dict(harness)
    defaults = resolved.get("defaults")
    if isinstance(defaults, dict) and "replay_trace" in defaults:
        updated_defaults = dict(defaults)
        updated_defaults["replay_trace"] = _resolve_relative_file_ref(
            updated_defaults.get("replay_trace"),
            base_dir,
        )
        resolved["defaults"] = updated_defaults

    scenarios = resolved.get("scenarios")
    if isinstance(scenarios, list):
        updated_scenarios: list[Any] = []
        for scenario in scenarios:
            if isinstance(scenario, dict) and "replay_trace" in scenario:
                updated = dict(scenario)
                updated["replay_trace"] = _resolve_relative_file_ref(
                    updated.get("replay_trace"),
                    base_dir,
                )
                updated_scenarios.append(updated)
            else:
                updated_scenarios.append(scenario)
        resolved["scenarios"] = updated_scenarios
    return resolved


def _normalize_harness_fragment(raw: Any, source: Path) -> dict[str, Any]:
    plain = _to_plain(raw)
    if not isinstance(plain, dict):
        raise BlueprintValidationError(f"Harness file must contain a mapping: {source}")
    if "harness" in plain:
        harness = plain["harness"]
        if not isinstance(harness, dict):
            raise BlueprintValidationError(f"'harness' in {source} must be a mapping")
        return _resolve_harness_paths(harness, source.parent)
    return _resolve_harness_paths(plain, source.parent)


def _load_yaml_plain(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.load(f)
    if raw is None:
        raise BlueprintValidationError(f"YAML file is empty: {path}")
    plain = _to_plain(raw)
    if not isinstance(plain, dict):
        raise BlueprintValidationError(f"Expected a YAML mapping at top level: {path}")
    return plain


def _resolve_harness_refs(
    document: dict[str, Any],
    *,
    blueprint_path: Path,
    seen: set[Path] | None = None,
) -> dict[str, Any]:
    harness = document.get("harness")
    if not isinstance(harness, dict):
        return document

    seen = set() if seen is None else set(seen)
    merged_defaults: dict[str, Any] = {}
    merged_scenarios: list[Any] = []

    refs: list[str] = []
    if isinstance(harness.get("file"), str):
        refs.append(harness["file"])
    files_value = harness.get("files", [])
    if isinstance(files_value, list):
        refs.extend(str(item) for item in files_value)
    elif files_value:
        raise BlueprintValidationError("harness.files must be a list of file paths")

    for ref in refs:
        ref_path = (blueprint_path.parent / ref).resolve()
        if ref_path in seen:
            raise BlueprintValidationError(f"Cyclic harness file reference detected: {ref_path}")
        if not ref_path.exists():
            raise BlueprintValidationError(f"Harness file not found: {ref_path}")
        if ref_path.suffix not in {".yml", ".yaml"}:
            raise BlueprintValidationError(
                f"Expected harness file to end with .yml or .yaml, got: {ref_path.suffix}"
            )
        seen.add(ref_path)
        loaded = _load_yaml_plain(ref_path)
        fragment = _resolve_harness_refs(
            {"harness": _normalize_harness_fragment(loaded, ref_path)},
            blueprint_path=ref_path,
            seen=seen,
        )["harness"]
        fragment_defaults = fragment.get("defaults", {})
        if not isinstance(fragment_defaults, dict):
            raise BlueprintValidationError(f"harness.defaults in {ref_path} must be a mapping")
        merged_defaults = _merge_dicts(merged_defaults, fragment_defaults)

        fragment_scenarios = fragment.get("scenarios", [])
        if not isinstance(fragment_scenarios, list):
            raise BlueprintValidationError(f"harness.scenarios in {ref_path} must be a list")
        merged_scenarios.extend(fragment_scenarios)

    inline_defaults = harness.get("defaults", {})
    if inline_defaults and not isinstance(inline_defaults, dict):
        raise BlueprintValidationError("harness.defaults must be a mapping")
    merged_defaults = _merge_dicts(merged_defaults, inline_defaults if isinstance(inline_defaults, dict) else {})

    inline_scenarios = harness.get("scenarios", [])
    if inline_scenarios and not isinstance(inline_scenarios, list):
        raise BlueprintValidationError("harness.scenarios must be a list")
    merged_scenarios.extend(inline_scenarios if isinstance(inline_scenarios, list) else [])

    resolved_harness = dict(harness)
    if refs:
        resolved_harness.pop("file", None)
        resolved_harness.pop("files", None)
    resolved_harness["defaults"] = merged_defaults
    resolved_harness["scenarios"] = merged_scenarios
    resolved_harness = _resolve_harness_paths(resolved_harness, blueprint_path.parent)

    updated = dict(document)
    updated["harness"] = resolved_harness
    return updated


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
        def replace_match(m: re.Match[str]) -> str:
            path = m.group(1)
            # ${env.VAR_NAME} → read from environment variables at load time
            if path.startswith("env."):
                env_var = path[4:]
                return os.environ.get(env_var, f"${{{path}}}")
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


def _load_dotenv(blueprint_path: Path) -> None:
    """Load .env files into os.environ (existing vars are never overwritten).

    Search order (first found wins):
      1. <blueprint_dir>/.env
      2. <cwd>/.env
    """
    candidates = [
        blueprint_path.parent / ".env",
        Path.cwd() / ".env",
    ]
    for env_file in candidates:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
            break  # stop at first found


def load_blueprint_yaml(path: Path) -> dict[str, Any]:
    """Load a blueprint YAML file, resolve variables, and return a plain dict.

    Before interpolation, loads the nearest .env file (blueprint dir or cwd)
    so that ${env.VAR} references resolve without needing to manually export
    variables. Existing environment variables always take precedence over .env.
    """
    if not path.exists():
        raise BlueprintValidationError(f"Blueprint file not found: {path}")
    if path.suffix not in {".yml", ".yaml"}:
        raise BlueprintValidationError(
            f"Expected a .yml or .yaml file, got: {path.suffix}"
        )

    _load_dotenv(path)

    plain = _load_yaml_plain(path)
    plain = _resolve_harness_refs(plain, blueprint_path=path)
    # Interpolate variables using the full document as context
    interpolated = cast(dict[str, Any], _interpolate_value(plain, plain))
    return interpolated
