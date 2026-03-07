<p align="center">
  <img src="logo.svg" alt="agent-blueprint" width="820"/>
</p>

# agent-blueprint

Declarative, framework-agnostic AI agent orchestration via YAML.

Define your agent graph in a YAML file. Generate production-ready code for LangGraph, CrewAI, or plain Python — no boilerplate.

```bash
abp init my-agent
abp generate my-agent.yml --target langgraph
```

---

## Table of Contents

- [Why](#why)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Blueprint Schema](#blueprint-schema)
  - [`blueprint`](#blueprint)
  - [`settings`](#settings)
  - [`state`](#state)
  - [`agents`](#agents)
  - [`model_providers`](#model_providers)
  - [`mcp_servers`](#mcp_servers)
  - [`tools`](#tools)
  - [`graph`](#graph)
  - [`memory`](#memory)
- [CLI Reference](#cli-reference)
  - [`abp init`](#abp-init)
  - [`abp validate`](#abp-validate)
  - [`abp generate`](#abp-generate)
  - [`abp deploy`](#abp-deploy)
  - [`abp run`](#abp-run)
  - [`abp inspect`](#abp-inspect)
  - [`abp schema`](#abp-schema)
- [Examples](#examples)
- [Generated Project Structure](#generated-project-structure-langgraph-target)
- [IDE Integration (VS Code)](#ide-integration-vs-code)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

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
abp init my-agent
# or for a multi-agent setup:
abp init my-agent --template multi-agent
```

This creates `my-agent.yml`:

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
abp validate my-agent.yml
```

```
╭──────────────────────── Valid — my-agent.yml ────────────────────────╮
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
abp inspect my-agent.yml
```

Outputs a [Mermaid](https://mermaid.live) diagram you can paste directly into any Mermaid renderer.

### 4. Generate code

```bash
abp generate my-agent.yml --target langgraph
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
| `model_providers` | No | Model provider connection definitions (API keys, endpoints) |
| `mcp_servers` | No | MCP server connection definitions |
| `agents` | Yes | Agent definitions (model, prompt, tools) |
| `tools` | No | Tool definitions (function, api, retrieval, mcp) |
| `graph` | Yes | Nodes, edges, entry point |
| `memory` | No | Checkpointing / persistence config |
| `input` | No | Input schema for the agent |
| `output` | No | Output schema for the agent |
| `deploy` | No | Cloud deployment configuration (Azure, AWS, GCP) |

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
    model_provider: openai_gpt      # References model_providers; falls back to settings.default_model_provider
    system_prompt: |
      You are a helpful assistant.
    tools: [tool_a, tool_b]         # References to tools section
    temperature: 0.5                # Override settings.default_temperature
    max_tokens: 2048
    output_schema:                  # Structured output fields to extract
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
```

### `model_providers`

Defines named model provider connections. Agents reference these by name via `model_provider`. If omitted, the generated code assumes the framework's default provider resolution (e.g. `OPENAI_API_KEY` environment variable).

```yaml
model_providers:
  openai_gpt:
    provider: openai              # openai | anthropic | google | ollama | azure_openai | bedrock | openai_compatible
    api_key_env: OPENAI_API_KEY   # env var holding the API key

  gemini:
    provider: google
    api_key_env: GOOGLE_API_KEY

  local_ollama:
    provider: ollama
    base_url: "http://localhost:11434"   # or ${env.OLLAMA_URL}

  azure_gpt4:
    provider: azure_openai
    base_url: "${env.AZURE_OPENAI_ENDPOINT}"
    api_key_env: AZURE_OPENAI_KEY
    deployment: "gpt-4o-prod"
    api_version: "2024-02-01"

  bedrock_claude:
    provider: bedrock
    region: "us-east-1"
    aws_profile_env: AWS_PROFILE

  my_local_server:
    provider: openai_compatible   # Any OpenAI-compatible endpoint (vLLM, LM Studio, etc.)
    base_url: "http://localhost:8000/v1"
    api_key_env: LOCAL_API_KEY    # optional
```

| Provider | Required fields | Optional fields |
|---|---|---|
| `openai` | — | `api_key_env` |
| `anthropic` | — | `api_key_env` |
| `google` | — | `api_key_env` |
| `ollama` | `base_url` | — |
| `azure_openai` | `base_url`, `deployment` | `api_key_env`, `api_version` |
| `bedrock` | — | `region`, `aws_profile_env` |
| `openai_compatible` | `base_url` | `api_key_env` |

Agents reference a provider with `model_provider`. If not set, `settings.default_model_provider` is used:

```yaml
agents:
  researcher:
    model: "gemini-2.0-flash"
    model_provider: gemini         # ← references model_providers.gemini

  writer:
    model: "llama3.2"
    model_provider: local_ollama

  router:
    model: "gpt-4o"
    # model_provider omitted → falls back to settings.default_model_provider
```

### `mcp_servers`

Defines MCP (Model Context Protocol) server connections. Tools of type `mcp` reference these by name.

```yaml
mcp_servers:
  stitch:
    transport: sse                        # sse | http | stdio
    url: "http://localhost:3100/sse"
    headers:
      Authorization: "Bearer ${env.STITCH_TOKEN}"

  filesystem:
    transport: stdio                      # Launched as a subprocess
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    env:
      SOME_VAR: "value"
```

| Transport | Required fields | Optional fields |
|---|---|---|
| `sse` / `http` | `url` | `headers` |
| `stdio` | `command` | `args`, `env` |

### `tools`

Four tool types are supported:

**`function`** — A Python function you implement.

Two modes are available:

**Without `impl` (stub generated):** The generator creates a placeholder you fill in:

```yaml
tools:
  classify_intent:
    type: function
    description: "Classify customer intent"
    parameters:
      message:
        type: string
        required: true
```

Generated `tools.py`:
```python
@tool
def classify_intent(message: str) -> str:
    """Classify customer intent"""
    # TODO: implement classify_intent
    raise NotImplementedError("classify_intent is not implemented yet")
```

**With `impl` (wires an existing function):** Point to any Python function in your codebase using a dotted import path. The generator produces an import + `tool()` wrapper — it never touches your implementation file:

```yaml
tools:
  classify_intent:
    type: function
    impl: "myapp.classifiers.classify_intent"   # dotted import path
    description: "Classify customer intent"
    parameters:
      message:
        type: string
        required: true

  web_search:
    type: function
    impl: "myapp.tools.search.web_search"        # deeper path works too
    description: "Search the web"
    parameters:
      query:
        type: string
        required: true
```

Your function, wherever you keep it:
```python
# myapp/classifiers.py  — your file, generator never touches it
def classify_intent(message: str) -> str:
    return call_my_model(message)
```

Generated `tools.py`:
```python
from myapp.classifiers import classify_intent as _classify_intent_impl
from myapp.tools.search import web_search as _web_search_impl

classify_intent = tool(_classify_intent_impl, name="classify_intent", description="...")
web_search      = tool(_web_search_impl,      name="web_search",      description="...")
```

> **Why `impl`?** Without it, every `abp generate` run overwrites your implementations in `tools.py`. With `impl`, the generated file is pure wiring code — safe to regenerate at any time.

**`api`** — An HTTP endpoint (code generated automatically):

```yaml
tools:
  lookup_invoice:
    type: api
    method: GET
    url: "https://api.example.com/invoices/{invoice_id}"
    auth:
      type: bearer          # bearer | basic | api_key
      token_env: "BILLING_API_KEY"
```

**`retrieval`** — A vector store retrieval tool:

```yaml
tools:
  search_kb:
    type: retrieval
    source: "knowledge_base"
    embedding_model: "text-embedding-3-small"
    top_k: 5
```

**`mcp`** — A tool exposed by an MCP server (references `mcp_servers`):

```yaml
tools:
  create_project:
    type: mcp
    server: stitch             # Must match a key in mcp_servers
    tool: create_project       # Tool name on the server
    description: "Create a new Stitch project"
    parameters:
      name:
        type: string
        required: true

  generate_screen:
    type: mcp
    server: stitch
    tool: generate_screen_from_text
    parameters:
      text:
        type: string
        required: true
      project_id:
        type: string
        required: true
```

Agents use MCP tools the same way as any other tool:

```yaml
agents:
  ui_agent:
    model: "claude-opus-4-6"
    tools: [create_project, generate_screen]
```

### `graph`

Defines the agent workflow as a directed graph:

```yaml
graph:
  entry_point: router        # Node where execution starts

  nodes:
    router:
      agent: router           # References an agent
      description: "Route requests"
    handle_billing:
      agent: billing_agent
    escalate:
      type: handoff           # Built-in: handoff | function
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
        - default: END           # Fallback if no condition matches
```

**Condition expressions** support: `==`, `!=`, `<`, `>`, `<=`, `>=`, `in`, `not in`, `and`, `or`, `not`. They reference `state` fields: `state.field_name`.

### `memory`

Configures LangGraph checkpointing — how conversation state and turn history are persisted across invocations. The `thread_id` passed to `run()` identifies the conversation; the checkpointer stores state per thread.

```yaml
memory:
  backend: in_memory           # in_memory | sqlite | postgres | redis
  connection_string_env: REDIS_URL
  checkpoint_every: node       # node | edge | manual
```

| Backend | Persistence | Use case |
|---|---|---|
| `in_memory` | Process lifetime only | Development, stateless APIs |
| `sqlite` | Local file | Local dev with persistence, single-process |
| `postgres` | External DB | Production, multi-instance |
| `redis` | External cache | Production, low-latency, multi-instance |

**`in_memory`** — no extra config needed:

```yaml
memory:
  backend: in_memory
```

**`sqlite`** — stores state in a local `.db` file. No server required:

```yaml
memory:
  backend: sqlite
  connection_string_env: SQLITE_DB_PATH   # optional; defaults to <blueprint-name>.db
```

```bash
# .env
SQLITE_DB_PATH=./my-agent.db
```

**`redis`** — connect to any Redis instance:

```yaml
memory:
  backend: redis
  connection_string_env: REDIS_URL
```

```bash
# .env
REDIS_URL=redis://localhost:6379       # local Redis
# REDIS_URL=rediss://user:pass@host:6380  # TLS / cloud Redis
```

Required package (added automatically to generated `requirements.txt`): `langgraph-checkpoint-redis`, `redis`

**`postgres`** — connect via standard PostgreSQL URL:

```yaml
memory:
  backend: postgres
  connection_string_env: DATABASE_URL
```

```bash
# .env
DATABASE_URL=postgresql://user:pass@localhost:5432/mydb
```

Required packages: `langgraph-checkpoint-postgres`, `psycopg[binary]`

> **Note:** If `DATABASE_URL` is not set at startup, the agent raises a `RuntimeError` immediately (fail-fast). For `redis`, the default is `redis://localhost:6379` if the env var is not set.

---

## CLI Reference

### `abp init`

Scaffold a new blueprint file:

```bash
abp init <name> [--template basic|multi-agent] [--output my-agent.yml]
```

| Flag | Default | Description |
|---|---|---|
| `--template` | `basic` | `basic` (single agent) or `multi-agent` (router + specialists) |
| `--output` | `<name>.yml` | Output file path |

### `abp validate`

Validate a blueprint against the schema:

```bash
abp validate <blueprint.yml> [--quiet]
```

Exits with code `0` on success, `1` on failure. Use `--quiet` in CI pipelines.

### `abp generate`

Generate framework code:

```bash
abp generate <blueprint.yml> [--target langgraph|crewai|plain] [--output-dir ./out] [--dry-run]
```

| Flag | Default | Description |
|---|---|---|
| `--target` | `langgraph` | Target framework |
| `--output-dir` | `./<name>-<target>` | Where to write generated files |
| `--dry-run` | `false` | List files without writing them |

**Supported targets:**

| Target | Status | Description |
|---|---|---|
| `langgraph` | Stable | Full LangGraph project with StateGraph, nodes, tools, memory |
| `plain` | Stable | Plain Python with openai SDK, no framework |
| `crewai` | Coming soon | CrewAI crews and tasks |

### `abp deploy`

Deploy to a cloud platform. Requires Docker and the relevant CLI (`az` / `aws` / `gcloud`) to be installed and authenticated.

```bash
abp deploy my-agent.yml --platform azure
abp deploy my-agent.yml --platform gcp --image-tag v1.2
abp deploy my-agent.yml --platform aws --dry-run
abp deploy my-agent.yml --env EXTRA_KEY=value
```

| Flag | Default | Description |
|---|---|---|
| `--platform` | from blueprint | `azure` \| `aws` \| `gcp` |
| `--image-tag` | `latest` | Docker image tag |
| `--dry-run` | `false` | Print all commands without executing |
| `--env KEY=VAL` | — | Extra env vars to inject as secrets (repeatable) |

**Deploy flow:**

1. Validates and compiles the blueprint
2. Generates LangGraph code to a temp dir
3. Adds `Dockerfile`, `server.py` (FastAPI `/invoke` + `/health`), `requirements_deploy.txt`
4. Checks platform CLI prerequisites and authentication
5. Collects secrets from environment (`api_key_env`, tool auth env vars)
6. Builds Docker image → pushes to cloud registry → creates/updates cloud service
7. Prints the deployed endpoint URL

**HTTP API of deployed agent:**

```bash
# Single invocation
curl -X POST https://<endpoint>/invoke \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello", "thread_id": "default"}'

# Health check
curl https://<endpoint>/health
```

**Platform-specific resources:**

| Platform | Registry | Service |
|---|---|---|
| Azure | Azure Container Registry (ACR) | Container Apps |
| AWS | Elastic Container Registry (ECR) | App Runner |
| GCP | Artifact Registry | Cloud Run |

**`deploy` section in blueprint:**

```yaml
deploy:
  platform: azure             # default platform for abp deploy (overridable with --platform)

  azure:
    subscription_env: AZURE_SUBSCRIPTION_ID
    resource_group: "my-rg"
    location: "westeurope"
    acr_name: "myregistry"
    container_app_env: "my-env"
    min_replicas: 0
    max_replicas: 3

  aws:
    region: "eu-west-1"
    ecr_repo: "my-agent"
    service_name: "my-agent-service"   # optional, defaults to blueprint name

  gcp:
    project_env: GCP_PROJECT_ID
    region: "europe-west1"
    artifact_repo: "cloud-run-source-deploy"
    allow_unauthenticated: false
```

**Secret injection:** Secrets are collected automatically from the blueprint (`model_providers[*].api_key_env`, `tools[*].auth.*_env`) and read from your local environment at deploy time. Missing secrets produce a warning but do not block deployment.

### `abp run`

Generate a blueprint to a temp dir and run it locally — no manual `pip install` or directory setup needed:

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
| `--target` | `langgraph` | Target framework (only `langgraph` currently) |
| `--thread-id` | `default` | Conversation thread ID (enables memory across turns in REPL) |
| `--install` | `false` | Run `pip install -r requirements.txt` before executing |
| `--env` | `.env` | Path to a `.env` file to load before running |

**How it works:**

1. Validates and compiles the blueprint
2. Generates code to a temporary directory
3. Adds your current working directory to `PYTHONPATH` — `impl:` references resolve automatically
4. Executes in a subprocess using the current Python environment
5. Cleans up the temp dir on exit

**Warnings:** If any `function` tools have no `impl` and haven't been implemented, `abp run` prints a warning — the agent will still start but will raise `NotImplementedError` if those tools are called.

```
⚠  Warning: 1 tool(s) have no implementation and will raise NotImplementedError if called: send_email
```

### `abp inspect`

Visualize the agent graph as a Mermaid diagram:

```bash
abp inspect <blueprint.yml> [--output graph.md]
```

Paste the output into [mermaid.live](https://mermaid.live) to render it visually.

### `abp schema`

Export the full JSON Schema for IDE/editor integration:

```bash
abp schema [--format json|yaml] [--output blueprint-schema.json]
```

Use this to enable YAML validation and autocompletion in VS Code (via the [YAML extension](https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml)).

---

## Examples

The `examples/` directory contains ready-to-use blueprints:

### `examples/basic-chatbot.yml`

Single-agent chatbot. The simplest possible blueprint.

```bash
abp generate examples/basic-chatbot.yml --target langgraph
```

### `examples/customer-support.yml`

Three-agent system: a router classifies requests and dispatches to a billing specialist or a technical support agent.

```bash
abp generate examples/customer-support.yml --target langgraph
abp inspect examples/customer-support.yml
```

### `examples/research-team.yml`

Sequential pipeline: planner → researcher (with web search tool) → writer.

```bash
abp generate examples/research-team.yml --target langgraph
```

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
├── deployers/      # Cloud deployers (Phase 4 — coming soon)
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
- [x] MCP server configuration (`mcp_servers`) and `mcp` tool type
- [x] Model provider configuration (`model_providers`) for OpenAI, Anthropic, Google, Ollama, Azure, Bedrock
- [x] `impl` field for function tools — wire existing Python functions without stub overwrite risk
- [x] `abp run` — generate to temp dir and execute locally (single-shot + interactive REPL)
- [ ] CrewAI generator
- [ ] AutoGen generator
- [x] `abp deploy --platform azure|aws|gcp` — Docker → ACR/ECR/Artifact Registry → Container Apps/App Runner/Cloud Run
- [ ] VS Code extension
- [ ] PyPI publish

---

## License

MIT
