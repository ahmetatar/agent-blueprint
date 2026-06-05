"""Gate snapshot construction and regression comparison.

`abp gate` runs harness scenarios + eval suites, condenses the results into a
small deterministic snapshot, and compares it against a baseline committed to
the repository. Only aggregates are stored — per-case details, stdout, and
trace manifests are deliberately excluded to keep baseline diffs stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_blueprint.eval_runner import EvalRunResult
from agent_blueprint.harness_runner import ScenarioResult

GATE_SCHEMA_VERSION = "1"


@dataclass
class GateComparison:
    passed: bool
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    new_entries: list[str] = field(default_factory=list)


def build_gate_snapshot(
    *,
    blueprint: str,
    blueprint_version: str,
    harness_results: list[ScenarioResult],
    eval_result: EvalRunResult | None,
) -> dict[str, Any]:
    """Condense run results into the baseline snapshot schema (version 1)."""
    scenarios = {
        result.scenario_id: {"passed": result.passed}
        for result in harness_results
    }
    suites: dict[str, Any] = {}
    if eval_result is not None:
        suites = {
            suite.suite_id: {
                "passed": suite.passed,
                "score": suite.score,
                "total": suite.total,
                "passed_cases": suite.passed_cases,
            }
            for suite in eval_result.suites
        }
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "blueprint": blueprint,
        "blueprint_version": blueprint_version,
        "harness": {"scenarios": scenarios},
        "evals": {"suites": suites},
    }


def current_run_all_green(snapshot: dict[str, Any]) -> bool:
    """True when every scenario and suite in the snapshot passed.

    Empty maps count as green so the check composes; the CLI separately
    rejects blueprints with nothing to gate.
    """
    scenarios = snapshot.get("harness", {}).get("scenarios", {})
    suites = snapshot.get("evals", {}).get("suites", {})
    scenarios_green = all(entry.get("passed") for entry in scenarios.values())
    suites_green = all(entry.get("passed") for entry in suites.values())
    return scenarios_green and suites_green


def compare_gate_snapshots(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    tolerance: float,
) -> GateComparison:
    """Diff a current snapshot against the baseline.

    Regressions: baselined scenario now failing or missing; baselined suite
    now failing, missing, or with `score < baseline - tolerance` (strict).
    New entries never regress by themselves — the CLI's all-green rule
    requires them to pass on their own.
    """
    regressions: list[str] = []
    improvements: list[str] = []
    new_entries: list[str] = []

    base_scenarios = baseline.get("harness", {}).get("scenarios", {})
    cur_scenarios = current.get("harness", {}).get("scenarios", {})
    for scenario_id, base_entry in sorted(base_scenarios.items()):
        cur_entry = cur_scenarios.get(scenario_id)
        if cur_entry is None:
            regressions.append(f"scenario '{scenario_id}' missing from current run")
        elif base_entry.get("passed") and not cur_entry.get("passed"):
            regressions.append(f"scenario '{scenario_id}' regressed: passed -> failed")
        elif not base_entry.get("passed") and cur_entry.get("passed"):
            improvements.append(f"scenario '{scenario_id}' improved: failed -> passed")
    for scenario_id in sorted(cur_scenarios):
        if scenario_id not in base_scenarios:
            new_entries.append(f"scenario '{scenario_id}' is new")

    base_suites = baseline.get("evals", {}).get("suites", {})
    cur_suites = current.get("evals", {}).get("suites", {})
    for suite_id, base_entry in sorted(base_suites.items()):
        cur_entry = cur_suites.get(suite_id)
        if cur_entry is None:
            regressions.append(f"eval suite '{suite_id}' missing from current run")
            continue
        if base_entry.get("passed") and not cur_entry.get("passed"):
            regressions.append(f"eval suite '{suite_id}' regressed: passed -> failed")
        base_score = float(base_entry.get("score", 0.0))
        cur_score = float(cur_entry.get("score", 0.0))
        if cur_score < base_score - tolerance:
            regressions.append(
                f"eval suite '{suite_id}' score dropped: "
                f"{base_score:.3f} -> {cur_score:.3f} (tolerance {tolerance:.3f})"
            )
        elif cur_score > base_score:
            improvements.append(
                f"eval suite '{suite_id}' score improved: {base_score:.3f} -> {cur_score:.3f}"
            )
    for suite_id in sorted(cur_suites):
        if suite_id not in base_suites:
            new_entries.append(f"eval suite '{suite_id}' is new")

    return GateComparison(
        passed=not regressions,
        regressions=regressions,
        improvements=improvements,
        new_entries=new_entries,
    )
