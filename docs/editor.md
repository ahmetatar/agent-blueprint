# Visual Editor (`abp editor`)

> **Status: phase E2 (in progress) — source + canvas editing.**
> The editor renders the blueprint as a live graph with validation/lint
> diagnostics; the YAML source is editable in place, nodes/edges can be
> added and removed on the canvas, and node positions persist across
> sessions. Schema-driven config forms (E2c) and action buttons (E3) come
> next. See [abp-editor-plan.md](abp-editor-plan.md) for the full roadmap.

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

## What the editor shows

- **Canvas** — the agent graph, auto-laid-out (ELK, top-down): one card per
  node with its type, resolved `provider/model`, and tool count. Conditional
  edges carry their condition as a label; the default route is dashed.
  Supervisors show dashed delegation/return edges to their workers plus the
  `on finish` route; parallel nodes show fan-out/fan-in edges; subgraphs
  render as nested groups with their own entry and END markers. START/END
  are explicit terminals, and a minimap helps with larger graphs.
- **Issues panel** — the validation error (if the file doesn't validate) and
  all lint findings, each clickable through to its source line.
- **Source panel** — the raw YAML in an editable Monaco pane with lint
  findings as inline markers.
- **Live reload** — the server watches the file; saving from any external
  editor updates the canvas, issues, and source in place (no browser refresh).

## Editing (E2)

- **Source editing** — the Source pane is a real editor: type, then *Save*
  (or `Cmd/Ctrl+S`). Saves that are not parseable YAML are rejected and the
  file is left untouched; content that parses but fails blueprint validation
  is still written (exactly as an external editor could), with the validation
  error surfacing in the Issues panel. While you have unsaved edits, an
  external change to the file raises a conflict banner — load the file
  version, or keep typing and Save overwrites (last-writer-wins).
- **Layout persistence** — node positions you drag are saved (debounced) to
  `.abp/editor-layout.json` next to the blueprint, keyed by file stem, and
  restored on the next session. The sidecar is editor-private convenience
  state: never required, never validated, safe to delete (you just fall back
  to auto-layout). Coordinates deliberately stay out of the blueprint schema.
- **Canvas editing** — structural edits write back to the YAML as *targeted
  mutations* (set this key, remove this list item) — never a wholesale
  re-serialization — so comments, key order, and quoting on untouched lines
  survive byte-for-byte:
  - **Draw an edge** by dragging between node handles (top = incoming,
    bottom = outgoing). A second outgoing edge converts a scalar `to:` into
    the conditional list form, keeping the original target as the default
    route. Edges cannot cross a subgraph boundary.
  - **Delete** a selected edge or node (`Backspace`). Deleting a node also
    removes every edge that references it. Synthetic edges (START→entry,
    supervisor delegation/return, parallel fan-out) are display-only.
  - **Add a node** via the *+ Node* button (agent or function node;
    top-level graph). Richer config forms are phase E2c.

  Canvas ops are strict: the mutated document is validated *before* the file
  is written, and an edit that would produce an invalid blueprint is rejected
  with the error shown on the canvas. Each op batch carries the file hash it
  was based on — if the file changed underneath (external editor, another
  tab), the edit is refused and the canvas refreshes instead.

### API surface

- `GET /api/health` — server liveness + version
- `GET /api/blueprint` — raw YAML, validation status, the graph view-model,
  lint findings with source positions, the saved canvas layout, and the
  file's content hash
- `PUT /api/blueprint/yaml` — whole-file save from the source pane
  (parseable-YAML gate; broadcasts `file_changed` to other tabs)
- `POST /api/blueprint/ops` — canvas ops (`add_node`, `remove_node`,
  `add_edge`, `remove_edge`, `set_field`) applied as targeted ruamel
  mutations; `409` when `base_hash` is stale, `422` when an op cannot apply
  or the result fails validation (nothing written)
- `PUT /api/layout` — persist canvas node positions to the layout sidecar
- `WS /ws` — pushes `file_changed` when the blueprint changes on disk
  (`origin: disk`) or is saved through the editor (`origin: save`); the
  watcher suppresses the disk echo of the editor's own writes

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
