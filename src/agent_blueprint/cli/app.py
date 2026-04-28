"""Main Typer CLI application."""

import click
import typer
from rich.align import Align
from rich.box import HEAVY
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from typer import rich_utils
from typer.core import HAS_RICH, TyperGroup

from agent_blueprint.cli import validate as validate_cmd
from agent_blueprint.cli import schema as schema_cmd
from agent_blueprint.cli import generate as generate_cmd
from agent_blueprint.cli import inspect_cmd
from agent_blueprint.cli import init_cmd
from agent_blueprint.cli import run_cmd
from agent_blueprint.cli import deploy_cmd
from agent_blueprint.cli import github_cmd

_WELCOME_BANNER = """\
 █████╗ ██████╗ ██████╗      ██████╗██╗     ██╗
██╔══██╗██╔══██╗██╔══██╗    ██╔════╝██║     ██║
███████║██████╔╝██████╔╝    ██║     ██║     ██║
██╔══██║██╔══██╗██╔═══╝     ██║     ██║     ██║
██║  ██║██████╔╝██║         ╚██████╗███████╗██║
╚═╝  ╚═╝╚═════╝ ╚═╝          ╚═════╝╚══════╝╚═╝
"""


class BannerGroup(TyperGroup):
    """Render a styled splash before the standard help output."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        if HAS_RICH and self.rich_markup_mode is not None:
            console = rich_utils._get_rich_console()
            banner = Text(_WELCOME_BANNER, style="bold white")
            tagline = Text("THE OPEN AGENT BLUEPRINT ECOSYSTEM", style="bright_black")
            panel = Panel(
                Padding(Align.left(Text.assemble(banner, "\n", tagline)), (0, 1)),
                box=HEAVY,
                border_style="white",
                padding=(0, 1),
            )
            console.print(panel)
            console.print()
            return rich_utils.rich_format_help(
                obj=self,
                ctx=ctx,
                markup_mode=self.rich_markup_mode,
            )

        formatter.write(f"{_WELCOME_BANNER}\n\n")
        formatter.write_text("THE OPEN AGENT BLUEPRINT ECOSYSTEM")
        formatter.write_paragraph()
        click.Group.format_help(self, ctx, formatter)


app = typer.Typer(
    name="abp",
    cls=BannerGroup,
    help="Agent Blueprint - Declarative AI agent orchestration via YAML",
    invoke_without_command=True,
    no_args_is_help=False,
    rich_markup_mode="rich",
)


@app.callback()
def main(ctx: typer.Context) -> None:
    """Render root help when ABP is called without a subcommand."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()

app.command("validate")(validate_cmd.validate)
app.command("schema")(schema_cmd.schema)
app.command("generate")(generate_cmd.generate)
app.command("inspect")(inspect_cmd.inspect)
app.command("init")(init_cmd.init)
app.command("run")(run_cmd.run)
app.command("deploy")(deploy_cmd.deploy)
app.command("github")(github_cmd.github)


if __name__ == "__main__":
    app(prog_name="abp")
