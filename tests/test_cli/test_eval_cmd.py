"""Tests for abp eval command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app
from agent_blueprint.eval_runner import EvalRunResult, EvalSuiteResult


runner = CliRunner()


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


class TestEvalCli:
    def test_requires_eval_suites(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
""",
        )

        result = runner.invoke(app, ["eval", str(blueprint)])

        assert result.exit_code == 1
        assert "no eval suites are defined" in result.output

    def test_filters_to_single_suite_and_writes_json(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
evals:
  suites:
    - id: router_accuracy
      metric: exact_match
      dataset: datasets/router_cases.yaml
    - id: policy
      metric: policy_violations
      dataset: datasets/policy_cases.yaml
""",
        )
        seen: list[str] = []

        def fake_run_eval_suites(ir, suites, *, blueprint_dir, install, **kwargs):
            seen.extend(item.id for item in suites)
            return EvalRunResult(
                blueprint=ir.name,
                blueprint_version=ir.version,
                passed=True,
                suites=[
                    EvalSuiteResult(
                        suite_id=suites[0].id,
                        metric=suites[0].metric.value,
                        dataset=suites[0].dataset,
                        passed=True,
                        score=1.0,
                        total=1,
                        passed_cases=1,
                        failed_cases=0,
                    )
                ],
            )

        output = tmp_path / "eval-results.json"
        monkeypatch.setattr("agent_blueprint.cli.eval_cmd.run_eval_suites", fake_run_eval_suites)

        result = runner.invoke(
            app,
            ["eval", str(blueprint), "--suite", "policy", "--output", str(output)],
        )

        assert result.exit_code == 0
        assert seen == ["policy"]
        assert "1 passed, 0 failed" in result.output
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["suites"][0]["suite_id"] == "policy"
        assert payload["suites"][0]["score"] == 1.0

    def test_exits_non_zero_when_suite_fails(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
evals:
  suites:
    - id: router_accuracy
      metric: exact_match
      dataset: datasets/router_cases.yaml
""",
        )

        def fake_run_eval_suites(ir, suites, *, blueprint_dir, install, **kwargs):
            return EvalRunResult(
                blueprint=ir.name,
                blueprint_version=ir.version,
                passed=False,
                suites=[
                    EvalSuiteResult(
                        suite_id="router_accuracy",
                        metric="exact_match",
                        dataset="datasets/router_cases.yaml",
                        passed=False,
                        score=0.0,
                        total=1,
                        passed_cases=0,
                        failed_cases=1,
                        failures=["case failed"],
                    )
                ],
            )

        monkeypatch.setattr("agent_blueprint.cli.eval_cmd.run_eval_suites", fake_run_eval_suites)

        result = runner.invoke(app, ["eval", str(blueprint)])

        assert result.exit_code == 1
        assert "FAIL" in result.output
        assert "case failed" in result.output

    def test_threads_trace_store_by_default(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
evals:
  suites:
    - id: suite
      metric: exact_match
      dataset: datasets/cases.yaml
""",
        )
        seen_kwargs: list[dict] = []

        def fake_run_eval_suites(ir, suites, *, blueprint_dir, install, **kwargs):
            seen_kwargs.append(dict(kwargs))
            return EvalRunResult(
                blueprint=ir.name, blueprint_version=ir.version, passed=True, suites=[],
            )

        monkeypatch.setattr("agent_blueprint.cli.eval_cmd.run_eval_suites", fake_run_eval_suites)
        result = runner.invoke(app, ["eval", str(blueprint)])
        assert result.exit_code == 0
        assert seen_kwargs[0]["trace_store"] == tmp_path / ".abp" / "traces"
        assert seen_kwargs[0]["save_traces"] == "failed"

    def test_save_traces_none_disables_store(self, monkeypatch, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "test-agent"
graph:
  entry_point: n
  nodes:
    n:
      type: function
  edges: []
evals:
  suites:
    - id: suite
      metric: exact_match
      dataset: datasets/cases.yaml
""",
        )
        seen_kwargs: list[dict] = []

        def fake_run_eval_suites(ir, suites, *, blueprint_dir, install, **kwargs):
            seen_kwargs.append(dict(kwargs))
            return EvalRunResult(
                blueprint=ir.name, blueprint_version=ir.version, passed=True, suites=[],
            )

        monkeypatch.setattr("agent_blueprint.cli.eval_cmd.run_eval_suites", fake_run_eval_suites)
        result = runner.invoke(app, ["eval", str(blueprint), "--save-traces", "none"])
        assert result.exit_code == 0
        assert seen_kwargs[0]["trace_store"] is None
