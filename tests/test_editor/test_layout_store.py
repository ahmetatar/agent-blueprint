"""Tests for the editor's canvas-layout sidecar (.abp/editor-layout.json)."""

import json
from pathlib import Path

from agent_blueprint.editor import layout_store


def test_load_missing_sidecar_is_empty(tmp_path: Path) -> None:
    assert layout_store.load_layout(tmp_path / "bp.yml") == {}


def test_save_load_roundtrip(tmp_path: Path) -> None:
    bp = tmp_path / "bp.yml"
    layout_store.save_layout(bp, {"assistant": {"x": 120.0, "y": -42.5}})
    assert layout_store.load_layout(bp) == {"assistant": {"x": 120.0, "y": -42.5}}


def test_sibling_blueprints_do_not_clobber(tmp_path: Path) -> None:
    one, two = tmp_path / "one.yml", tmp_path / "two.yml"
    layout_store.save_layout(one, {"a": {"x": 1.0, "y": 1.0}})
    layout_store.save_layout(two, {"b": {"x": 2.0, "y": 2.0}})
    assert layout_store.load_layout(one) == {"a": {"x": 1.0, "y": 1.0}}
    assert layout_store.load_layout(two) == {"b": {"x": 2.0, "y": 2.0}}
    data = json.loads(layout_store.layout_path(one).read_text(encoding="utf-8"))
    assert set(data) == {"one", "two"}


def test_save_replaces_stale_entries(tmp_path: Path) -> None:
    bp = tmp_path / "bp.yml"
    layout_store.save_layout(bp, {"a": {"x": 1.0, "y": 1.0}, "removed": {"x": 9.0, "y": 9.0}})
    layout_store.save_layout(bp, {"a": {"x": 1.0, "y": 1.0}})
    assert layout_store.load_layout(bp) == {"a": {"x": 1.0, "y": 1.0}}


def test_corrupt_sidecar_treated_as_absent(tmp_path: Path) -> None:
    bp = tmp_path / "bp.yml"
    sidecar = layout_store.layout_path(bp)
    sidecar.parent.mkdir()
    sidecar.write_text("not json{", encoding="utf-8")
    assert layout_store.load_layout(bp) == {}
    # Saving over a corrupt sidecar recovers instead of raising.
    layout_store.save_layout(bp, {"a": {"x": 0.0, "y": 0.0}})
    assert layout_store.load_layout(bp) == {"a": {"x": 0.0, "y": 0.0}}


def test_non_mapping_sidecar_treated_as_absent(tmp_path: Path) -> None:
    bp = tmp_path / "bp.yml"
    sidecar = layout_store.layout_path(bp)
    sidecar.parent.mkdir()
    sidecar.write_text(json.dumps(["not", "a", "mapping"]), encoding="utf-8")
    assert layout_store.load_layout(bp) == {}
