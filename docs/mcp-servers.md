# MCP Servers

Defines MCP (Model Context Protocol) server connections. Tools of type `mcp` reference these by name.

## Configuration

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

## Transport Reference

| Transport | Required fields | Optional fields |
|---|---|---|
| `sse` / `http` | `url` | `headers` |
| `stdio` | `command` | `args`, `env` |
