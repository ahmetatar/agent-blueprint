# Memory & Checkpointing

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
