"""Tests for gate snapshot construction and comparison logic."""

from agent_blueprint.eval_runner import EvalRunResult, EvalSuiteResult
from agent_blueprint.gating import (
    GATE_SCHEMA_VERSION,
    build_gate_snapshot,
    compare_gate_snapshots,
    current_run_all_green,
)
from agent_blueprint.harness_runner import ScenarioResult


def _scenario(scenario_id: str, passed: bool) -> ScenarioResult:
    return ScenarioResult(scenario_id=scenario_id, passed=passed, returncode=0)


def _eval_result(*suites: EvalSuiteResult) -> EvalRunResult:
    return EvalRunResult(
        blueprint="test",
        blueprint_version="1.0",
        passed=all(suite.passed for suite in suites),
        suites=list(suites),
    )


def _suite(suite_id: str, *, passed: bool, score: float) -> EvalSuiteResult:
    return EvalSuiteResult(
        suite_id=suite_id,
        metric="exact_match",
        dataset="cases.yaml",
        passed=passed,
        score=score,
        total=3,
        passed_cases=3 if passed else 1,
        failed_cases=0 if passed else 2,
    )


def _snapshot(scenarios: dict, suites: dict) -> dict:
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "blueprint": "test",
        "blueprint_version": "1.0",
        "harness": {"scenarios": scenarios},
        "evals": {"suites": suites},
    }


class TestBuildGateSnapshot:
    def test_snapshot_shape(self):
        snapshot = build_gate_snapshot(
            blueprint="test",
            blueprint_version="1.0",
            harness_results=[_scenario("happy", True), _scenario("edge", False)],
            eval_result=_eval_result(_suite("router", passed=True, score=1.0)),
        )
        assert snapshot["schema_version"] == GATE_SCHEMA_VERSION
        assert snapshot["blueprint"] == "test"
        assert snapshot["harness"]["scenarios"] == {
            "happy": {"passed": True},
            "edge": {"passed": False},
        }
        assert snapshot["evals"]["suites"]["router"] == {
            "passed": True,
            "score": 1.0,
            "total": 3,
            "passed_cases": 3,
        }

    def test_empty_surfaces_yield_empty_maps(self):
        snapshot = build_gate_snapshot(
            blueprint="test",
            blueprint_version="1.0",
            harness_results=[],
            eval_result=None,
        )
        assert snapshot["harness"]["scenarios"] == {}
        assert snapshot["evals"]["suites"] == {}


class TestCurrentRunAllGreen:
    def test_all_green(self):
        assert current_run_all_green(
            _snapshot({"a": {"passed": True}}, {"s": {"passed": True}})
        )

    def test_failing_scenario_not_green(self):
        assert not current_run_all_green(_snapshot({"a": {"passed": False}}, {}))

    def test_failing_suite_not_green(self):
        assert not current_run_all_green(_snapshot({}, {"s": {"passed": False}}))

    def test_empty_maps_compose_as_green(self):
        assert current_run_all_green(_snapshot({}, {}))


class TestCompareGateSnapshots:
    def test_identical_snapshots_pass(self):
        snap = _snapshot({"a": {"passed": True}}, {"s": {"passed": True, "score": 1.0}})
        comparison = compare_gate_snapshots(snap, snap, tolerance=0.0)
        assert comparison.passed is True
        assert comparison.regressions == []
        assert comparison.improvements == []
        assert comparison.new_entries == []

    def test_scenario_pass_to_fail_is_regression(self):
        comparison = compare_gate_snapshots(
            _snapshot({"a": {"passed": True}}, {}),
            _snapshot({"a": {"passed": False}}, {}),
            tolerance=0.0,
        )
        assert comparison.passed is False
        assert comparison.regressions == ["scenario 'a' regressed: passed -> failed"]

    def test_missing_scenario_is_regression(self):
        comparison = compare_gate_snapshots(
            _snapshot({"a": {"passed": True}}, {}),
            _snapshot({}, {}),
            tolerance=0.0,
        )
        assert comparison.regressions == ["scenario 'a' missing from current run"]

    def test_scenario_fail_to_pass_is_improvement(self):
        comparison = compare_gate_snapshots(
            _snapshot({"a": {"passed": False}}, {}),
            _snapshot({"a": {"passed": True}}, {}),
            tolerance=0.0,
        )
        assert comparison.passed is True
        assert comparison.improvements == ["scenario 'a' improved: failed -> passed"]

    def test_new_scenario_reported(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {}),
            _snapshot({"b": {"passed": True}}, {}),
            tolerance=0.0,
        )
        assert comparison.passed is True
        assert comparison.new_entries == ["scenario 'b' is new"]

    def test_suite_score_drop_is_regression(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {"s": {"passed": True, "score": 1.0}}),
            _snapshot({}, {"s": {"passed": True, "score": 0.8}}),
            tolerance=0.0,
        )
        assert comparison.passed is False
        assert "score dropped: 1.000 -> 0.800" in comparison.regressions[0]

    def test_tolerance_boundary_is_not_regression(self):
        # current == baseline - tolerance is allowed (strict <)
        comparison = compare_gate_snapshots(
            _snapshot({}, {"s": {"passed": True, "score": 1.0}}),
            _snapshot({}, {"s": {"passed": True, "score": 0.7}}),
            tolerance=0.3,
        )
        assert comparison.passed is True

    def test_suite_pass_to_fail_is_regression_even_with_tolerance(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {"s": {"passed": True, "score": 1.0}}),
            _snapshot({}, {"s": {"passed": False, "score": 1.0}}),
            tolerance=1.0,
        )
        assert comparison.passed is False
        assert comparison.regressions == ["eval suite 's' regressed: passed -> failed"]

    def test_missing_suite_is_regression(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {"s": {"passed": True, "score": 1.0}}),
            _snapshot({}, {}),
            tolerance=0.0,
        )
        assert comparison.regressions == ["eval suite 's' missing from current run"]

    def test_new_suite_reported(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {}),
            _snapshot({}, {"s": {"passed": True, "score": 1.0}}),
            tolerance=0.0,
        )
        assert comparison.new_entries == ["eval suite 's' is new"]

    def test_suite_score_improvement_reported(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {"s": {"passed": True, "score": 0.5}}),
            _snapshot({}, {"s": {"passed": True, "score": 0.9}}),
            tolerance=0.0,
        )
        assert comparison.improvements == [
            "eval suite 's' score improved: 0.500 -> 0.900"
        ]

    def test_fail_and_drop_yield_two_regressions(self):
        comparison = compare_gate_snapshots(
            _snapshot({}, {"s": {"passed": True, "score": 1.0}}),
            _snapshot({}, {"s": {"passed": False, "score": 0.4}}),
            tolerance=0.0,
        )
        assert len(comparison.regressions) == 2
