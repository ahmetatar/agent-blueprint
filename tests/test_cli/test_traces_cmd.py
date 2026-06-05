"""Tests for abp traces list/export commands."""

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app
from agent_blueprint.harness_runner import ScenarioResult
from agent_blueprint.trace_store import build_trace_record, save_trace_record

runner = CliRunner()


def _store_record(
    store: Path,
    *,
    scenario_id: str = "happy_path",
    passed: bool = False,
    blueprint: str = "test-agent",
    saved_at: str = "2026-06-05T10:00:00Z",
) -> Path:
    result = ScenarioResult(
        scenario_id=scenario_id,
        passed=passed,
        returncode=0,
        failures=[] if passed else ["route mismatch"],
        trace_manifest={
            "run": {
                "run_id": scenario_id,
                "blueprint": blueprint,
                "blueprint_version": "1.0",
                "mode": "mock",
                "seed": 7,
            },
            "trace": [
                {"event": "tool_called", "tool": "lookup_invoice"},
                {"event": "node_finished", "node": "billing"},
            ],
            "replay": {
                "llm_outputs": {"assistant": [{"content": "ok"}]},
                "tool_outputs": {},
            },
        },
    )
    record = build_trace_record(
        scenario_id=scenario_id,
        input={"message": "hello"},
        result=result,
        saved_at=saved_at,
    )
    return save_trace_record(record, store_dir=store)


class TestTracesList:
    def test_empty_dir_is_friendly(self, tmp_path):
        result = runner.invoke(app, ["traces", "list", "--dir", str(tmp_path / "none")])
        assert result.exit_code == 0
        assert "No trace records found" in result.output

    def test_renders_table(self, tmp_path):
        _store_record(tmp_path, scenario_id="happy_path", passed=False)
        _store_record(tmp_path, scenario_id="edge_case", passed=True,
                      saved_at="2026-06-05T11:00:00Z")
        result = runner.invoke(app, ["traces", "list", "--dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "happy_path" in result.output
        assert "edge_case" in result.output
        assert "FAILED" in result.output
        assert "PASSED" in result.output
        assert "2 record(s)" in result.output

    def test_status_filter(self, tmp_path):
        _store_record(tmp_path, scenario_id="happy_path", passed=False)
        _store_record(tmp_path, scenario_id="edge_case", passed=True,
                      saved_at="2026-06-05T11:00:00Z")
        result = runner.invoke(
            app, ["traces", "list", "--dir", str(tmp_path), "--status", "failed"]
        )
        assert result.exit_code == 0
        assert "happy_path" in result.output
        assert "edge_case" not in result.output

    def test_invalid_status_errors(self, tmp_path):
        result = runner.invoke(
            app, ["traces", "list", "--dir", str(tmp_path), "--status", "bogus"]
        )
        assert result.exit_code == 1


class TestTracesExport:
    def test_creates_dataset_with_empty_expected(self, tmp_path):
        _store_record(tmp_path, passed=False)
        output = tmp_path / "datasets" / "regressions.json"
        result = runner.invoke(
            app, ["traces", "export", "--dir", str(tmp_path), "--output", str(output)]
        )
        assert result.exit_code == 0
        assert "1 new case(s)" in " ".join(result.output.split())
        payload = json.loads(output.read_text(encoding="utf-8"))
        case = payload["cases"][0]
        assert case["expected"] == {}
        assert case["llm_mode"] == "mock"
        assert case["fixtures"]["llm_outputs"]["assistant"] == [{"content": "ok"}]

    def test_golden_fills_expected(self, tmp_path):
        _store_record(tmp_path, passed=True)
        output = tmp_path / "golden.json"
        result = runner.invoke(
            app,
            ["traces", "export", "--dir", str(tmp_path), "--output", str(output),
             "--status", "passed", "--golden"],
        )
        assert result.exit_code == 0
        case = json.loads(output.read_text(encoding="utf-8"))["cases"][0]
        assert case["expected"]["route"] == "billing"
        assert case["expected"]["tools_called"] == ["lookup_invoice"]

    def test_merge_dedup(self, tmp_path):
        _store_record(tmp_path, passed=False)
        output = tmp_path / "regressions.json"
        first = runner.invoke(
            app, ["traces", "export", "--dir", str(tmp_path), "--output", str(output)]
        )
        assert "1 new case(s)" in " ".join(first.output.split())
        second = runner.invoke(
            app, ["traces", "export", "--dir", str(tmp_path), "--output", str(output)]
        )
        assert second.exit_code == 0
        # Rich wraps long lines (CI tmp paths are long) — compare unwrapped text.
        unwrapped = " ".join(second.output.split())
        assert "0 new case(s)" in unwrapped
        assert "1 already present" in unwrapped

    def test_no_records_exits_zero(self, tmp_path):
        output = tmp_path / "regressions.json"
        result = runner.invoke(
            app, ["traces", "export", "--dir", str(tmp_path), "--output", str(output)]
        )
        assert result.exit_code == 0
        assert "nothing to export" in result.output
        assert not output.exists()


def _store_eval_record(store: Path, *, scenario_id: str = "exported__a1b2c3d4") -> Path:
    result = ScenarioResult(
        scenario_id=scenario_id,
        passed=False,
        returncode=0,
        failures=["still failing"],
        trace_manifest={
            "run": {"run_id": scenario_id, "blueprint": "test-agent",
                    "blueprint_version": "1.0", "mode": "mock"},
            "trace": [],
            "replay": {"llm_outputs": {}, "tool_outputs": {}},
        },
    )
    record = build_trace_record(
        scenario_id=scenario_id,
        input={"message": "hi"},
        result=result,
        saved_at="2026-06-05T12:00:00Z",
        origin="eval",
    )
    return save_trace_record(record, store_dir=store)


class TestOriginFiltering:
    def test_export_skips_eval_origin_by_default(self, tmp_path):
        _store_record(tmp_path, passed=False)
        _store_eval_record(tmp_path)
        output = tmp_path / "regressions.json"
        result = runner.invoke(
            app, ["traces", "export", "--dir", str(tmp_path), "--output", str(output)]
        )
        assert result.exit_code == 0
        assert "1 new case(s)" in " ".join(result.output.split())
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert len(payload["cases"]) == 1
        assert payload["cases"][0]["id"].startswith("happy_path__")

    def test_export_origin_all_includes_eval(self, tmp_path):
        _store_record(tmp_path, passed=False)
        _store_eval_record(tmp_path)
        output = tmp_path / "regressions.json"
        result = runner.invoke(
            app,
            ["traces", "export", "--dir", str(tmp_path), "--output", str(output),
             "--origin", "all"],
        )
        assert result.exit_code == 0
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert len(payload["cases"]) == 2

    def test_list_origin_filter_and_column(self, tmp_path):
        _store_record(tmp_path, passed=False)
        _store_eval_record(tmp_path)
        result = runner.invoke(
            app, ["traces", "list", "--dir", str(tmp_path), "--origin", "eval"]
        )
        assert result.exit_code == 0
        # Rich truncates long cell values — assert on the filtered count instead.
        assert "1 record(s)" in result.output
        assert "happy_path" not in result.output
        assert "eval" in result.output
