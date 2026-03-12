"""abp validate - Validate a blueprint YAML file."""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agent_blueprint.exceptions import BlueprintCompilationError, BlueprintValidationError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def validate(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output on success"),
) -> None:
    """Validate a blueprint YAML file against the schema."""
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(Panel(
            _format_validation_errors(e),
            title=f"[bold red]Validation failed[/] — {blueprint.name}",
            border_style="red",
        ))
        raise typer.Exit(1) from e

    try:
        ir = compile_blueprint(spec)
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    if ir.warnings and not quiet:
        for w in ir.warnings:
            console.print(f"[bold yellow]⚠  Warning:[/] {w}")

    if not quiet:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_row("[green]Blueprint[/]", spec.blueprint.name)
        table.add_row("[green]Version[/]", spec.blueprint.version)
        table.add_row("[green]Agents[/]", str(len(spec.agents)))
        table.add_row("[green]Tools[/]", str(len(spec.tools)))
        table.add_row("[green]Nodes[/]", str(len(spec.graph.nodes)))
        table.add_row("[green]Entry point[/]", spec.graph.entry_point)

        console.print(Panel(
            table,
            title=f"[bold green]Valid[/] — {blueprint.name}",
            border_style="green",
        ))


def _format_validation_errors(e: ValidationError) -> str:
    lines = []
    for err in e.errors():
        loc = " → ".join(str(p) for p in err["loc"])
        lines.append(f"  [yellow]{loc}[/]: {err['msg']}")
    return "\n".join(lines)
