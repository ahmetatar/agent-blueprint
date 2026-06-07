# Visual Editor (`abp editor`)

> **Status: phase E1 — read-only visualizer.** The editor renders the
> blueprint as a live graph with validation/lint diagnostics; editing and
> action buttons arrive in later phases. See
> [abp-editor-plan.md](abp-editor-plan.md) for the full roadmap.

```bash
pip install "agent-blueprint[editor]"
abp editor my-blueprint.yml
```

This starts a local web server on `127.0.0.1` (a random free port unless
`--port` is given) and opens your browser. The URL carries a per-session random
token; requests without it are rejected, so other local users cannot reach the
server. Press `Ctrl+C` to stop.

The YAML file stays the single source of truth — the editor is a live view over
it, never a separate store.

| Option | Default | Meaning |
| --- | --- | --- |
| `--port`, `-p` | random free port | port to bind on 127.0.0.1 |
| `--open/--no-open` | `--open` | open the browser automatically |

## What the editor shows (E1)

- **Canvas** — the agent graph, auto-laid-out (ELK, top-down): one card per
  node with its type, resolved `provider/model`, and tool count. Conditional
  edges carry their condition as a label; the default route is dashed.
  Supervisors show dashed delegation/return edges to their workers plus the
  `on finish` route; parallel nodes show fan-out/fan-in edges; subgraphs
  render as nested groups with their own entry and END markers. START/END
  are explicit terminals, and a minimap helps with larger graphs.
- **Issues panel** — the validation error (if the file doesn't validate) and
  all lint findings, each clickable through to its source line.
- **Source panel** — the raw YAML in a read-only Monaco pane with lint
  findings as inline markers.
- **Live reload** — the server watches the file; saving from any external
  editor updates the canvas, issues, and source in place (no browser refresh).

The canvas is a *view*: nodes can be dragged around for inspection, but
nothing writes back to the file yet — that is phase E2.

### API surface

- `GET /api/health` — server liveness + version
- `GET /api/blueprint` — raw YAML, validation status, the graph view-model,
  and lint findings with source positions
- `WS /ws` — pushes `file_changed` when the blueprint changes on disk

## Working on the frontend (contributors)

The frontend lives in `frontend/` (Vite + React). Built assets are not
committed; published wheels and sdists embed them via a hatch build hook.

```bash
# editable installs need a one-time build for `abp editor` to serve the UI:
cd frontend && npm install && npm run build

# frontend development with hot reload:
abp editor my-blueprint.yml --dev   # API only, fixed port 8321, no token
cd frontend && npm run dev          # Vite dev server proxies /api and /ws
```
