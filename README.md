<p align="center">
  <img src="logo.svg" alt="agent-blueprint" width="820"/>
</p>

# agent-blueprint

[![PyPI version](https://img.shields.io/pypi/v/agent-blueprint)](https://pypi.org/project/agent-blueprint/)
[![Python 3.11+](https://img.shields.io/pypi/pyversions/agent-blueprint)](https://pypi.org/project/agent-blueprint/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen)]()

Declarative, framework-agnostic AI agent orchestration via YAML.

Define your agent graph in a YAML file. Generate production-ready code for LangGraph, CrewAI, or plain Python — no boilerplate.

```bash
abp init my-agent
abp generate my-agent.yml --target langgraph
```

---

## Why

Building multi-agent systems with LangGraph, CrewAI, or AutoGen means writing a lot of framework-specific boilerplate. Changing frameworks means rewriting everything. `agent-blueprint` separates the **what** (your agent logic) from the **how** (the framework).

| Without abp | With abp |
|---|---|
| Write LangGraph state classes, node functions, graph builders | Write a 30-line YAML file |
| Rewrite everything when switching frameworks | Change `--target` flag |
| No standard schema — every project looks different | Consistent, validated blueprint format |

---

## Installation

**Requirements:** Python 3.11+

```bash
pip install agent-blueprint
```

Or with `pipx` (recommended for CLI tools — keeps `abp` isolated):

```bash
pipx install agent-blueprint
```

Or install from source:

```bash
git clone https://github.com/ahmetatar/agent-blueprint
cd agent-blueprint
pip install -e ".[dev]"
```

After installation, the `abp` CLI is available:

```bash
abp --help
```

---

## Quick Start

### 1. Create a blueprint

```bash
abp init --template=blueprint --output=my-agent.agents.yaml
# or create a markdown request template for Codex/Claude Code:
abp init --template=spec --output=my-agent.spec.md
```

This creates `my-agent.agents.yaml`:

```yaml
blueprint:
  name: "my-agent"
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
    model: "${settings.default_model}"
    system_prompt: |
      You are a helpful assistant.

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
```

### 2. Validate

```bash
abp validate my-agent.agents.yaml
```

```
╭──────────────────── Valid — my-agent.agents.yaml ────────────────────╮
│   Blueprint      my-agent                                             │
│   Version        1.0                                                  │
│   Agents         1                                                    │
│   Tools          0                                                    │
│   Nodes          1                                                    │
│   Entry point    assistant                                            │
╰───────────────────────────────────────────────────────────────────────╯
```

### 3. Visualize the graph

```bash
abp inspect my-agent.agents.yaml
```

Outputs a [Mermaid](https://mermaid.live) diagram you can paste directly into any Mermaid renderer.

### 4. Generate code

```bash
abp generate my-agent.agents.yaml --target langgraph
```

```
╭────────────── Generated — my-agent (langgraph) ──────────────╮
│   my-agent-langgraph/__init__.py                              │
│   my-agent-langgraph/state.py                                 │
│   my-agent-langgraph/tools.py                                 │
│   my-agent-langgraph/nodes.py                                 │
│   my-agent-langgraph/graph.py                                 │
│   my-agent-langgraph/main.py                                  │
│   my-agent-langgraph/requirements.txt                         │
│   my-agent-langgraph/.env.example                             │
╰───────────────────────────────────────────────────────────────╯
```

### 5. Run

```bash
cd my-agent-langgraph
pip install -r requirements.txt
cp .env.example .env   # add your OPENAI_API_KEY
python main.py "Hello, how are you?"
```

---

## Blueprint Schema

A blueprint YAML has these top-level sections:

| Section | Required | Description |
|---|---|---|
| `blueprint` | Yes | Name, version, description |
| `settings` | No | Default model, temperature, retries |
| `state` | No | Shared state fields flowing through the graph |
| `model_providers` | No | Model provider connections ([details](docs/model-providers.md)) |
| `retrievers` | No | Generic RAG retriever implementations ([details](docs/rag.md)) |
| `mcp_servers` | No | MCP server connections ([details](docs/mcp-servers.md)) |
| `agents` | Yes | Agent definitions (model, prompt, tools) |
| `tools` | No | Tool definitions ([details](docs/tools.md)) |
| `graph` | Yes | Nodes, edges, entry point |
| `memory` | No | Checkpointing / persistence ([details](docs/memory.md)) |
| `input` | No | Input schema for the agent |
| `output` | No | Output schema for the agent |
| `deploy` | No | Cloud deployment configuration ([details](docs/deploy.md)) |

### `blueprint`

```yaml
blueprint:
  name: "my-agent"       # Required. Used for naming generated files.
  version: "1.0"         # Optional. Default: "1.0"
  description: "..."     # Optional.
  author: "..."          # Optional.
  tags: [support, nlp]   # Optional.
```

### `settings`

```yaml
settings:
  default_model: "gpt-4o"            # Default model for all agents
  default_model_provider: openai_gpt  # Default provider (references model_providers)
  default_temperature: 0.7
  max_retries: 3
  timeout_seconds: 300
```

Settings values can be referenced anywhere with `${settings.field_name}`.

> **Variable interpolation** supports two namespaces:
> - `${settings.field}` — resolved from the blueprint's `settings` section
> - `${env.VAR_NAME}` — resolved from environment variables at load time; if the variable is not set, the placeholder is kept as-is

### `state`

Defines the typed state object shared across all nodes:

```yaml
state:
  fields:
    messages:
      type: "list[message]"   # Built-in message list type
      reducer: append          # How concurrent updates merge: append | replace | merge
    department:
      type: string
      default: null
      enum: [billing, technical, general]
    resolved:
      type: boolean
      default: false
```

### `agents`

```yaml
agents:
  my_agent:
    name: "Friendly Name"           # Optional display name
    model: "gpt-4o"                 # or ${settings.default_model}
    model_provider: openai_gpt      # References model_providers
    system_prompt: |
      You are a helpful assistant.
    tools: [tool_a, tool_b]         # References to tools section
    temperature: 0.5
    max_tokens: 2048
    output_schema:                  # Structured output fields
      department:
        type: string
        enum: [billing, technical]
    memory:
      type: conversation_buffer     # conversation_buffer | summary | vector
      max_tokens: 4000
    human_in_the_loop:
      enabled: true
      trigger: before_tool_call     # before_tool_call | after_tool_call | before_response | always
      tools: [dangerous_tool]       # Only require approval for specific tools
    llm_params:                     # Optional raw kwargs for the LangChain chat class
      timeout: 60
    reasoning:
      enabled: true                 # Mark this as a native reasoning/thinking agent
      params:                       # Raw kwargs passed through to the selected adapter
        reasoning:
          effort: high
```

> See [Model Providers](docs/model-providers.md) for adapter selection and [Reasoning Patterns](docs/reasoning.md) for native thinking and graph-level reasoning strategies.

### `tools`

Four tool types are supported: `function`, `api`, `retrieval`, and `mcp`.

```yaml
tools:
  classify_intent:
    type: function
    impl: "myapp.classifiers.classify_intent"   # optional: wire existing code
    description: "Classify customer intent"
    parameters:
      message:
        type: string
        required: true

  lookup_invoice:
    type: api
    method: GET
    url: "https://api.example.com/invoices/{invoice_id}"
    auth:
      type: bearer
      token_env: "BILLING_API_KEY"
```

> See [Tools](docs/tools.md) for full documentation on all tool types including `impl`, `api`, `retrieval`, and `mcp`.
> See [Retrieval-Augmented Generation](docs/rag.md) for generic retrievers and automatic RAG context injection.

### `graph`

Defines the agent workflow as a directed graph:

```yaml
graph:
  entry_point: router

  nodes:
    router:
      agent: router
      description: "Route requests"
    handle_billing:
      agent: billing_agent
    escalate:
      type: handoff
      channel: slack

  edges:
    # Simple edge
    - from: handle_billing
      to: END

    # Conditional routing
    - from: router
      to:
        - condition: "state.department == 'billing'"
          target: handle_billing
        - condition: "state.department == 'technical'"
          target: handle_technical
        - default: END
```

**Condition expressions** support: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`, `and`, `or`, `not`. They reference `state` fields: `state.field_name`.

### `memory`

```yaml
memory:
  backend: in_memory     # in_memory | sqlite | postgres | redis
```

> See [Memory & Checkpointing](docs/memory.md) for all backend options and configuration.

---

## CLI Reference

| Command | Description |
|---|---|
| `abp init --template=blueprint --output=<file>` | Scaffold an ABP blueprint YAML |
| `abp init --template=spec --output=<file>` | Scaffold a markdown request template for Codex/Claude Code |
| `abp validate <file>` | Validate a blueprint against the schema (`--quiet` for CI) |
| `abp generate <file>` | Generate framework code (`--target langgraph\|plain`, `--dry-run`) |
| `abp run <file> [input]` | Generate to temp dir and run locally (single-shot or REPL) |
| `abp deploy <file>` | Deploy to cloud (`--platform azure\|aws\|gcp`, [details](docs/deploy.md)) |
| `abp inspect <file>` | Visualize graph as Mermaid diagram |
| `abp schema` | Export JSON Schema (`--format json\|yaml`) |
| `abp github` | Open the GitHub repository |

### `abp run`

```bash
# Single-shot
abp run my-agent.yml "What is the capital of France?"

# Interactive REPL (omit input)
abp run my-agent.yml

# With options
abp run my-agent.yml --thread-id session-1 --install --env .env.local
```

| Flag | Default | Description |
|---|---|---|
| `--target` | `langgraph` | Target framework |
| `--thread-id` | `default` | Conversation thread ID |
| `--install` | `false` | Run `pip install -r requirements.txt` before executing |
| `--env` | `.env` | Path to a `.env` file to load |

---

## Examples

The `examples/` directory contains ready-to-use blueprints:

| Example | Description |
|---|---|
| [`basic-chatbot.yml`](examples/basic-chatbot.yml) | Single-agent chatbot — the simplest possible blueprint |
| [`customer-support.yml`](examples/customer-support.yml) | Three-agent system: router → billing specialist / technical support |
| [`research-team.yml`](examples/research-team.yml) | Sequential pipeline: planner → researcher → writer |

```bash
abp generate examples/customer-support.yml --target langgraph
abp inspect examples/customer-support.yml
```

> See [Reasoning Patterns](docs/reasoning.md) for advanced patterns: Chain-of-Thought, ReAct, Self-Reflection, and Extended Thinking.

---

## Generated Project Structure (LangGraph target)

```
my-agent-langgraph/
├── __init__.py          # Package init
├── state.py             # AgentState TypedDict
├── tools.py             # Tool functions (fill in implementations)
├── nodes.py             # Node functions (one per agent node)
├── graph.py             # StateGraph construction with edges and routing
├── main.py              # Entrypoint: run(user_input) → str
├── requirements.txt     # langgraph, langchain-openai, httpx, ...
└── .env.example         # Required environment variables
```

The generated code is **human-readable and fully editable**. It's a starting point, not a black box.

---

## IDE Integration (VS Code)

Export the JSON Schema and configure the YAML extension for autocompletion and inline validation:

```bash
abp schema --output blueprint-schema.json
```

Add to `.vscode/settings.json`:

```json
{
  "yaml.schemas": {
    "./blueprint-schema.json": "*.blueprint.yml"
  }
}
```

---

## Development

```bash
git clone https://github.com/ahmetatar/agent-blueprint
cd agent-blueprint
pip install -e ".[dev]"

# Run tests
python3 -m pytest tests/ -v

# Lint
ruff check src/
```

### Project Structure

```
src/agent_blueprint/
├── cli/            # Typer CLI commands (validate, generate, inspect, init, schema)
├── models/         # Pydantic v2 schema models
├── ir/             # Intermediate representation: compiler + expression parser
├── generators/     # Code generators (langgraph, plain, crewai stub)
├── deployers/      # Cloud deployers
├── templates/      # Jinja2 templates per target framework
└── utils/          # YAML loader, Mermaid visualizer
```

### Adding a new target framework

1. Create `src/agent_blueprint/generators/<framework>.py` implementing `BaseGenerator`
2. Add Jinja2 templates to `src/agent_blueprint/templates/<framework>/`
3. Register in `src/agent_blueprint/cli/generate.py`

The `AgentGraph` IR in `src/agent_blueprint/ir/compiler.py` is the single input to all generators — you don't touch the parser or validator.

---

## Roadmap

- [x] YAML schema + Pydantic validation
- [x] Variable interpolation (`${settings.field}`, `${env.VAR}`)
- [x] Safe condition expression parser
- [x] LangGraph code generator
- [x] Plain Python generator
- [x] CLI: `validate`, `generate`, `inspect`, `init`, `schema`
- [x] MCP server configuration and `mcp` tool type
- [x] Model provider configuration (OpenAI, Anthropic, Google, Ollama, Azure, Bedrock)
- [x] `impl` field for function tools
- [x] `abp run` — generate and execute locally
- [x] `abp deploy --platform azure|aws|gcp`
- [x] PyPI publish (`pip install agent-blueprint`)
- [ ] CrewAI generator
- [ ] AutoGen generator
- [ ] VS Code extension

---

## License

MIT
