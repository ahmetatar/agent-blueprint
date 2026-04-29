"""abp fix - Apply safe blueprint auto-fixes explicitly."""

from pathlib import Path

import typer

from agent_blueprint.cli.lint_cmd import _load_spec_and_ir, _render_findings, console
from agent_blueprint.linting import LintSeverity, apply_auto_fixes, lint_blueprint


def fix(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress output when no fixes are applied and no findings remain"),
) -> None:
    """Apply safe lint auto-fixes to a blueprint."""
    spec, ir = _load_spec_and_ir(blueprint)
    findings = lint_blueprint(spec, ir)
    applied = apply_auto_fixes(blueprint, findings)

    if applied and not quiet:
        console.print(f"[green]Applied {len(applied)} auto-fix(es)[/] — {blueprint.name}")
        for item in applied:
            console.print(f"[green]FIXED[/] {item}")
    elif not quiet:
        console.print(f"[yellow]No auto-fixable findings[/] — {blueprint.name}")

    spec, ir = _load_spec_and_ir(blueprint)
    findings = lint_blueprint(spec, ir)
    _render_findings(blueprint, findings, quiet=quiet)

    errors = [finding for finding in findings if finding.severity == LintSeverity.error]
    if errors:
        raise typer.Exit(1)
