"""Harness scenario runner for ABP workflows.

This module evaluates ABP harness semantics. The current execution
adapter uses the local LangGraph runner, but the harness contract is
intended to remain portable across future executable targets such as
CrewAI.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_blueprint.contracts_validation import validate_output_contract
from agent_blueprint.exceptions import ExpressionError
from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.ir.expression import parse_expression
from agent_blueprint.models.harness import HarnessFixtures, HarnessScenario
from agent_blueprint.runners.local import LocalRunResult, LocalRunner
from agent_blueprint.trace import diff_trace_manifests


@dataclass
class ScenarioResult:
    scenario_id: str
    passed: bool
    returncode: int
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    trace_manifest: dict[str, Any] | None = None


def resolve_harness_trace_mode(llm_mode: str, tool_mode: str) -> str:
    if llm_mode == "live" and tool_mode == "live":
        return "live"
    if llm_mode == "mock" and tool_mode == "stub":
        return "mock"
    if llm_mode == "replay" and tool_mode == "stub":
        return "stubbed"
    if llm_mode == "replay" and tool_mode == "live":
        return "live-tools"
    if llm_mode == "replay" and tool_mode == "replay":
        return "replay"
    return "live"


def scenario_user_input(ir: AgentGraph, scenario: HarnessScenario) -> str | None:
    if not scenario.input:
        return None
    if ir.input_schema:
        return json.dumps(scenario.input)
    for key in ("message", "input", "user_input"):
        value = scenario.input.get(key)
        if value is not None:
            return str(value)
    return json.dumps(scenario.input)


def parse_runner_output(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return text
    return text


def merge_harness_fixtures(defaults: HarnessFixtures, scenario: HarnessFixtures) -> dict[str, Any]:
    return {
        "llm_outputs": {
            **defaults.llm_outputs,
            **scenario.llm_outputs,
        },
        "tool_outputs": {
            **defaults.tool_outputs,
            **scenario.tool_outputs,
        },
    }


def extract_replay_fixtures(trace_manifest: dict[str, Any]) -> dict[str, Any]:
    replay = trace_manifest.get("replay", {})
    if not isinstance(replay, dict):
        return {"llm_outputs": {}, "tool_outputs": {}}
    llm_outputs = replay.get("llm_outputs", {})
    tool_outputs = replay.get("tool_outputs", {})
    if not isinstance(llm_outputs, dict) or not isinstance(tool_outputs, dict):
        return {"llm_outputs": {}, "tool_outputs": {}}
    return {
        "llm_outputs": llm_outputs,
        "tool_outputs": tool_outputs,
    }


def load_trace_manifest(path: str) -> dict[str, Any]:
    trace_path = Path(path)
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Replay trace must decode to an object: {trace_path}")
    return payload


def run_harness_scenario(
    ir: AgentGraph,
    scenario: HarnessScenario,
    *,
    install: bool,
    trace_store: Path | None = None,
    save_traces: str = "all",
    origin: str = "harness",
) -> ScenarioResult:
    llm_mode = (scenario.llm_mode or ir.harness.defaults.llm_mode).value  # type: ignore[union-attr]
    tool_mode = (scenario.tool_mode or ir.harness.defaults.tool_mode).value  # type: ignore[union-attr]
    seed = scenario.seed if scenario.seed is not None else ir.harness.defaults.seed  # type: ignore[union-attr]
    replay_trace = scenario.replay_trace or ir.harness.defaults.replay_trace  # type: ignore[union-attr]
    fixtures = merge_harness_fixtures(
        ir.harness.defaults.fixtures,  # type: ignore[union-attr]
        scenario.fixtures,
    )
    golden_trace = None
    if replay_trace:
        golden_trace = load_trace_manifest(replay_trace)
        replay_fixtures = extract_replay_fixtures(golden_trace)
        fixtures = {
            "llm_outputs": {
                **replay_fixtures["llm_outputs"],
                **fixtures["llm_outputs"],
            },
            "tool_outputs": {
                **replay_fixtures["tool_outputs"],
                **fixtures["tool_outputs"],
            },
        }

    runner = LocalRunner(ir, thread_id=scenario.id)
    execution_mode = resolve_harness_trace_mode(llm_mode, tool_mode)
    captured = runner.run_capture(
        user_input=scenario_user_input(ir, scenario),
        install=install,
        keep_temp=False,
        extra_env={
            "ABP_TRACE_MODE": execution_mode,
            "ABP_RUN_ID": scenario.id,
            "ABP_SCENARIO_ID": scenario.id,
            "ABP_LLM_MODE": llm_mode,
            "ABP_TOOL_MODE": tool_mode,
            "ABP_HARNESS_FIXTURES": json.dumps(fixtures, sort_keys=True),
            **({"ABP_TRACE_SEED": str(seed)} if seed is not None else {}),
        },
    )

    result = ScenarioResult(
        scenario_id=scenario.id,
        passed=captured.returncode == 0,
        returncode=captured.returncode,
        stdout=captured.stdout,
        stderr=captured.stderr,
        trace_manifest=captured.trace_manifest,
    )
    if (llm_mode != "live" or tool_mode != "live") and not (
        fixtures["llm_outputs"] or fixtures["tool_outputs"]
    ):
        result.warnings.append(
            "Harness execution modes are recorded in trace metadata, but live/mock/stub behavior "
            "is not fully emulated until mocked adapters land."
        )
    if captured.returncode != 0:
        result.failures.append(f"Scenario process exited with code {captured.returncode}")
        result.passed = False
    else:
        evaluate_scenario_expectations(ir, scenario, captured, result)
        if golden_trace is not None and captured.trace_manifest is not None:
            diff = diff_trace_manifests(golden_trace, captured.trace_manifest)
            if diff:
                result.failures.append("replay trace drift detected:\n" + diff)
            else:
                result.checks.append("replay_trace")
        result.passed = not result.failures

    if trace_store is not None and (save_traces == "all" or not result.passed):
        # Local import: trace_store imports ScenarioResult from this module.
        from agent_blueprint.trace_store import build_trace_record, save_trace_record

        record = build_trace_record(
            scenario_id=scenario.id,
            input=scenario.input,
            result=result,
            origin=origin,
        )
        save_trace_record(record, store_dir=trace_store)
    return result


def evaluate_scenario_expectations(
    ir: AgentGraph,
    scenario: HarnessScenario,
    captured: LocalRunResult,
    result: ScenarioResult,
) -> None:
    expected = scenario.expected
    manifest = captured.trace_manifest or {}
    events = manifest.get("trace", [])

    if expected.tools_called:
        actual_tools = [event.get("tool") for event in events if event.get("event") == "tool_called"]
        if actual_tools == expected.tools_called:
            result.checks.append("tools_called")
        else:
            result.failures.append(
                f"tools_called mismatch: expected {expected.tools_called}, got {actual_tools}"
            )

    if expected.approvals_triggered is not None:
        approvals = any(event.get("event") == "approval_requested" for event in events)
        if approvals == expected.approvals_triggered:
            result.checks.append("approvals_triggered")
        else:
            result.failures.append(
                f"approvals_triggered mismatch: expected {expected.approvals_triggered}, got {approvals}"
            )

    if expected.outputs:
        actual_output = parse_runner_output(captured.stdout)
        mismatches = []
        if not isinstance(actual_output, dict):
            result.failures.append(
                f"outputs expectation requires structured output, got {type(actual_output).__name__}"
            )
        else:
            for key, expected_value in expected.outputs.items():
                if actual_output.get(key) != expected_value:
                    mismatches.append((key, expected_value, actual_output.get(key)))
            if mismatches:
                rendered = ", ".join(
                    f"{key}: expected {want!r}, got {got!r}" for key, want, got in mismatches
                )
                result.failures.append(f"outputs mismatch: {rendered}")
            else:
                result.checks.append("outputs")

    if expected.route is not None:
        finished = [event for event in events if event.get("event") == "node_finished"]
        last_node = finished[-1].get("node") if finished else None
        escalated_to = any(event.get("route") == expected.route for event in finished)
        if last_node == expected.route or escalated_to:
            result.checks.append("route")
        else:
            result.failures.append(
                f"route mismatch: expected {expected.route!r}, ended on {last_node!r}"
            )

    if expected.output_contract is not None:
        contracts = ir.contracts.outputs if ir.contracts else {}
        contract = contracts.get(expected.output_contract)
        if contract is None:
            result.failures.append(
                f"output_contract references unknown contract '{expected.output_contract}'"
            )
        else:
            violations = validate_output_contract(parse_runner_output(captured.stdout), contract)
            if violations:
                result.failures.append("output_contract violations: " + "; ".join(violations))
            else:
                result.checks.append("output_contract")

    if expected.state_assertions:
        final_state = manifest.get("final_state")
        if not isinstance(final_state, dict):
            result.failures.append(
                "state_assertions require a final_state in the trace manifest "
                "(regenerate the project with the current abp version)"
            )
        else:
            all_passed = True
            for assertion in expected.state_assertions:
                try:
                    code = parse_expression(assertion).to_dict_access("state")
                    # Safe: the parser forbids calls/subscripts in the source
                    # expression, so the rendered code only reads dict fields.
                    passed = bool(eval(code, {"__builtins__": {}}, {"state": final_state}))  # noqa: S307
                except ExpressionError as exc:
                    result.failures.append(f"state assertion invalid: {exc}")
                    all_passed = False
                    continue
                except Exception as exc:  # noqa: BLE001 — report, don't crash the harness
                    result.failures.append(
                        f"state assertion {assertion!r} failed to evaluate: {exc}"
                    )
                    all_passed = False
                    continue
                if not passed:
                    result.failures.append(f"state assertion not satisfied: {assertion}")
                    all_passed = False
            if all_passed:
                result.checks.append("state_assertions")

    if expected.artifacts:
        written = {
            event.get("metadata", {}).get("artifact")
            for event in events
            if event.get("event") == "artifact_written"
        }
        missing = [name for name in expected.artifacts if name not in written]
        if missing:
            wrote = sorted(name for name in written if name)
            result.failures.append(f"artifacts not written: {missing} (wrote {wrote})")
        else:
            result.checks.append("artifacts")
