"""abp init - Scaffold blueprint and agent-spec templates."""

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

contracts:
  nodes:
    router:
      produces: [route]
      output_contract: router_route
  outputs:
    router_route:
      type: object
      required: [route]
      additionalProperties: false
      properties:
        route:
          type: string
          enum: [specialist_a, specialist_b]

memory:
  backend: in_memory
"""

AGENT_SPEC_TEMPLATE = """\
# ABP Blueprint Request

Use this document as input for Codex, Claude Code, or another agentic coding CLI
to generate an agent-blueprint YAML file.

## Goal

Build an ABP-compatible YAML blueprint for:

> __DESCRIBE_THE_AGENT_GOAL__

## Output File

Write the final blueprint to:

`blueprints/{name}.yml`

## ABP Requirements

- Output must be valid agent-blueprint YAML.
- Use only schema-supported top-level sections:
  `blueprint`, `settings`, `state`, `model_providers`, `retrievers`,
  `mcp_servers`, `agents`, `tools`, `graph`, `memory`, `input`, `output`, `deploy`.
- Do not invent unsupported fields.
- Prefer LangGraph-compatible patterns.
- After writing the file, run:
  `abp validate blueprints/{name}.yml`
- If validation fails, fix the YAML and validate again.
- Use `abp schema --format yaml` if the current schema is needed.

## Agent Behavior

### Primary User

__WHO_WILL_USE_THIS_AGENT__

### Main Task

__WHAT_THE_AGENT_SHOULD_DO__

### Constraints

- __CONSTRAINT_1__
- __CONSTRAINT_2__
- __CONSTRAINT_3__

### Success Criteria

- __SUCCESS_CRITERION_1__
- __SUCCESS_CRITERION_2__

## Agents

Define these agents if needed:

| Agent | Responsibility | Tools | Notes |
|---|---|---|---|
| assistant | Main response agent | optional | Use for simple single-agent flows |
| router | Route user intent | none | Optional for multi-agent flows |
| researcher | Retrieve or inspect info | search_kb | Optional when RAG/search is needed |
| writer | Produce final response | none | Optional when separating research from writing |

## Tools

### Function Tools

| Name | Description | Parameters | Implementation |
|---|---|---|---|
| classify_intent | Classify user request | message:string | myapp.tools.classify_intent |

### Retrieval Tools

Use generic ABP RAG. ABP must not know Chroma, Qdrant, Pinecone, or any other
vector-store details.

| Retriever | Impl | Config |
|---|---|---|
| support_docs | myapp.retrieval.search_support_docs | index_name=support-docs |

| Tool | Retriever | top_k |
|---|---|---|
| search_kb | support_docs | 5 |

## RAG Mode

Use one:

- `tool_only`: model decides when to search.
- `context_only`: ABP retrieves before the LLM call and injects context only.
- `hybrid`: both context injection and model-callable search.

Selected mode:

`context_only`

## State

Required state fields:

| Field | Type | Reducer | Notes |
|---|---|---|---|
| messages | list[message] | append | Required for conversation |
| route | string | replace | Optional routing field |
| resolved | boolean | replace | Optional completion flag |

## Workflow

For single-agent flows:

1. User message enters `assistant`.
2. Assistant answers.
3. End.

For multi-agent flows:

1. User message enters `router`.
2. Router chooses a specialist.
3. Specialist handles the request.
4. Final answer returns to the user.

## Memory

Use:

```yaml
memory:
  backend: in_memory
```

Unless specified otherwise.

## Model

Default model:

`openai/gpt-4o`

Temperature:

`0.3`

## Generation Instructions

Use this prompt with an agentic CLI:

```md
You are generating an agent-blueprint YAML file.

Read the spec below and create a valid ABP blueprint. Do not invent fields
outside the ABP schema. Prefer minimal, explicit YAML.

After writing the file, run:

abp validate blueprints/{name}.yml

If validation fails, inspect the error, fix the YAML, and run validation again
until it passes.
```

## Output Requirements

- Write valid YAML only to the target file.
- Keep prompts explicit and production-oriented.
- Avoid placeholder tools unless listed above.
- Do not include markdown fences in the YAML file.
"""


class TemplateType(str, Enum):
    blueprint = "blueprint"
    spec = "spec"


def _safe_name(value: str) -> str:
    return value.replace(" ", "-").lower()


def _name_from_output(output: Path, default: str) -> str:
    output_stem = output.name
    for suffix in (
        ".agent-spec.md",
        ".spec.md",
        ".agents.yaml",
        ".agents.yml",
        ".md",
        ".yaml",
        ".yml",
    ):
        if output_stem.endswith(suffix):
            output_stem = output_stem[: -len(suffix)]
            break
    return _safe_name(output_stem or default)


def init(
    template: TemplateType = typer.Option(
        TemplateType.blueprint,
        "--template",
        "-t",
        help="Template to scaffold: blueprint or spec",
    ),
    output: Path = typer.Option(
        None, "--output", "--o", "-o", help="Output file path"
    ),
) -> None:
    """Scaffold a new blueprint YAML file or agent-spec markdown template."""
    is_spec = template == TemplateType.spec
    safe_name = "agent"
    if output is not None:
        safe_name = _name_from_output(output, safe_name)

    if output is None:
        suffix = ".spec.md" if is_spec else ".agents.yaml"
        output = Path(f"{safe_name}{suffix}")

    if output.exists():
        overwrite = typer.confirm(f"{output} already exists. Overwrite?")
        if not overwrite:
            raise typer.Exit(0)

    if template == TemplateType.blueprint:
        content = BASIC_TEMPLATE.format(name=safe_name)
    elif template == TemplateType.spec:
        content = AGENT_SPEC_TEMPLATE.format(name=safe_name)
    else:
        raise typer.BadParameter(f"Unsupported template: {template.value}")

    output.write_text(content, encoding="utf-8")
    console.print(f"[green]Created[/] {output}")
    console.print("\nNext steps:")
    if is_spec:
        console.print(f"  Fill in {output}")
        console.print("  Paste it into Codex, Claude Code, or another agentic CLI")
        console.print(f"  abp validate blueprints/{safe_name}.yml")
    else:
        console.print(f"  abp validate {output}")
        console.print(f"  abp inspect {output}")
        console.print(f"  abp generate {output} --target langgraph")
