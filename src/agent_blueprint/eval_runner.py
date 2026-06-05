"""Dataset-driven eval runner for ABP workflows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from agent_blueprint.exceptions import BlueprintValidationError
from agent_blueprint.harness_runner import ScenarioResult, run_harness_scenario
from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.models.evals import EvalMetric, EvalSuiteDef
from agent_blueprint.models.harness import HarnessDef, HarnessExpected, HarnessFixtures, HarnessScenario
from agent_blueprint.utils.yaml_loader import yaml


class EvalCaseDef(BaseModel):
    id: str = Field(min_length=1)
    input: dict[str, Any] = Field(default_factory=dict)
    expected: HarnessExpected = Field(default_factory=HarnessExpected)
    llm_mode: str | None = None
    tool_mode: str | None = None
    seed: int | None = None
    replay_trace: str | None = None
    fixtures: HarnessFixtures = Field(default_factory=HarnessFixtures)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RubricConfig(BaseModel):
    artifact: str
    min_score: float = Field(default=1.0, ge=0.0, le=1.0)
    required_sections: list[str] = Field(default_factory=list)
    required_terms: list[str] = Field(default_factory=list)
    min_word_count: int = Field(default=0, ge=0)


@dataclass
class EvalCaseResult:
    case_id: str
    passed: bool
    score: float
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class EvalSuiteResult:
    suite_id: str
    metric: str
    dataset: str
    passed: bool
    score: float
    total: int
    passed_cases: int
    failed_cases: int
    cases: list[EvalCaseResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class EvalRunResult:
    blueprint: str
    blueprint_version: str
    passed: bool
    suites: list[EvalSuiteResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_eval_suites(
    ir: AgentGraph,
    suites: list[EvalSuiteDef],
    *,
    blueprint_dir: Path,
    install: bool,
    trace_store: Path | None = None,
    save_traces: str = "all",
) -> EvalRunResult:
    results = [
        run_eval_suite(
            ir,
            suite,
            blueprint_dir=blueprint_dir,
            install=install,
            trace_store=trace_store,
            save_traces=save_traces,
        )
        for suite in suites
    ]
    return EvalRunResult(
        blueprint=ir.name,
        blueprint_version=ir.version,
        passed=all(result.passed for result in results),
        suites=results,
    )


def run_eval_suite(
    ir: AgentGraph,
    suite: EvalSuiteDef,
    *,
    blueprint_dir: Path,
    install: bool,
    trace_store: Path | None = None,
    save_traces: str = "all",
) -> EvalSuiteResult:
    cases = load_eval_dataset(suite.dataset, blueprint_dir=blueprint_dir)
    effective_ir = replace(ir, harness=ir.harness or HarnessDef())
    case_results: list[EvalCaseResult] = []
    for case in cases:
        scenario = _case_to_scenario(case)
        scenario_result = run_harness_scenario(
            effective_ir,
            scenario,
            install=install,
            trace_store=trace_store,
            save_traces=save_traces,
            origin="eval",
        )
        case_results.append(_evaluate_case_result(suite, case, scenario_result))

    total = len(case_results)
    passed_cases = sum(1 for result in case_results if result.passed)
    failed_cases = total - passed_cases
    score = sum(result.score for result in case_results) / total if total else 0.0
    return EvalSuiteResult(
        suite_id=suite.id,
        metric=suite.metric.value,
        dataset=suite.dataset,
        passed=failed_cases == 0 and total > 0,
        score=score,
        total=total,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        cases=case_results,
        failures=[] if total else ["dataset contains no eval cases"],
    )


def load_eval_dataset(dataset: str, *, blueprint_dir: Path) -> list[EvalCaseDef]:
    dataset_path = Path(dataset)
    if not dataset_path.is_absolute():
        dataset_path = blueprint_dir / dataset_path
    if not dataset_path.exists():
        raise BlueprintValidationError(f"Eval dataset not found: {dataset_path}")

    if dataset_path.suffix == ".jsonl":
        records = [
            json.loads(line)
            for line in dataset_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    elif dataset_path.suffix == ".json":
        records = json.loads(dataset_path.read_text(encoding="utf-8"))
    elif dataset_path.suffix in {".yml", ".yaml"}:
        with dataset_path.open("r", encoding="utf-8") as f:
            records = yaml.load(f)
    else:
        raise BlueprintValidationError(
            f"Eval dataset must be .yaml, .yml, .json, or .jsonl: {dataset_path}"
        )

    if isinstance(records, dict):
        records = records.get("cases", records.get("eval_cases"))
    if not isinstance(records, list):
        raise BlueprintValidationError(f"Eval dataset must contain a list of cases: {dataset_path}")

    cases: list[EvalCaseDef] = []
    try:
        for record in records:
            cases.append(EvalCaseDef.model_validate(record))
    except ValidationError as e:
        raise BlueprintValidationError(f"Invalid eval dataset case in {dataset_path}: {e}") from e
    return cases


def _case_to_scenario(case: EvalCaseDef) -> HarnessScenario:
    return HarnessScenario.model_validate({
        "id": case.id,
        "input": case.input,
        "expected": case.expected.model_dump(),
        "llm_mode": case.llm_mode,
        "tool_mode": case.tool_mode,
        "seed": case.seed,
        "replay_trace": case.replay_trace,
        "fixtures": case.fixtures.model_dump(),
    })


def _evaluate_case_result(
    suite: EvalSuiteDef,
    case: EvalCaseDef,
    scenario_result: ScenarioResult,
) -> EvalCaseResult:
    failures = list(scenario_result.failures)
    checks = list(scenario_result.checks)
    score = 1.0 if scenario_result.passed and not failures else 0.0

    if suite.metric == EvalMetric.policy_violations:
        violation_count = 0
        if scenario_result.trace_manifest:
            violation_count = sum(
                1
                for event in scenario_result.trace_manifest.get("trace", [])
                if event.get("event") == "policy_violation"
            )
        if violation_count == 0:
            checks.append("policy_violations")
        else:
            failures.append(f"policy_violations mismatch: expected 0, got {violation_count}")
            score = 0.0

    if suite.metric == EvalMetric.rubric:
        rubric_result = _evaluate_artifact_rubric(suite, case, scenario_result)
        checks.extend(rubric_result.checks)
        failures.extend(rubric_result.failures)
        score = rubric_result.score

    passed = scenario_result.passed and not failures
    return EvalCaseResult(
        case_id=scenario_result.scenario_id,
        passed=passed,
        score=score,
        checks=checks,
        failures=failures,
        warnings=list(scenario_result.warnings),
    )


def _evaluate_artifact_rubric(
    suite: EvalSuiteDef,
    case: EvalCaseDef,
    scenario_result: ScenarioResult,
) -> EvalCaseResult:
    try:
        rubric = _rubric_for_case(suite, case)
    except ValueError as exc:
        return EvalCaseResult(
            case_id=scenario_result.scenario_id,
            passed=False,
            score=0.0,
            failures=[str(exc)],
        )

    artifact_path = _artifact_path_from_trace(scenario_result.trace_manifest, rubric.artifact)
    if artifact_path is None:
        return EvalCaseResult(
            case_id=scenario_result.scenario_id,
            passed=False,
            score=0.0,
            failures=[f"rubric artifact not found in trace: {rubric.artifact}"],
        )
    if not artifact_path.exists():
        return EvalCaseResult(
            case_id=scenario_result.scenario_id,
            passed=False,
            score=0.0,
            failures=[f"rubric artifact path does not exist: {artifact_path}"],
        )

    content = artifact_path.read_text(encoding="utf-8")
    checks: list[str] = []
    failures: list[str] = []
    total = 0
    passed = 0

    def record(ok: bool, name: str, failure: str) -> None:
        nonlocal total, passed
        total += 1
        if ok:
            passed += 1
            checks.append(name)
        else:
            failures.append(failure)

    lower_content = content.lower()
    for section in rubric.required_sections:
        record(
            section.lower() in lower_content,
            f"rubric.required_sections:{section}",
            f"missing required artifact section: {section}",
        )
    for term in rubric.required_terms:
        record(
            term.lower() in lower_content,
            f"rubric.required_terms:{term}",
            f"missing required artifact term: {term}",
        )
    if rubric.min_word_count:
        word_count = len(content.split())
        record(
            word_count >= rubric.min_word_count,
            "rubric.min_word_count",
            f"artifact word count {word_count} is below minimum {rubric.min_word_count}",
        )

    if total == 0:
        return EvalCaseResult(
            case_id=scenario_result.scenario_id,
            passed=False,
            score=0.0,
            failures=["rubric must define at least one scoring criterion"],
        )

    score = passed / total
    if score < rubric.min_score:
        failures.append(f"rubric score {score:.3f} is below minimum {rubric.min_score:.3f}")

    return EvalCaseResult(
        case_id=scenario_result.scenario_id,
        passed=not failures,
        score=score,
        checks=checks,
        failures=failures,
    )


def _rubric_for_case(suite: EvalSuiteDef, case: EvalCaseDef) -> RubricConfig:
    raw = case.metadata.get("rubric") or suite.metadata.get("rubric")
    if raw is None:
        raise ValueError("rubric eval case requires metadata.rubric or suite metadata.rubric")
    try:
        return RubricConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid rubric config: {exc}") from exc


def _artifact_path_from_trace(
    trace_manifest: dict[str, Any] | None,
    artifact_name: str,
) -> Path | None:
    if not trace_manifest:
        return None
    for event in trace_manifest.get("trace", []):
        if event.get("event") != "artifact_written":
            continue
        metadata = event.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        if metadata.get("artifact") == artifact_name and metadata.get("path"):
            return Path(str(metadata["path"]))
    return None
