"""abp ui — open the visual Blueprint editor in the browser."""

import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()


def ui(
    blueprint: Optional[Path] = typer.Argument(None, help="Blueprint YAML to load (optional)"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host to bind"),
    port: int = typer.Option(7842, "--port", "-p", help="Port to listen on"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser automatically"),
) -> None:
    """Open the visual Agent Blueprint editor in your browser.

    Design agents and edges visually, then generate or run YAML.
    """
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        console.print(
            "[bold red]UI dependencies not installed.[/]\n"
            "Run: [cyan]pip install 'agent-blueprint[ui]'[/]"
        )
        raise typer.Exit(1)

    from agent_blueprint.ui.server import create_app

    if blueprint and not blueprint.exists():
        console.print(f"[bold red]File not found:[/] {blueprint}")
        raise typer.Exit(1)

    app = create_app(blueprint_path=blueprint)
    url = f"http://{host}:{port}"

    console.rule("[bold purple]Agent Blueprint UI[/]")
    console.print(f"  Editor:  [cyan]{url}[/]")
    if blueprint:
        console.print(f"  File:    [green]{blueprint.resolve()}[/]")
    console.print("  Stop:    [bold]Ctrl+C[/]\n")

    if not no_browser:
        def _open():
            time.sleep(0.9)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="error")
