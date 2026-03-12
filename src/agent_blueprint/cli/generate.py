"""abp generate - Generate framework code from a blueprint."""

from enum import Enum
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from agent_blueprint.exceptions import BlueprintValidationError
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


class TargetFramework(str, Enum):
    langgraph = "langgraph"
    crewai = "crewai"
    plain = "plain"


def generate(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    target: TargetFramework = typer.Option(
        TargetFramework.langgraph, "--target", "-t", help="Target framework"
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o", help="Output directory (default: ./<blueprint-name>)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be generated"),
) -> None:
    """Generate framework code from a blueprint YAML file."""
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(f"[bold red]Validation error:[/] {e}")
        raise typer.Exit(1) from e

    if output_dir is None:
        safe_name = spec.blueprint.name.replace(" ", "-").lower()
        output_dir = Path(f"{safe_name}-{target.value}")

    # Compile IR
    from agent_blueprint.ir.compiler import compile_blueprint
    from agent_blueprint.exceptions import BlueprintCompilationError

    try:
        ir = compile_blueprint(spec)
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    for w in ir.warnings:
        console.print(f"[bold yellow]⚠  Warning:[/] {w}")

    # Select generator
    if target == TargetFramework.langgraph:
        from agent_blueprint.generators.langgraph import LangGraphGenerator
        generator = LangGraphGenerator()
    elif target == TargetFramework.crewai:
        from agent_blueprint.generators.crewai import CrewAIGenerator
        generator = CrewAIGenerator()
    else:
        from agent_blueprint.generators.plain import PlainPythonGenerator
        generator = PlainPythonGenerator()

    from agent_blueprint.exceptions import GeneratorError

    try:
        files = generator.generate(ir)
    except GeneratorError as e:
        err_console.print(f"[bold red]Generation error:[/] {e}")
        raise typer.Exit(1) from e

    if dry_run:
        console.print(Panel(
            "\n".join(f"  [cyan]{f}[/]" for f in files.keys()),
            title=f"[bold yellow]Dry run[/] — would generate {len(files)} files",
            border_style="yellow",
        ))
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        file_path = output_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    console.print(Panel(
        "\n".join(f"  [green]{output_dir / f}[/]" for f in files.keys()),
        title=f"[bold green]Generated[/] — {spec.blueprint.name} ({target.value})",
        border_style="green",
    ))
    console.print(f"\nNext steps:")
    console.print(f"  cd {output_dir}")
    console.print(f"  pip install -r requirements.txt")
    console.print(f"  python main.py")
