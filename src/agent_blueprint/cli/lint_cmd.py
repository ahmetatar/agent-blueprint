"""abp lint - Static blueprint lint checks."""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from agent_blueprint.exceptions import BlueprintCompilationError, BlueprintValidationError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import LintSeverity, apply_auto_fixes, lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def lint(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output when no findings are reported"),
    auto_fix: bool = typer.Option(False, "--auto-fix", help="Apply safe automatic fixes before reporting final lint results"),
) -> None:
    """Run static lint checks on a blueprint."""
    spec, ir = _load_spec_and_ir(blueprint)
    findings = lint_blueprint(spec, ir)

    if auto_fix and findings:
        applied = apply_auto_fixes(blueprint, findings)
        if applied and not quiet:
            console.print(f"[green]Applied {len(applied)} auto-fix(es)[/] — {blueprint.name}")
            for item in applied:
                console.print(f"[green]FIXED[/] {item}")
        spec, ir = _load_spec_and_ir(blueprint)
        findings = lint_blueprint(spec, ir)

    _render_findings(blueprint, findings, quiet=quiet)

    errors = [finding for finding in findings if finding.severity == LintSeverity.error]
    if errors:
        raise typer.Exit(1)


def _load_spec_and_ir(blueprint: Path) -> tuple[BlueprintSpec, object]:
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
    return spec, ir


def _render_findings(blueprint: Path, findings: list, *, quiet: bool) -> None:
    errors = [finding for finding in findings if finding.severity == LintSeverity.error]
    warnings = [finding for finding in findings if finding.severity == LintSeverity.warning]

    if findings:
        console.print(f"[bold]Lint[/] — {blueprint.name}")
        for finding in findings:
            style = "red" if finding.severity == LintSeverity.error else "yellow"
            fix_label = " (auto-fixable)" if finding.autofixable else ""
            console.print(
                f"[{style}]{finding.severity.value.upper()}[/{style}] "
                f"{finding.code} "
                f"at {finding.location}: {finding.message}{fix_label}"
            )
    elif not quiet:
        console.print(f"[green]No lint findings[/] — {blueprint.name}")

    if not quiet or findings:
        console.print(
            f"[bold]Summary:[/] {len(errors)} error(s), {len(warnings)} warning(s)"
        )
