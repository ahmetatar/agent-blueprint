# Scope

ABP's value is the blueprint spec, the runtime guarantees compiled from it, and
the test and gate tooling around them. Development effort goes to that core.
Everything else is kept working but frozen until there is demand for it.

## Tiers

| Tier | Meaning |
|---|---|
| **Core** | Actively developed. New features land here. Breaking changes follow the schema-compatibility rules in `CONTRIBUTING.md`. |
| **Experimental** | Shipped and tested, but frozen: bug fixes are accepted, new features are not. Can be moved out of the package or removed in a future minor release, with a breaking-change note and migration guidance. |
| **Not implemented** | Accepted by the CLI or schema but not functional. Most fail loudly; exceptions are noted below. |

## Commands

| Command | Tier | Notes |
|---|---|---|
| `abp validate`, `abp schema`, `abp init` | Core | |
| `abp lint`, `abp fix`, `abp doctor`, `abp inspect` | Core | |
| `abp generate`, `abp run` (incl. `--sandbox`) | Core | LangGraph target |
| `abp test`, `abp eval`, `abp gate`, `abp traces` | Core | |
| `abp deploy` | Experimental | Docker/Podman (local), AWS App Runner, Azure Container Apps, GCP Cloud Run. Tested against recorded CLI calls, not live clouds. See [deploy.md](deploy.md). |
| `abp package` | Experimental | pipx-installable CLI packaging. See [cli-packaging.md](cli-packaging.md). |
| `abp github` | Convenience | Opens the repository in a browser. |

## Generator targets

| Target | Tier | Notes |
|---|---|---|
| `langgraph` | Core | Every documented feature, contract and policy |
| `plain` | Experimental | Minimal single-node Python output |
| `crewai` | Not implemented | `abp generate --target crewai` fails; `abp doctor` flags it |

## Blueprint features

| Feature | Tier | Notes |
|---|---|---|
| MCP tool generation (`mcp_servers`) | Not implemented | `abp generate` fails with the tool names; `abp doctor` reports it. See [mcp-servers.md](mcp-servers.md). |
| `agents.*.memory.type: summary \| vector` | Not implemented | Fails at generation; use `conversation_buffer` |
| Plain `type: function` nodes | Not implemented | Generate a stub that returns `{}`; not flagged by lint or doctor yet |

Proposals that are not implemented live in [proposals/](proposals/).
