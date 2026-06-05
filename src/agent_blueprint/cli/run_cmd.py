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
        True, "--install/--no-install", help="pip install dependencies before running (default: on)"
    ),
    env_file: Path = typer.Option(
        Path(".env"), "--env", help="Path to .env file to load"
    ),
    sandbox: Optional[bool] = typer.Option(
        None,
        "--sandbox/--no-sandbox",
        help="Run inside a container (default: blueprint run.sandbox.enabled)",
    ),
    engine: Optional[str] = typer.Option(
        None, "--engine", help="Sandbox container engine: auto | docker | podman"
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
            "[bold red]abp run[/] only supports [cyan]langgraph[/] target for now"
        )
        raise typer.Exit(1)

    # 3. Resolve sandbox settings: CLI flags override the blueprint `run:` section
    from agent_blueprint.models.run import SandboxConfig, SandboxEngine

    sandbox_cfg = (spec.run.sandbox if spec.run else None) or SandboxConfig()
    use_sandbox = sandbox if sandbox is not None else sandbox_cfg.enabled
    if engine is not None:
        try:
            sandbox_cfg = sandbox_cfg.model_copy(update={"engine": SandboxEngine(engine)})
        except ValueError:
            err_console.print(
                f"[bold red]Invalid engine:[/] '{engine}' (expected: auto | docker | podman)"
            )
            raise typer.Exit(1) from None

    # 4. Run
    from agent_blueprint.exceptions import SandboxError
    from agent_blueprint.runners.local import LocalRunner
    from agent_blueprint.runners.sandbox import SandboxRunner

    try:
        runner: LocalRunner
        if use_sandbox:
            runner = SandboxRunner(ir, spec, sandbox_cfg, thread_id=thread_id)
            console.print(f"→ Sandbox engine: [cyan]{runner.engine}[/]")
        else:
            runner = LocalRunner(ir, thread_id=thread_id)
        rc = runner.run(
            user_input=input,
            install=install and not use_sandbox,
            env_file=env_file if env_file.exists() else None,
            keep_temp=keep_temp,
        )
    except SandboxError as e:
        err_console.print(f"[bold red]Sandbox error:[/] {e}")
        raise typer.Exit(1) from e
    except GeneratorError as e:
        err_console.print(f"[bold red]Generator error:[/] {e}")
        raise typer.Exit(1) from e

    raise typer.Exit(rc)
