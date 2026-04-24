# Retrieval-Augmented Generation

`agent-blueprint` supports generic retrieval-augmented generation (RAG) without
knowing any vector-store provider APIs.

ABP does not contain Chroma, Qdrant, Pinecone, Weaviate, Elasticsearch, or
database-specific logic. Retrieval infrastructure stays in user code. ABP only
defines the contract, wires the retriever into generated LangGraph tools, and
optionally injects retrieved context before an LLM call.

## Concepts

RAG is modeled with three layers:

1. `retrievers`: reusable retrieval resources backed by user code.
2. `tools`: LLM-callable retrieval tools that point to a retriever.
3. `agents[*].rag`: optional automatic context injection before the LLM call.

This keeps provider details outside ABP while still making RAG declarative in
the blueprint.

## Retriever Contract

A retriever is a top-level resource:

```yaml
retrievers:
  support_docs:
    impl: "myapp.retrieval.search_support_docs"
    description: "Support knowledge base retriever"
    config:
      index_name: "support-docs"
```

The `impl` function is imported by the generated project and called with keyword
arguments:

```python
def search_support_docs(query: str, top_k: int, config: dict):
    ...
```

The retriever may use any backend internally. For example, it can query Qdrant,
Chroma, a SQL full-text index, an HTTP RAG service, or a local file index.

Supported return shapes:

- `str`
- `list[str]`
- `dict` with a `chunks` list
- dictionaries with `content`, `page_content`, `text`, `source`, `score`, or `metadata`
- LangChain-style document objects with `page_content` and `metadata`

ABP formats the result into plain context text before passing it to the LLM.

## Retrieval Tool

Expose a retriever as a tool:

```yaml
tools:
  search_kb:
    type: retrieval
    retriever: support_docs
    description: "Search support knowledge base"
    top_k: 5
```

The generated tool accepts a single `query: str` argument and calls:

```python
retriever_impl(query=query, top_k=5, config={...})
```

## Agent Modes

### Tool Only

The model decides when to call the retrieval tool.

```yaml
agents:
  assistant:
    model: "openai/gpt-4o"
    tools: [search_kb]
```

or explicitly:

```yaml
agents:
  assistant:
    rag:
      tool: search_kb
      mode: tool_only
```

Use this when retrieval is optional and the model should decide whether the
question needs external knowledge.

### Context Only

ABP retrieves before the LLM call and injects the result as an additional system
context message. The retrieval tool is not exposed to the model.

```yaml
agents:
  assistant:
    model: "openai/gpt-4o"
    system_prompt: |
      Answer using the retrieved context when it is relevant.
    rag:
      tool: search_kb
      mode: context_only
      max_context_chars: 8000
```

Use this when the agent should consistently ground answers in a knowledge base.

### Hybrid

ABP injects initial retrieved context and also exposes the retrieval tool to the
model for follow-up searches.

```yaml
agents:
  assistant:
    rag:
      tool: search_kb
      mode: hybrid
      max_context_chars: 8000
```

Use this when the first retrieval pass is useful but the model may need to refine
the query.

## Full Example

```yaml
blueprint:
  name: "support-rag-agent"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

retrievers:
  support_docs:
    impl: "myapp.retrieval.search_support_docs"
    config:
      index_name: "support-docs"

tools:
  search_kb:
    type: retrieval
    retriever: support_docs
    description: "Search support documentation"
    top_k: 5

agents:
  assistant:
    model: "openai/gpt-4o"
    system_prompt: |
      Answer clearly. Use retrieved context when it is relevant.
    rag:
      tool: search_kb
      mode: context_only
      max_context_chars: 8000

graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
```

Example retriever implementation:

```python
def search_support_docs(query: str, top_k: int, config: dict) -> list[dict]:
    index_name = config["index_name"]
    results = query_my_vector_store(index_name=index_name, query=query, limit=top_k)
    return [
        {
            "content": item.text,
            "source": item.metadata.get("source"),
            "score": item.score,
            "metadata": item.metadata,
        }
        for item in results
    ]
```

## Design Notes

RAG is intentionally split from concrete vector-store providers. This avoids
locking ABP to one retrieval stack and keeps generated code stable.

Provider-specific concerns belong in the retriever implementation:

- connection strings and credentials
- embedding model setup
- collection/index names
- filters and metadata logic
- reranking
- hybrid keyword/vector search
- chunk formatting policy beyond ABP's generic formatter

ABP owns only the orchestration contract: which retriever to call, how many
chunks to request, whether the model can call retrieval as a tool, and whether
retrieved context is injected before the LLM call.
