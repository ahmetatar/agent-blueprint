"""Tests for dataset-driven eval execution."""

from pathlib import Path

from agent_blueprint.eval_runner import load_eval_dataset, run_eval_suite
from agent_blueprint.harness_runner import ScenarioResult
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def test_load_eval_dataset_accepts_yaml_cases(tmp_path: Path):
    dataset = tmp_path / "router_cases.yaml"
    dataset.write_text(
        """\
cases:
  - id: billing
    input:
      message: "refund"
    expected:
      outputs:
        route: billing
""",
        encoding="utf-8",
    )

    cases = load_eval_dataset("router_cases.yaml", blueprint_dir=tmp_path)

    assert len(cases) == 1
    assert cases[0].id == "billing"
    assert cases[0].expected.outputs == {"route": "billing"}


def test_exact_match_eval_suite_aggregates_case_results(monkeypatch, tmp_path: Path):
    dataset = tmp_path / "router_cases.yaml"
    dataset.write_text(
        """\
cases:
  - id: pass_case
    input: {}
    expected: {}
  - id: fail_case
    input: {}
    expected: {}
""",
        encoding="utf-8",
    )
    spec = BlueprintSpec.model_validate({
        "blueprint": {"name": "eval-test"},
        "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
        "evals": {
            "suites": [
                {
                    "id": "router_accuracy",
                    "metric": "exact_match",
                    "dataset": "router_cases.yaml",
                }
            ]
        },
    })
    ir = compile_blueprint(spec)

    def fake_run_harness_scenario(ir, scenario, *, install):
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=scenario.id == "pass_case",
            returncode=0 if scenario.id == "pass_case" else 1,
            checks=["outputs"] if scenario.id == "pass_case" else [],
            failures=[] if scenario.id == "pass_case" else ["outputs mismatch"],
        )

    monkeypatch.setattr(
        "agent_blueprint.eval_runner.run_harness_scenario",
        fake_run_harness_scenario,
    )

    result = run_eval_suite(
        ir,
        ir.evals.suites[0],  # type: ignore[union-attr]
        blueprint_dir=tmp_path,
        install=False,
    )

    assert result.passed is False
    assert result.total == 2
    assert result.passed_cases == 1
    assert result.failed_cases == 1
    assert result.score == 0.5


def test_policy_violations_metric_counts_trace_events(monkeypatch, tmp_path: Path):
    dataset = tmp_path / "policy_cases.jsonl"
    dataset.write_text('{"id":"case_one","input":{},"expected":{}}\n', encoding="utf-8")
    spec = BlueprintSpec.model_validate({
        "blueprint": {"name": "eval-test"},
        "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
        "evals": {
            "suites": [
                {
                    "id": "policy",
                    "metric": "policy_violations",
                    "dataset": "policy_cases.jsonl",
                }
            ]
        },
    })
    ir = compile_blueprint(spec)

    def fake_run_harness_scenario(ir, scenario, *, install):
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=True,
            returncode=0,
            trace_manifest={"trace": [{"event": "policy_violation"}]},
        )

    monkeypatch.setattr(
        "agent_blueprint.eval_runner.run_harness_scenario",
        fake_run_harness_scenario,
    )

    result = run_eval_suite(
        ir,
        ir.evals.suites[0],  # type: ignore[union-attr]
        blueprint_dir=tmp_path,
        install=False,
    )

    assert result.passed is False
    assert result.cases[0].failures == ["policy_violations mismatch: expected 0, got 1"]


def test_rubric_metric_scores_artifact_quality(monkeypatch, tmp_path: Path):
    dataset = tmp_path / "prd_cases.yaml"
    artifact = tmp_path / "artifacts" / "prd.md"
    artifact.parent.mkdir()
    artifact.write_text(
        """\
# Problem
Users cannot compare refund options.

# Success Metrics
Reduce support escalations and improve conversion.
""",
        encoding="utf-8",
    )
    dataset.write_text(
        """\
cases:
  - id: prd_case
    input: {}
    expected: {}
    metadata:
      rubric:
        artifact: prd_doc
        min_score: 0.75
        required_sections: [Problem, Success Metrics]
        required_terms: [refund]
        min_word_count: 8
""",
        encoding="utf-8",
    )
    spec = BlueprintSpec.model_validate({
        "blueprint": {"name": "eval-test"},
        "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
        "evals": {
            "suites": [
                {
                    "id": "prd_quality",
                    "metric": "rubric",
                    "dataset": "prd_cases.yaml",
                }
            ]
        },
    })
    ir = compile_blueprint(spec)

    def fake_run_harness_scenario(ir, scenario, *, install):
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=True,
            returncode=0,
            trace_manifest={
                "trace": [
                    {
                        "event": "artifact_written",
                        "metadata": {"artifact": "prd_doc", "path": str(artifact)},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "agent_blueprint.eval_runner.run_harness_scenario",
        fake_run_harness_scenario,
    )

    result = run_eval_suite(
        ir,
        ir.evals.suites[0],  # type: ignore[union-attr]
        blueprint_dir=tmp_path,
        install=False,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.cases[0].checks == [
        "rubric.required_sections:Problem",
        "rubric.required_sections:Success Metrics",
        "rubric.required_terms:refund",
        "rubric.min_word_count",
    ]


def test_rubric_metric_fails_below_min_score(monkeypatch, tmp_path: Path):
    dataset = tmp_path / "prd_cases.yaml"
    artifact = tmp_path / "artifacts" / "prd.md"
    artifact.parent.mkdir()
    artifact.write_text("# Problem\nUsers need help.\n", encoding="utf-8")
    dataset.write_text(
        """\
cases:
  - id: prd_case
    input: {}
    expected: {}
metadata:
  ignored: true
""",
        encoding="utf-8",
    )
    spec = BlueprintSpec.model_validate({
        "blueprint": {"name": "eval-test"},
        "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
        "evals": {
            "suites": [
                {
                    "id": "prd_quality",
                    "metric": "rubric",
                    "dataset": "prd_cases.yaml",
                    "metadata": {
                        "rubric": {
                            "artifact": "prd_doc",
                            "min_score": 1.0,
                            "required_sections": ["Problem", "Success Metrics"],
                        }
                    },
                }
            ]
        },
    })
    ir = compile_blueprint(spec)

    def fake_run_harness_scenario(ir, scenario, *, install):
        return ScenarioResult(
            scenario_id=scenario.id,
            passed=True,
            returncode=0,
            trace_manifest={
                "trace": [
                    {
                        "event": "artifact_written",
                        "metadata": {"artifact": "prd_doc", "path": str(artifact)},
                    }
                ]
            },
        )

    monkeypatch.setattr(
        "agent_blueprint.eval_runner.run_harness_scenario",
        fake_run_harness_scenario,
    )

    result = run_eval_suite(
        ir,
        ir.evals.suites[0],  # type: ignore[union-attr]
        blueprint_dir=tmp_path,
        install=False,
    )

    assert result.passed is False
    assert result.score == 0.5
    assert "missing required artifact section: Success Metrics" in result.cases[0].failures
    assert "rubric score 0.500 is below minimum 1.000" in result.cases[0].failures
