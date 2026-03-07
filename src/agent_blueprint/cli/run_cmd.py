"""abp run — generate to a temp dir and execute locally."""

from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from rich.console import Console

from agent_blueprint.exceptions import BlueprintValidationError, GeneratorError
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)


def run(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    input: Optional[str] = typer.Argument(
        None, help="Input message (omit for interactive REPL)"
    ),
    target: str = typer.Option(
        "langgraph", "--target", "-t", help="Target framework (only langgraph supported)"
    ),
    thread_id: str = typer.Option(
        "default", "--thread-id", help="Conversation thread ID"
    ),
    install: bool = typer.Option(
        False, "--install", help="pip install dependencies before running"
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env", help="Path to .env file to load"
    ),
    keep_temp: bool = typer.Option(
        False, "--keep-temp", hidden=True, help="Do not delete the temp dir after run"
    ),
) -> None:
    """Generate a blueprint to a temp dir and run it locally.

    Without INPUT, starts an interactive REPL. With INPUT, runs once and exits.
    """
    # 1. Load and validate
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(f"[bold red]Validation error:[/] {e}")
        raise typer.Exit(1) from e

    # 2. Compile IR
    from agent_blueprint.ir.compiler import compile_blueprint
    from agent_blueprint.exceptions import BlueprintCompilationError

    try:
        ir = compile_blueprint(spec)
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    if target != "langgraph":
        err_console.print(
            f"[bold red]abp run[/] only supports [cyan]langgraph[/] target for now"
        )
        raise typer.Exit(1)

    # 3. Run
    from agent_blueprint.runners.local import LocalRunner

    try:
        runner = LocalRunner(ir, thread_id=thread_id)
        rc = runner.run(
            user_input=input,
            install=install,
            env_file=env_file if env_file.exists() else None,
            keep_temp=keep_temp,
        )
    except GeneratorError as e:
        err_console.print(f"[bold red]Generator error:[/] {e}")
        raise typer.Exit(1) from e

    raise typer.Exit(rc)
