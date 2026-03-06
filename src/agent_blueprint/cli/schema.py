"""abp schema - Export the blueprint JSON Schema."""

import json
from enum import Enum

import typer
from rich.console import Console
from rich.syntax import Syntax

from agent_blueprint.models.blueprint import BlueprintSpec

console = Console()


class OutputFormat(str, Enum):
    json = "json"
    yaml = "yaml"


def schema(
    format: OutputFormat = typer.Option(OutputFormat.json, "--format", "-f", help="Output format"),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to file instead of stdout"),
) -> None:
    """Export the blueprint JSON Schema for editor/IDE integration."""
    schema_dict = BlueprintSpec.model_json_schema()

    if format == OutputFormat.json:
        content = json.dumps(schema_dict, indent=2)
        syntax_lang = "json"
    else:
        try:
            from ruamel.yaml import YAML
            import io
            y = YAML()
            y.default_flow_style = False
            buf = io.StringIO()
            y.dump(schema_dict, buf)
            content = buf.getvalue()
            syntax_lang = "yaml"
        except ImportError:
            content = json.dumps(schema_dict, indent=2)
            syntax_lang = "json"

    if output:
        from pathlib import Path
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]Schema written to[/] {output}")
    else:
        console.print(Syntax(content, syntax_lang, theme="monokai"))
