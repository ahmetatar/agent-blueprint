# MCP Servers

Defines MCP (Model Context Protocol) server connections. Tools of type `mcp` reference these by name.

> **Status:** `mcp_servers` and `mcp` tools are validated at the schema level
> (server references are cross-checked), but **code generation for MCP tools is
> not implemented yet**. `abp generate` fails with a clear error and `abp doctor`
> reports a `target-incompatible-feature` finding when a blueprint uses them.

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
