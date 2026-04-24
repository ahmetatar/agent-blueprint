# Tools

`agent-blueprint` supports four tool types. Define them in the `tools` section of your blueprint and reference them from agents via `tools: [tool_name]`.

## `function` — Python Function

Two modes are available:

### Without `impl` (stub generated)

The generator creates a placeholder you fill in:

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

### With `impl` (wires an existing function)

Point to any Python function in your codebase using a dotted import path. The generator produces an import + `tool()` wrapper — it never touches your implementation file:

```yaml
tools:
  classify_intent:
    type: function
    impl: "myapp.classifiers.classify_intent"
    description: "Classify customer intent"
    parameters:
      message:
        type: string
        required: true

  web_search:
    type: function
    impl: "myapp.tools.search.web_search"
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

## `api` — HTTP Endpoint

Code is generated automatically for API calls:

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

## `retrieval` — Vector Store

Retrieval is generic. `agent-blueprint` does not know Chroma, Qdrant, Pinecone,
or any other vector-store API. Define a top-level retriever with an `impl`
function, then expose it as a retrieval tool.

Your retriever implementation is called with keyword arguments:

```python
def search_support_docs(query: str, top_k: int, config: dict) -> list[dict] | str:
    ...
```

It may return a string, a list of strings, LangChain-style documents with
`page_content` and `metadata`, or dictionaries with `content`, `source`,
`score`, and `metadata`.

```yaml
retrievers:
  support_docs:
    impl: "myapp.retrieval.search_support_docs"
    config:
      index_name: "support-docs"

tools:
  search_kb:
    type: retrieval
    retriever: support_docs
    description: "Search support knowledge base"
    top_k: 5
```

Agents can either expose the retrieval tool for model-driven tool use:

```yaml
agents:
  assistant:
    tools: [search_kb]
```

or ask ABP to retrieve before the LLM call and inject the result as context:

```yaml
agents:
  assistant:
    rag:
      tool: search_kb
      mode: pre_context   # pre_context | tool_only | hybrid
      max_context_chars: 8000
```

## `mcp` — MCP Server Tool

References an MCP server defined in `mcp_servers`:

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
