"""abp init - Scaffold a new blueprint YAML file."""

from enum import Enum
from pathlib import Path

import typer
from rich.console import Console

console = Console()

BASIC_TEMPLATE = """\
blueprint:
  name: "{name}"
  version: "1.0"
  description: "A simple single-agent blueprint"

settings:
  default_model: "gpt-4o"
  default_temperature: 0.7

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

agents:
  assistant:
    model: "${{settings.default_model}}"
    system_prompt: |
      You are a helpful assistant.
    tools: []

graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
      description: "Main assistant node"
  edges:
    - from: assistant
      to: END

memory:
  backend: in_memory
"""

MULTI_AGENT_TEMPLATE = """\
blueprint:
  name: "{name}"
  version: "1.0"
  description: "A multi-agent blueprint with routing"

settings:
  default_model: "gpt-4o"
  default_temperature: 0.7

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    route:
      type: string
      default: null

agents:
  router:
    model: "${{settings.default_model}}"
    system_prompt: |
      You are a routing agent. Determine which specialist should handle the request.
      Respond with a JSON object: {{"route": "specialist_a" | "specialist_b"}}
    output_schema:
      route:
        type: string
        enum: [specialist_a, specialist_b]

  specialist_a:
    model: "${{settings.default_model}}"
    system_prompt: |
      You are Specialist A. Handle requests routed to you.

  specialist_b:
    model: "${{settings.default_model}}"
    system_prompt: |
      You are Specialist B. Handle requests routed to you.

graph:
  entry_point: router
  nodes:
    router:
      agent: router
      description: "Route incoming requests"
    specialist_a:
      agent: specialist_a
      description: "Specialist A handler"
    specialist_b:
      agent: specialist_b
      description: "Specialist B handler"
  edges:
    - from: router
      to:
        - condition: "state.route == 'specialist_a'"
          target: specialist_a
        - condition: "state.route == 'specialist_b'"
          target: specialist_b
        - default: END
    - from: specialist_a
      to: END
    - from: specialist_b
      to: END

memory:
  backend: in_memory
"""


class TemplateType(str, Enum):
    basic = "basic"
    multi_agent = "multi-agent"


def init(
    name: str = typer.Argument("my-agent", help="Name for the new agent"),
    template: TemplateType = typer.Option(
        TemplateType.basic, "--template", "-t", help="Template to use"
    ),
    output: Path = typer.Option(
        None, "--output", "-o", help="Output file path (default: <name>.yml)"
    ),
) -> None:
    """Scaffold a new blueprint YAML file."""
    if output is None:
        safe_name = name.replace(" ", "-").lower()
        output = Path(f"{safe_name}.yml")

    if output.exists():
        overwrite = typer.confirm(f"{output} already exists. Overwrite?")
        if not overwrite:
            raise typer.Exit(0)

    content = (
        BASIC_TEMPLATE if template == TemplateType.basic else MULTI_AGENT_TEMPLATE
    ).format(name=name)

    output.write_text(content, encoding="utf-8")
    console.print(f"[green]Created[/] {output}")
    console.print(f"\nNext steps:")
    console.print(f"  abp validate {output}")
    console.print(f"  abp inspect {output}")
    console.print(f"  abp generate {output} --target langgraph")
