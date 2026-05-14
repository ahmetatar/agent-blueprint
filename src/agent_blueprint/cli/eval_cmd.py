"""abp eval - Run dataset-driven eval suites."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from agent_blueprint.eval_runner import run_eval_suites
from agent_blueprint.exceptions import BlueprintCompilationError, BlueprintValidationError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def eval_(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    suite: str | None = typer.Option(None, "--suite", help="Run a single eval suite by ID"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write machine-readable JSON results"),
    json_stdout: bool = typer.Option(False, "--json", help="Print machine-readable JSON results to stdout"),
    install: bool = typer.Option(
        False, "--install/--no-install", help="pip install dependencies before running eval cases"
    ),
) -> None:
    """Run eval suites defined for a blueprint."""
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

    if ir.evals is None or not ir.evals.suites:
        err_console.print("[bold red]Eval error:[/] no eval suites are defined for this blueprint")
        raise typer.Exit(1)

    suites = ir.evals.suites
    if suite is not None:
        suites = [item for item in suites if item.id == suite]
        if not suites:
            err_console.print(f"[bold red]Eval error:[/] suite '{suite}' was not found")
            raise typer.Exit(1)

    try:
        result = run_eval_suites(ir, suites, blueprint_dir=blueprint.parent, install=install)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Eval error:[/] {e}")
        raise typer.Exit(1) from e

    payload = result.to_dict()
    if output is not None:
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if json_stdout:
        console.print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _render_eval_results(spec.blueprint.name, payload)

    if not result.passed:
        raise typer.Exit(1)


def _render_eval_results(blueprint_name: str, payload: dict[str, object]) -> None:
    table = Table(title=f"Eval Results - {blueprint_name}")
    table.add_column("Suite")
    table.add_column("Metric")
    table.add_column("Status")
    table.add_column("Score")
    table.add_column("Cases")
    table.add_column("Notes")

    failed = 0
    suites = payload.get("suites", [])
    if not isinstance(suites, list):
        suites = []
    for suite_result in suites:
        if not isinstance(suite_result, dict):
            continue
        passed = bool(suite_result.get("passed"))
        if passed:
            status = "[green]PASS[/]"
            notes = "-"
        else:
            failed += 1
            status = "[red]FAIL[/]"
            failures = suite_result.get("failures", [])
            notes = "; ".join(str(item) for item in failures) if isinstance(failures, list) else "-"
        table.add_row(
            str(suite_result.get("suite_id", "-")),
            str(suite_result.get("metric", "-")),
            status,
            f"{float(suite_result.get('score', 0.0)):.3f}",
            f"{suite_result.get('passed_cases', 0)}/{suite_result.get('total', 0)}",
            notes,
        )

    console.print(table)
    total = len(suites)
    summary = f"{total - failed} passed, {failed} failed"
    if failed:
        err_console.print(f"[bold red]{summary}[/]")
    else:
        console.print(f"[bold green]{summary}[/]")
