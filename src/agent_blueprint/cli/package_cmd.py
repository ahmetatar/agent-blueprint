"""abp package — package a blueprint as an installable CLI distribution."""

from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from agent_blueprint.cli.generate import TargetFramework
from agent_blueprint.exceptions import BlueprintValidationError
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def package(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    target: TargetFramework = typer.Option(
        TargetFramework.langgraph, "--target", "-t",
        help="Target framework (only langgraph supported)",
    ),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o", help="Output directory (default: ./<blueprint-name>-cli)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be packaged"),
) -> None:
    """Package a blueprint as a pip/pipx-installable command-line tool.

    Generates the agent, restructures it into a src-layout Python package
    with a console-script entry point, and writes a pyproject.toml — so the
    agent installs as a command named after the blueprint.
    """
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(f"[bold red]Validation error:[/] {e}")
        raise typer.Exit(1) from e

    if target != TargetFramework.langgraph:
        err_console.print(
            "[bold red]abp package[/] only supports the [cyan]langgraph[/] target for now."
        )
        raise typer.Exit(1)

    from agent_blueprint.exceptions import BlueprintCompilationError, GeneratorError
    from agent_blueprint.ir.compiler import compile_blueprint

    try:
        ir = compile_blueprint(spec)
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    for w in ir.warnings:
        console.print(f"[bold yellow]⚠  Warning:[/] {w}")

    from agent_blueprint.generators.langgraph import LangGraphGenerator
    from agent_blueprint.packagers.cli import CliPackager, dist_name_for

    # Collect user `impl` modules that live next to the blueprint (e.g. a
    # function-tool impl `farm_impl.py`) so the packaged CLI can resolve them.
    from agent_blueprint.models.tools import ToolType

    impl_paths = [t.impl for t in spec.tools.values() if t.type == ToolType.function and t.impl]
    impl_paths += [r.impl for r in spec.retrievers.values() if r.impl]
    user_modules: dict[str, str] = {}
    for impl_path in impl_paths:
        root = impl_path.split(".")[0]
        if root in user_modules:
            continue
        candidate = blueprint.parent / f"{root}.py"
        if candidate.exists():
            user_modules[root] = candidate.read_text(encoding="utf-8")

    try:
        files = LangGraphGenerator().generate(ir)
        packaged = CliPackager().package(files, ir, user_modules=user_modules)
    except GeneratorError as e:
        err_console.print(f"[bold red]Generation error:[/] {e}")
        raise typer.Exit(1) from e

    dist_name = dist_name_for(ir.name)
    if output_dir is None:
        output_dir = Path(f"{dist_name}-cli")

    if dry_run:
        console.print(Panel(
            "\n".join(f"  [cyan]{f}[/]" for f in sorted(packaged)),
            title=f"[bold yellow]Dry run[/] — would package {len(packaged)} files",
            border_style="yellow",
        ))
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in packaged.items():
        file_path = output_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    console.print(Panel(
        "\n".join(f"  [green]{output_dir / f}[/]" for f in sorted(packaged)),
        title=f"[bold green]Packaged[/] — {spec.blueprint.name} (CLI)",
        border_style="green",
    ))
    install_path = str(output_dir) if output_dir.is_absolute() else f"./{output_dir}"
    console.print("\nNext steps:")
    console.print(f"  pipx install {install_path}        # or: pip install {install_path}")
    console.print(f'  {dist_name} "Hello"                # single-shot')
    console.print(f"  {dist_name}                        # interactive mode")
