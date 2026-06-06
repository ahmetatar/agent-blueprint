"""Main Typer CLI application."""

from typing import Any

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
from agent_blueprint.cli import fix_cmd
from agent_blueprint.cli import init_cmd
from agent_blueprint.cli import lint_cmd
from agent_blueprint.cli import doctor_cmd
from agent_blueprint.cli import run_cmd
from agent_blueprint.cli import test_cmd
from agent_blueprint.cli import eval_cmd
from agent_blueprint.cli import gate_cmd
from agent_blueprint.cli import traces_cmd
from agent_blueprint.cli import deploy_cmd
from agent_blueprint.cli import github_cmd
from agent_blueprint.cli import package_cmd
from agent_blueprint.cli import editor_cmd

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

    # typer >= 0.26 vendors click as typer._click, so click types are not
    # importable; the override stays structurally compatible via Any.
    def format_help(self, ctx: Any, formatter: Any) -> None:
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
        super().format_help(ctx, formatter)


app = typer.Typer(
    name="abp",
    cls=BannerGroup,
    help="Agent Blueprint - Declarative, framework-agnostic AI agent orchestration via YAML",
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
app.command("fix")(fix_cmd.fix)
app.command("init")(init_cmd.init)
app.command("lint")(lint_cmd.lint)
app.command("doctor")(doctor_cmd.doctor)
app.command("run")(run_cmd.run)
app.command("test")(test_cmd.test)
app.command("eval")(eval_cmd.eval_)
app.command("gate")(gate_cmd.gate)
app.add_typer(traces_cmd.traces_app, name="traces")
app.command("deploy")(deploy_cmd.deploy)
app.command("package")(package_cmd.package)
app.command("editor")(editor_cmd.editor)
app.command("github")(github_cmd.github)


if __name__ == "__main__":
    app(prog_name="abp")
