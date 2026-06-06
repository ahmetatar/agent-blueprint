# Visual Editor (`abp editor`)

> **Status: phase E0 — skeleton.** The command works and serves a placeholder
> page; the visual canvas, editing, and action buttons arrive in later phases.
> See [abp-editor-plan.md](abp-editor-plan.md) for the full roadmap.

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

## What E0 ships

- `GET /api/health` — server liveness + version
- `GET /api/blueprint` — raw YAML plus load/validation status
- the embedded UI: a placeholder page showing the blueprint name, validity, and
  source — proving the packaging spine (a React app served from inside the
  `abp` wheel, no Node required at install time)

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
