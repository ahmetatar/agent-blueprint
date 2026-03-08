"""abp inspect - Visualize the agent graph."""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from agent_blueprint.exceptions import BlueprintValidationError
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.graph_viz import to_mermaid
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def inspect(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    format: str = typer.Option("mermaid", "--format", "-f", help="Output format: mermaid"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to file"),
) -> None:
    """Visualize the agent graph as a Mermaid diagram."""
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except (BlueprintValidationError, ValidationError) as e:
        err_console.print(f"[bold red]Error:[/] {e}")
        raise typer.Exit(1) from e

    diagram = to_mermaid(spec)

    if output:
        output.write_text(diagram, encoding="utf-8")
        console.print(f"[green]Mermaid diagram written to[/] {output}")
    else:
        console.print(f"\n[bold cyan]Graph[/] — {spec.blueprint.name}\n")
        console.print(Syntax(diagram, "text", theme="monokai"))
        console.print(
            "\n[dim]Paste the above into https://mermaid.live to visualize[/]"
        )
