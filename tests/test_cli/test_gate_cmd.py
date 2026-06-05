"""Tests for abp gate command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app
from agent_blueprint.eval_runner import EvalRunResult, EvalSuiteResult
from agent_blueprint.harness_runner import ScenarioResult

runner = CliRunner()

_BLUEPRINT_WITH_BOTH = """\
blueprint:
  name: "gate-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
harness:
  defaults:
    llm_mode: live
    tool_mode: live
  scenarios:
    - id: happy_path
      input:
        message: "hello"
      expected:
        outputs: {}
evals:
  suites:
    - id: router_accuracy
      metric: exact_match
      dataset: datasets/router_cases.yaml
"""

_BLUEPRINT_EMPTY = """\
blueprint:
  name: "gate-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
"""


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _write_baseline(tmp_path: Path, data: dict) -> Path:
    baseline_dir = tmp_path / ".abp"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    path = baseline_dir / "gate-baseline.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _baseline_data(
    *,
    blueprint: str = "gate-agent",
    scenarios: dict | None = None,
    suites: dict | None = None,
    schema_version: str = "1",
) -> dict:
    return {
        "schema_version": schema_version,
        "blueprint": blueprint,
        "blueprint_version": "1.0",
        "harness": {"scenarios": scenarios if scenarios is not None else {}},
        "evals": {"suites": suites if suites is not None else {}},
    }


def _patch_runners(
    monkeypatch,
    *,
    scenario_passed: bool = True,
    suite_passed: bool = True,
    suite_score: float = 1.0,
    install_seen: list | None = None,
):
    def fake_run_harness_scenario(ir, scenario, *, install):
        if install_seen is not None:
            install_seen.append(("harness", install))
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=scenario_passed,
            returncode=0,
            failures=[] if scenario_passed else ["boom"],
        )

    def fake_run_eval_suites(ir, suites, *, blueprint_dir, install):
        if install_seen is not None:
            install_seen.append(("evals", install))
        return EvalRunResult(
            blueprint=ir.name,
            blueprint_version=ir.version,
            passed=suite_passed,
            suites=[
                EvalSuiteResult(
                    suite_id=suite.id,
                    metric=suite.metric.value,
                    dataset=suite.dataset,
                    passed=suite_passed,
                    score=suite_score,
                    total=3,
                    passed_cases=3 if suite_passed else 1,
                    failed_cases=0 if suite_passed else 2,
                )
                for suite in suites
            ],
        )

    monkeypatch.setattr(
        "agent_blueprint.cli.gate_cmd.run_harness_scenario", fake_run_harness_scenario
    )
    monkeypatch.setattr("agent_blueprint.cli.gate_cmd.run_eval_suites", fake_run_eval_suites)


class TestGateCli:
    def test_nothing_to_gate_exits_one(self, tmp_path):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_EMPTY)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "no harness scenarios and no eval" in result.output

    def test_missing_baseline_is_actionable(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "--update-baseline" in result.output

    def test_update_baseline_writes_when_green(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint), "--update-baseline"])
        assert result.exit_code == 0
        baseline_path = tmp_path / ".abp" / "gate-baseline.json"
        assert baseline_path.exists()
        data = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "1"
        assert data["blueprint"] == "gate-agent"
        assert data["harness"]["scenarios"]["happy_path"] == {"passed": True}
        assert data["evals"]["suites"]["router_accuracy"]["score"] == 1.0

    def test_update_baseline_refuses_when_red(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _patch_runners(monkeypatch, scenario_passed=False)
        result = runner.invoke(app, ["gate", str(blueprint), "--update-baseline"])
        assert result.exit_code == 1
        assert "red baseline" in result.output
        assert not (tmp_path / ".abp" / "gate-baseline.json").exists()

    def test_passes_when_matches_baseline(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={"happy_path": {"passed": True}},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 0
        assert "Gate PASSED" in result.output

    def test_scenario_regression_fails(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={"happy_path": {"passed": True}},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch, scenario_passed=False)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "regressed: passed -> failed" in result.output
        assert "Gate FAILED" in result.output

    def test_missing_scenario_is_regression(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={
                "happy_path": {"passed": True},
                "deleted_scenario": {"passed": True},
            },
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "missing from current run" in result.output

    def test_new_scenario_passing_is_ok(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 0
        assert "is new" in result.output

    def test_new_scenario_failing_fails_gate(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch, scenario_passed=False)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "Gate FAILED" in result.output

    def test_eval_score_drop_fails(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={"happy_path": {"passed": True}},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch, suite_score=0.8)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "score dropped" in result.output

    def test_tolerance_allows_small_drop(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={"happy_path": {"passed": True}},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch, suite_score=0.8)
        result = runner.invoke(app, ["gate", str(blueprint), "--tolerance", "0.3"])
        assert result.exit_code == 0
        assert "Gate PASSED" in result.output

    def test_json_output_shape(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(
            scenarios={"happy_path": {"passed": True}},
            suites={"router_accuracy": {"passed": True, "score": 1.0, "total": 3, "passed_cases": 3}},
        ))
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["passed"] is True
        assert payload["all_green"] is True
        assert payload["regressions"] == []
        assert payload["current"]["schema_version"] == "1"

    def test_baseline_blueprint_mismatch_errors(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(blueprint="other-agent"))
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "belongs to blueprint 'other-agent'" in result.output

    def test_unsupported_baseline_schema_version_errors(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        _write_baseline(tmp_path, _baseline_data(schema_version="99"))
        _patch_runners(monkeypatch)
        result = runner.invoke(app, ["gate", str(blueprint)])
        assert result.exit_code == 1
        assert "unsupported baseline schema_version" in result.output

    def test_install_flag_passed_through(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        install_seen: list = []
        _patch_runners(monkeypatch, install_seen=install_seen)
        result = runner.invoke(app, ["gate", str(blueprint), "--update-baseline", "--install"])
        assert result.exit_code == 0
        assert ("harness", True) in install_seen
        assert ("evals", True) in install_seen

    def test_explicit_baseline_path_option(self, tmp_path, monkeypatch):
        blueprint = _write_blueprint(tmp_path, _BLUEPRINT_WITH_BOTH)
        custom = tmp_path / "custom-baseline.json"
        _patch_runners(monkeypatch)
        result = runner.invoke(
            app, ["gate", str(blueprint), "--update-baseline", "--baseline", str(custom)]
        )
        assert result.exit_code == 0
        assert custom.exists()
        result = runner.invoke(app, ["gate", str(blueprint), "--baseline", str(custom)])
        assert result.exit_code == 0
        assert "Gate PASSED" in result.output
