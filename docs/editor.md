# Visual Editor (`abp editor`)

> **Status: phase E3a — editing plus action buttons.**
> The editor renders the blueprint as a live graph with validation/lint
> diagnostics; the YAML source is editable in place, nodes/edges can be
> added and removed on the canvas, node config (and the linked agent) is
> editable through schema-driven forms, and node positions persist across
> sessions. The Actions tab runs `abp test` / `run` / `gate` / `generate` /
> `doctor` in the background with live progress. Per-node live execution
> highlighting (E3b) comes next. See [abp-editor-plan.md](abp-editor-plan.md)
> for the full roadmap.

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
    top-level graph).
- **Config forms** — selecting a node opens the *Config* tab: the node's
  fields (per node type, plus its retry policy) and — for agent-backed
  nodes — the linked agent definition (model, system prompt, tools,
  temperature, …). Forms are driven by the blueprint JSON Schema
  (`GET /api/schema`), so new model fields appear without editor changes;
  anything too complex for a simple input says "edit in Source" instead of
  rendering a broken control. Clearing a field reverts it to its default
  (the key is removed from the YAML). *Apply* writes only the fields you
  changed, as targeted mutations.

  Canvas ops are strict: the mutated document is validated *before* the file
  is written, and an edit that would produce an invalid blueprint is rejected
  with the error shown on the canvas. Each op batch carries the file hash it
  was based on — if the file changed underneath (external editor, another
  tab), the edit is refused and the canvas refreshes instead.

## Actions (E3a)

The *Actions* tab runs the operational surfaces against the open blueprint —
the same logic modules the CLI uses, with results shaped for the panel
instead of the terminal:

- **Test** — opens a scenario picker (all harness scenarios pre-selected) and
  runs them like `abp test`; each scenario reports pass/fail live as it
  finishes, with failure details underneath. Failed traces land in
  `.abp/traces/` exactly as with the CLI.
- **Run…** — a one-shot `abp run` with an input message you type; stdout and
  stderr stream into the result view when the run finishes. The interactive
  REPL stays CLI-only, and blueprints that request a sandbox
  (`run.sandbox.enabled`) are refused with a hint to use `abp run` — the
  editor never silently skips the sandbox.
- **Gate** / **Update baseline** — `abp gate` against
  `.abp/gate-baseline.json`, with regressions/improvements listed.
  *Update baseline* asks for confirmation and refuses to write a red baseline,
  same as `--update-baseline`.
- **Generate** — writes the generated project next to the blueprint
  (`<name>-langgraph/`) and lists the files.
- **Doctor** — pre-generation diagnostics, rendered like the Issues panel.

One task runs at a time; starting a second is refused (`409`). A running
task shows a **Cancel** button — cancelling terminates the underlying
generated-project subprocess, so even a live-LLM scenario stops promptly.
Buttons that don't apply are disabled with a hint (no harness scenarios →
no Test; nothing to gate → no Gate).

### API surface

- `GET /api/health` — server liveness + version
- `GET /api/blueprint` — raw YAML, validation status, the graph view-model,
  lint findings with source positions, the action surface (scenario/suite
  ids, baseline presence), the saved canvas layout, and the file's content
  hash
- `GET /api/schema` — the blueprint JSON Schema (same as `abp schema`);
  drives the config forms
- `PUT /api/blueprint/yaml` — whole-file save from the source pane
  (parseable-YAML gate; broadcasts `file_changed` to other tabs)
- `POST /api/blueprint/ops` — canvas/config ops (`add_node`, `remove_node`,
  `add_edge`, `remove_edge`, `set_field`, `unset_field`) applied as targeted
  ruamel mutations; `409` when `base_hash` is stale, `422` when an op cannot
  apply or the result fails validation (nothing written)
- `PUT /api/layout` — persist canvas node positions to the layout sidecar
- `POST /api/actions/{test,run,gate,generate,doctor}` — start a background
  action task; `409` when one is already running
- `GET /api/tasks/current` — the running (or last finished) task, including
  accumulated progress — lets a reloaded tab resync
- `POST /api/tasks/current/cancel` — cancel the running task (terminates the
  generated-project subprocess)
- `WS /ws` — pushes `file_changed` when the blueprint changes on disk
  (`origin: disk`) or is saved through the editor (`origin: save`); the
  watcher suppresses the disk echo of the editor's own writes. Task events
  ride the same channel: `task_started`, `task_progress` (per
  scenario/suite), `task_done`

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
