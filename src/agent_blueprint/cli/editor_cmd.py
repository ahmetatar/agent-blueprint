"""abp editor — open the visual blueprint editor in the browser."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

_EXTRA_HINT = (
    "[bold red]The editor extra is not installed.[/]\n"
    "Install it with: [cyan]pip install 'agent-blueprint\\[editor]'[/]"
)


def editor(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    port: Optional[int] = typer.Option(
        None, "--port", "-p", help="Port to bind (default: a random free port)"
    ),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Open the browser automatically (default: on)"
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        hidden=True,
        help="API-only mode on a fixed port for the Vite dev-server proxy",
    ),
) -> None:
    """Open the visual editor for a blueprint (localhost only).

    The YAML file stays the single source of truth; the editor is a live view
    over it. Press Ctrl+C to stop.
    """
    if not blueprint.is_file():
        err_console.print(f"[bold red]Blueprint not found:[/] {blueprint}")
        raise typer.Exit(1)

    try:
        from agent_blueprint.editor import server
    except ModuleNotFoundError as e:
        err_console.print(_EXTRA_HINT)
        raise typer.Exit(1) from e

    def announce(url: str) -> None:
        console.print(f"[green]✓[/] Editor running: [bold cyan]{url}[/]")
        console.print("[bright_black]Press Ctrl+C to stop[/]")

    try:
        server.run_editor(
            blueprint,
            port=port,
            open_browser=open_browser,
            dev=dev,
            url_callback=announce,
        )
    except KeyboardInterrupt:  # pragma: no cover - interactive teardown
        raise typer.Exit(0) from None
