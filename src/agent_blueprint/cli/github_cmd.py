"""GitHub command - opens the project's GitHub repository."""

import webbrowser
import typer
from rich.console import Console

console = Console()

GITHUB_URL = "https://github.com/ahmetatar/agent-blueprint"


def github() -> None:
    """Open the Agent Blueprint GitHub repository in your browser."""
    console.print(f"[cyan]Opening[/cyan] [link={GITHUB_URL}]{GITHUB_URL}[/link]")
    webbrowser.open(GITHUB_URL)
