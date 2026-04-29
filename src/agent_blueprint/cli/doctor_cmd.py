"""abp doctor - Pre-generation blueprint diagnostics."""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from agent_blueprint.cli.generate import TargetFramework
from agent_blueprint.doctoring import DoctorSeverity, doctor_blueprint
from agent_blueprint.exceptions import BlueprintCompilationError, BlueprintValidationError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def doctor(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    target: TargetFramework = typer.Option(
        TargetFramework.langgraph, "--target", "-t", help="Target framework to diagnose against"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output when no findings are reported"),
) -> None:
    """Run pre-generation diagnostics on a blueprint."""
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

    findings = doctor_blueprint(spec, ir, target=target)
    errors = [finding for finding in findings if finding.severity == DoctorSeverity.error]
    warnings = [finding for finding in findings if finding.severity == DoctorSeverity.warning]

    if findings:
        console.print(f"[bold]Doctor[/] — {blueprint.name} ({target.value})")
        for finding in findings:
            style = "red" if finding.severity == DoctorSeverity.error else "yellow"
            console.print(
                f"[{style}]{finding.severity.value.upper()}[/{style}] "
                f"{finding.code} at {finding.location}: {finding.message}"
            )
    elif not quiet:
        console.print(f"[green]No doctor findings[/] — {blueprint.name} ({target.value})")

    if not quiet or findings:
        console.print(f"[bold]Summary:[/] {len(errors)} error(s), {len(warnings)} warning(s)")

    if errors:
        raise typer.Exit(1)
