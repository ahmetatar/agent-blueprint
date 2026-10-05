# Memory & Checkpointing

> This page covers the top-level `memory:` block (checkpointing). For per-agent context limits (`agents.*.memory`), see [Agent context limits](#agent-context-limits).

Configures LangGraph checkpointing — how conversation state and turn history are persisted across invocations. The `thread_id` passed to `run()` identifies the conversation; the checkpointer stores state per thread.

## Configuration

```yaml
memory:
  backend: in_memory           # in_memory | sqlite | postgres | redis
  connection_string_env: REDIS_URL
  checkpoint_every: node       # node | edge | manual
```

## Backends

| Backend | Persistence | Use case |
|---|---|---|
| `in_memory` | Process lifetime only | Development, stateless APIs |
| `sqlite` | Local file | Local dev with persistence, single-process |
| `postgres` | External DB | Production, multi-instance |
| `redis` | External cache | Production, low-latency, multi-instance |

### `in_memory`

No extra config needed:

```yaml
memory:
  backend: in_memory
```

### `sqlite`

Stores state in a local `.db` file. No server required:

```yaml
memory:
  backend: sqlite
  connection_string_env: SQLITE_DB_PATH   # optional; defaults to <blueprint-name>.db
```

```bash
# .env
SQLITE_DB_PATH=./my-agent.db
```

### `redis`

Connect to any Redis instance:

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

Required packages (added automatically to generated `requirements.txt`): `langgraph-checkpoint-redis`, `redis`

### `postgres`

Connect via standard PostgreSQL URL:

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

## Agent context limits

`agents.*.memory` is a *context-view policy*: it limits what the LLM is shown, not what is stored. The checkpointed `messages` state, replay, and harness results always keep the full history.

```yaml
agents:
  support:
    model: gpt-4o
    memory:
      type: conversation_buffer
      max_messages: 20     # keep at most the 20 most recent messages
      max_tokens: 6000     # approximate token budget for the whole LLM input
```

Applied just before every LLM call of that agent (including each step of a tool loop):

- Oldest messages are dropped first; the most recent message is always kept.
- System messages (system prompt, RAG context) are never dropped. They do not count toward `max_messages` but do count toward `max_tokens`.
- A tool call and its tool results are dropped together, and the window never opens on an orphaned tool result or assistant reply when a user turn is still in range.
- `max_tokens` uses a deterministic ~4 characters/token estimate (no tokenizer dependency), so mock/replay runs are reproducible.
- When messages are dropped, a `context_trimmed` trace event is emitted (`dropped_messages`, `kept_messages`, the configured limits; no content).

### Tool-output compaction

Tool results can dominate a long tool loop. Two optional limits shrink them in the LLM view only (the checkpointed `messages` keep the full result):

```yaml
memory:
  max_tool_result_chars: 4000     # longer results keep head + tail with an "omitted" marker
  keep_recent_tool_results: 3     # older results become a stub: size + sha256 prefix
```

- Compaction runs before the `max_messages` / `max_tokens` window, so a compacted result may keep a tool exchange that would otherwise be evicted.
- The tool-call / tool-result pairing is preserved (only the content changes); a stub or marker is never used when it would be larger than the original.
- A `context_compacted` trace event records `compacted_results`, `chars_saved` and the configured limits (sizes only, no content). It is emitted on each LLM call that compacts something, so a tool loop can emit it repeatedly.
- Both values must be positive integers. LLM-based summarization of tool output is not implemented.

`type: summary` and `type: vector` are **not implemented yet**: `abp generate` fails and `abp doctor` reports an error instead of silently ignoring them. `max_messages` / `max_tokens` must be positive integers.
