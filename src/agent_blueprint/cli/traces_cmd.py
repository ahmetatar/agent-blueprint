"""abp traces - Inspect and export persisted harness trace records."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agent_blueprint.trace_store import export_records_to_dataset, list_trace_records

console = Console()
err_console = Console(stderr=True)

traces_app = typer.Typer(
    help="Inspect and export persisted harness trace records (.abp/traces)",
    no_args_is_help=True,
)

_STATUS_CHOICES = {"failed", "passed", "all"}
_ORIGIN_CHOICES = {"harness", "eval", "all"}


def _validated_status(status: str) -> str:
    if status not in _STATUS_CHOICES:
        err_console.print("[bold red]Traces error:[/] --status must be failed, passed, or all")
        raise typer.Exit(1)
    return status


def _validated_origin(origin: str) -> str:
    if origin not in _ORIGIN_CHOICES:
        err_console.print("[bold red]Traces error:[/] --origin must be harness, eval, or all")
        raise typer.Exit(1)
    return origin


@traces_app.command("list")
def list_(
    dir: Path = typer.Option(
        Path(".abp/traces"), "--dir", help="Trace store directory to scan"
    ),
    status: str = typer.Option("all", "--status", help="Filter by status: failed|passed|all"),
    blueprint: str | None = typer.Option(None, "--blueprint", help="Filter by blueprint name"),
    origin: str = typer.Option("all", "--origin", help="Filter by origin: harness|eval|all"),
) -> None:
    """List persisted trace records."""
    records = list_trace_records(
        store_dir=dir,
        status=_validated_status(status),
        blueprint=blueprint,
        origin=_validated_origin(origin),
    )
    if not records:
        console.print(f"No trace records found in {dir}")
        return

    table = Table(title=f"Trace records - {dir}")
    table.add_column("Scenario")
    table.add_column("Blueprint")
    table.add_column("Status")
    table.add_column("Origin")
    table.add_column("Saved at")
    table.add_column("Failures")

    for record in records:
        record_status = str(record.get("status", "-"))
        style = "[red]FAILED[/]" if record_status == "failed" else "[green]PASSED[/]"
        failures = record.get("failures", [])
        summary = "; ".join(str(item) for item in failures)
        if len(summary) > 60:
            summary = summary[:57] + "..."
        table.add_row(
            str(record.get("scenario_id", "-")),
            str(record.get("blueprint", "-")),
            style,
            str(record.get("origin", "harness")),
            str(record.get("saved_at", "-")),
            summary or "-",
        )
    console.print(table)
    console.print(f"{len(records)} record(s)")


@traces_app.command("export")
def export(
    output: Path = typer.Option(..., "--output", "-o", help="Eval dataset file to write/merge"),
    status: str = typer.Option("failed", "--status", help="Export records with this status"),
    dir: Path = typer.Option(
        Path(".abp/traces"), "--dir", help="Trace store directory to scan"
    ),
    blueprint: str | None = typer.Option(None, "--blueprint", help="Filter by blueprint name"),
    golden: bool = typer.Option(
        False,
        "--golden",
        help="Fill expected (route, tools_called) from each trace to lock in current behavior",
    ),
    origin: str = typer.Option(
        "harness",
        "--origin",
        help="Export records with this origin: harness|eval|all "
        "(default harness — eval-origin records are skipped to avoid re-exporting "
        "already-exported cases)",
    ),
) -> None:
    """Export trace records as eval dataset cases (merge-with-dedup, idempotent)."""
    records = list_trace_records(
        store_dir=dir,
        status=_validated_status(status),
        blueprint=blueprint,
        origin=_validated_origin(origin),
    )
    if not records:
        console.print(f"No matching trace records in {dir}; nothing to export")
        return

    added, skipped = export_records_to_dataset(records, output=output, golden=golden)
    console.print(
        f"[green]Exported[/] {added} new case(s) to {output} "
        f"({skipped} already present)"
    )
