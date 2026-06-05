"""abp gate - Regression merge gate over harness scenarios and eval suites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from agent_blueprint.eval_runner import EvalRunResult, run_eval_suites
from agent_blueprint.exceptions import BlueprintCompilationError, BlueprintValidationError
from agent_blueprint.gating import (
    GATE_SCHEMA_VERSION,
    GateComparison,
    build_gate_snapshot,
    compare_gate_snapshots,
    current_run_all_green,
)
from agent_blueprint.harness_runner import ScenarioResult, run_harness_scenario
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)

DEFAULT_BASELINE_DIRNAME = ".abp"
DEFAULT_BASELINE_FILENAME = "gate-baseline.json"


def gate(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    baseline: Path | None = typer.Option(
        None,
        "--baseline",
        help="Baseline JSON path (default <blueprint_dir>/.abp/gate-baseline.json)",
    ),
    update_baseline: bool = typer.Option(
        False,
        "--update-baseline",
        help="Run everything and overwrite the baseline (only when the run is all green)",
    ),
    tolerance: float = typer.Option(
        0.0, "--tolerance", help="Allowed eval score drop before flagging a regression"
    ),
    json_stdout: bool = typer.Option(
        False, "--json", help="Print a machine-readable gate report to stdout"
    ),
    install: bool = typer.Option(
        False,
        "--install/--no-install",
        help="pip install dependencies before running scenarios and eval cases",
    ),
    save_traces: str = typer.Option(
        "failed",
        "--save-traces",
        help="Persist harness trace records to the trace store: failed|all|none",
    ),
    trace_dir: Path | None = typer.Option(
        None, "--trace-dir", help="Trace store dir (default <blueprint_dir>/.abp/traces)"
    ),
) -> None:
    """Run harness + evals and fail on regression against a stored baseline."""
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
        ir = compile_blueprint(spec)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(f"[bold red]Validation error:[/] {e}")
        raise typer.Exit(1) from e
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    scenarios = list(ir.harness.scenarios) if ir.harness else []
    suites = list(ir.evals.suites) if ir.evals else []
    if not scenarios and not suites:
        err_console.print(
            "[bold red]Gate error:[/] blueprint defines no harness scenarios and no eval "
            "suites; nothing to gate"
        )
        raise typer.Exit(1)

    if save_traces not in {"failed", "all", "none"}:
        err_console.print("[bold red]Gate error:[/] --save-traces must be failed, all, or none")
        raise typer.Exit(1)
    trace_store = (
        None if save_traces == "none" else (trace_dir or blueprint.parent / ".abp" / "traces")
    )

    harness_results: list[ScenarioResult] = [
        run_harness_scenario(
            ir, scenario, install=install, trace_store=trace_store, save_traces=save_traces
        )
        for scenario in scenarios
    ]
    eval_result: EvalRunResult | None = None
    if suites:
        try:
            eval_result = run_eval_suites(
                ir, suites, blueprint_dir=blueprint.parent, install=install
            )
        except BlueprintValidationError as e:
            err_console.print(f"[bold red]Gate error:[/] {e}")
            raise typer.Exit(1) from e

    current = build_gate_snapshot(
        blueprint=spec.blueprint.name,
        blueprint_version=ir.version,
        harness_results=harness_results,
        eval_result=eval_result,
    )
    all_green = current_run_all_green(current)
    baseline_path = baseline or (
        blueprint.parent / DEFAULT_BASELINE_DIRNAME / DEFAULT_BASELINE_FILENAME
    )

    if update_baseline:
        if not all_green:
            _render_run_failures(harness_results, eval_result)
            err_console.print(
                "[bold red]Gate error:[/] refusing to write a red baseline — "
                "fix the failing scenarios/suites first"
            )
            raise typer.Exit(1)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        console.print(f"[green]Gate baseline written to[/] {baseline_path}")
        raise typer.Exit(0)

    if not baseline_path.exists():
        err_console.print(
            f"[bold red]Gate error:[/] no gate baseline found at {baseline_path}; "
            f"run 'abp gate {blueprint} --update-baseline' to create one"
        )
        raise typer.Exit(1)

    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline_data.get("schema_version") != GATE_SCHEMA_VERSION:
        err_console.print(
            "[bold red]Gate error:[/] unsupported baseline schema_version "
            f"{baseline_data.get('schema_version')!r}; regenerate with --update-baseline"
        )
        raise typer.Exit(1)
    if baseline_data.get("blueprint") != current["blueprint"]:
        err_console.print(
            "[bold red]Gate error:[/] baseline belongs to blueprint "
            f"'{baseline_data.get('blueprint')}', current run is '{current['blueprint']}'; "
            "pass --baseline to point at the right file"
        )
        raise typer.Exit(1)

    comparison = compare_gate_snapshots(baseline_data, current, tolerance=tolerance)
    gate_passed = all_green and comparison.passed

    if json_stdout:
        report = {
            "blueprint": current["blueprint"],
            "passed": gate_passed,
            "all_green": all_green,
            "regressions": comparison.regressions,
            "improvements": comparison.improvements,
            "new_entries": comparison.new_entries,
            "current": current,
        }
        console.print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _render_gate_report(current, comparison, all_green, gate_passed, harness_results, eval_result)

    raise typer.Exit(0 if gate_passed else 1)


def _render_run_failures(
    harness_results: list[ScenarioResult],
    eval_result: EvalRunResult | None,
) -> None:
    for result in harness_results:
        if not result.passed:
            notes = "; ".join(result.failures) or f"exit code {result.returncode}"
            err_console.print(f"[red]FAIL[/] scenario '{result.scenario_id}': {notes}")
    if eval_result is not None:
        for suite in eval_result.suites:
            if not suite.passed:
                notes = "; ".join(suite.failures) or "failing cases"
                err_console.print(f"[red]FAIL[/] eval suite '{suite.suite_id}': {notes}")


def _render_gate_report(
    current: dict[str, Any],
    comparison: GateComparison,
    all_green: bool,
    gate_passed: bool,
    harness_results: list[ScenarioResult],
    eval_result: EvalRunResult | None,
) -> None:
    table = Table(title=f"Gate - {current['blueprint']}")
    table.add_column("Entry")
    table.add_column("Status")
    table.add_column("Score")

    for scenario_id, entry in sorted(current["harness"]["scenarios"].items()):
        status = "[green]PASS[/]" if entry["passed"] else "[red]FAIL[/]"
        table.add_row(f"scenario {scenario_id}", status, "-")
    for suite_id, entry in sorted(current["evals"]["suites"].items()):
        status = "[green]PASS[/]" if entry["passed"] else "[red]FAIL[/]"
        table.add_row(f"eval {suite_id}", status, f"{entry['score']:.3f}")
    console.print(table)

    for line in comparison.improvements:
        console.print(f"[green]IMPROVED[/] {line}")
    for line in comparison.new_entries:
        console.print(f"[cyan]NEW[/] {line}")

    if gate_passed:
        checks = len(current["harness"]["scenarios"]) + len(current["evals"]["suites"])
        console.print(f"[bold green]Gate PASSED[/] — {checks} checks, 0 regressions")
        return

    if not all_green:
        _render_run_failures(harness_results, eval_result)
    if comparison.regressions:
        err_console.print("[bold red]Regressions:[/]")
        for line in comparison.regressions:
            err_console.print(f"  - {line}")
    err_console.print("[bold red]Gate FAILED[/]")
