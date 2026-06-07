"""Editor-private canvas layout sidecar (`<blueprint_dir>/.abp/editor-layout.json`).

Canvas coordinates are presentation state, not blueprint semantics, so they
never touch the YAML file. They live next to the other `.abp/` artifacts
(traces, gate baseline), keyed by blueprint stem so sibling blueprints in one
directory don't clobber each other. The sidecar is never required and safe to
delete; a corrupt or missing file is treated as "no saved layout" and the
canvas falls back to automatic layout.
"""

import json
from pathlib import Path
from typing import Any


def layout_path(blueprint_path: Path) -> Path:
    return blueprint_path.parent / ".abp" / "editor-layout.json"


def _read_sidecar(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_layout(blueprint_path: Path) -> dict[str, dict[str, float]]:
    """Saved node positions for this blueprint (`{node_id: {"x": .., "y": ..}}`)."""
    entry = _read_sidecar(layout_path(blueprint_path)).get(blueprint_path.stem)
    return entry if isinstance(entry, dict) else {}


def save_layout(blueprint_path: Path, positions: dict[str, dict[str, float]]) -> None:
    """Replace this blueprint's layout entry, preserving sibling blueprints'."""
    path = layout_path(blueprint_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = _read_sidecar(path)
    sidecar[blueprint_path.stem] = positions
    path.write_text(json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8")
