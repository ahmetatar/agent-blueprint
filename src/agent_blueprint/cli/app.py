"""Main Typer CLI application."""

import typer
from rich.console import Console

from agent_blueprint.cli import validate as validate_cmd
from agent_blueprint.cli import schema as schema_cmd
from agent_blueprint.cli import generate as generate_cmd
from agent_blueprint.cli import inspect_cmd
from agent_blueprint.cli import init_cmd
from agent_blueprint.cli import run_cmd
from agent_blueprint.cli import deploy_cmd

console = Console()

app = typer.Typer(
    name="abp",
    help="Agent Blueprint - Declarative AI agent orchestration via YAML",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.command("validate")(validate_cmd.validate)
app.command("schema")(schema_cmd.schema)
app.command("generate")(generate_cmd.generate)
app.command("inspect")(inspect_cmd.inspect)
app.command("init")(init_cmd.init)
app.command("run")(run_cmd.run)
app.command("deploy")(deploy_cmd.deploy)


if __name__ == "__main__":
    app()
