"""Tests for the generated trace module's ABP_TRACE_STREAM_FILE observer.

The stream observer powers the editor's live execution view: when the env
var is set, every trace event is appended to that file as one JSON line.
The JSON manifest must stay byte-identical either way — goldens, harness
diffs, and gate baselines are all downstream of it.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _generate_trace_module() -> str:
    raw = load_blueprint_yaml(FIXTURES / "basic_chatbot.yml")
    ir = compile_blueprint(BlueprintSpec.model_validate(raw))
    return LangGraphGenerator().generate(ir)["_abp_trace.py"]


@pytest.fixture
def import_trace_module(tmp_path, monkeypatch):
    """Write the rendered _abp_trace.py to disk; import it on demand so each
    test controls the env *before* the module-level observer registration."""
    (tmp_path / "_abp_trace.py").write_text(_generate_trace_module(), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("ABP_TRACE_FILE", raising=False)
    monkeypatch.delenv("ABP_TRACE_STREAM_FILE", raising=False)
    monkeypatch.delenv("ABP_TRACE_CONTENT", raising=False)

    def _import():
        sys.modules.pop("_abp_trace", None)
        return importlib.import_module("_abp_trace")

    yield _import
    sys.modules.pop("_abp_trace", None)


def _drive_run(trace_mod) -> dict:
    trace_mod.start_trace(
        run_id="r1", blueprint="demo", blueprint_version="1.0", mode="mock"
    )
    trace_mod.emit_trace_event("node_started", node="assistant", input_state={"x": 1})
    trace_mod.emit_trace_event("tool_called", tool="search", args={"q": "hi"})
    trace_mod.emit_trace_event("node_finished", node="assistant", output_state={"x": 2})
    return trace_mod.finalize_trace(status="success", output_state={"x": 2})


def test_stream_file_receives_one_json_line_per_event(
    import_trace_module, tmp_path, monkeypatch
) -> None:
    stream = tmp_path / "stream.jsonl"
    monkeypatch.setenv("ABP_TRACE_STREAM_FILE", str(stream))
    _drive_run(import_trace_module())

    lines = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
    events = [line["event"] for line in lines]
    # run_started (observer-level), then every emitted event incl. the
    # manifest's closing run_finished.
    assert events == [
        "run_started",
        "node_started",
        "tool_called",
        "node_finished",
        "run_finished",
    ]
    assert lines[0]["run"]["run_id"] == "r1"
    assert lines[1]["node"] == "assistant"
    # Privacy posture matches the manifest: hashes only, never content.
    assert "input_state_hash" in lines[1]
    assert "input_state" not in lines[1]


def test_no_stream_env_no_observer_no_file(import_trace_module, tmp_path) -> None:
    trace_mod = import_trace_module()
    assert trace_mod._TRACE_OBSERVERS == []
    _drive_run(trace_mod)
    assert list(tmp_path.glob("*.jsonl")) == []


def test_manifest_identical_with_and_without_stream(
    import_trace_module, tmp_path, monkeypatch
) -> None:
    def _stable(manifest: dict) -> str:
        # Timestamps differ between runs by construction; the normalized
        # form (what goldens and replay diffs use) must be identical.
        trace_mod = sys.modules["_abp_trace"]
        return trace_mod.stable_trace_json(manifest)

    without = _drive_run(import_trace_module())
    baseline = _stable(without)

    monkeypatch.setenv("ABP_TRACE_STREAM_FILE", str(tmp_path / "stream.jsonl"))
    with_stream = _drive_run(import_trace_module())
    assert _stable(with_stream) == baseline


def test_unwritable_stream_path_never_breaks_the_run(
    import_trace_module, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv(
        "ABP_TRACE_STREAM_FILE", str(tmp_path / "missing-dir" / "stream.jsonl")
    )
    manifest = _drive_run(import_trace_module())  # must not raise
    assert manifest["run"]["run_id"] == "r1"


# ---- opt-in content capture (E5.4) ---------------------------------------


def test_content_off_by_default_hashes_only(import_trace_module) -> None:
    manifest = _drive_run(import_trace_module())
    events = {e["event"]: e for e in manifest["trace"]}
    started = events["node_started"]
    assert "input_state_hash" in started
    assert "input_state" not in started  # default posture: no content
    finished = events["node_finished"]
    assert "output_state_hash" in finished
    assert "output_state" not in finished


def test_content_mode_adds_summarized_state(import_trace_module, monkeypatch) -> None:
    monkeypatch.setenv("ABP_TRACE_CONTENT", "1")
    manifest = _drive_run(import_trace_module())
    events = {e["event"]: e for e in manifest["trace"]}
    # Content sits alongside the hashes, never replacing them.
    assert events["node_started"]["input_state"] == {"x": 1}
    assert events["node_started"]["input_state_hash"]
    assert events["node_finished"]["output_state"] == {"x": 2}


def test_content_off_manifest_byte_identical(import_trace_module, monkeypatch) -> None:
    # The default (content off) must match the pre-E5.4 normalized manifest so
    # goldens / harness diffs / gate baselines are untouched.
    without = _drive_run(import_trace_module())
    base = sys.modules["_abp_trace"].stable_trace_json(without)
    # An explicit "off" value behaves like unset.
    monkeypatch.setenv("ABP_TRACE_CONTENT", "off")
    off = _drive_run(import_trace_module())
    assert sys.modules["_abp_trace"].stable_trace_json(off) == base
