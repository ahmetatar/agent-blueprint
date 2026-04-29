"""abp test — execute ABP harness scenarios for a blueprint.

The command surface is ABP-level and target-agnostic by design.
The current executor is LangGraph-backed because that is the only
runtime target with full local execution support today.
"""

from __future__ import annotations

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from agent_blueprint.exceptions import BlueprintCompilationError, BlueprintValidationError
from agent_blueprint.harness_runner import run_harness_scenario
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def test(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    scenario: str | None = typer.Option(None, "--scenario", help="Run a single scenario by ID"),
    install: bool = typer.Option(
        False, "--install/--no-install", help="pip install dependencies before running scenarios"
    ),
) -> None:
    """Run harness scenarios defined for a blueprint."""
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(f"[bold red]Validation error:[/] {e}")
        raise typer.Exit(1) from e

    try:
        ir = compile_blueprint(spec)
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    if ir.harness is None or not ir.harness.scenarios:
        err_console.print("[bold red]Harness error:[/] no harness scenarios are defined for this blueprint")
        raise typer.Exit(1)

    scenarios = ir.harness.scenarios
    if scenario is not None:
        scenarios = [item for item in scenarios if item.id == scenario]
        if not scenarios:
            err_console.print(f"[bold red]Harness error:[/] scenario '{scenario}' was not found")
            raise typer.Exit(1)

    results = [run_harness_scenario(ir, item, install=install) for item in scenarios]

    table = Table(title=f"Harness Results — {spec.blueprint.name}")
    table.add_column("Scenario")
    table.add_column("Status")
    table.add_column("Checks")
    table.add_column("Notes")

    failed = 0
    for item in results:
        if item.passed:
            status = "[green]PASS[/]"
            checks = ", ".join(item.checks) if item.checks else "-"
            notes = ", ".join(item.warnings) if item.warnings else "-"
        else:
            failed += 1
            status = "[red]FAIL[/]"
            checks = ", ".join(item.checks) if item.checks else "-"
            notes = "; ".join(item.failures + item.warnings)
        table.add_row(item.scenario_id, status, checks, notes)

    console.print(table)
    summary = f"{len(results) - failed} passed, {failed} failed"
    if failed:
        err_console.print(f"[bold red]{summary}[/]")
        raise typer.Exit(1)
    console.print(f"[bold green]{summary}[/]")
