# Visual Editor (`abp editor`)

> **Status: phase E3 complete — editing, actions, live execution, deploy.**
> The editor renders the blueprint as a live graph with validation/lint
> diagnostics; the YAML source is editable in place, nodes/edges can be
> added and removed on the canvas, node config (and the linked agent) is
> editable through schema-driven forms, and node positions persist across
> sessions. The Actions tab runs `abp test` / `run` / `gate` / `generate` /
> `doctor` in the background with live progress, canvas nodes light up as
> the run executes them, and a confirm-gated Deploy builds and starts the
> agent as a local container (docker/podman). See
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
  - **Select an edge** by clicking it (the hit area is generous, not just
    the 1-px path); the selection shows endpoint handles and a floating
    toolbar naming it (`edge router → worker`) with a Delete button.
  - **Reconnect an edge** by dragging an endpoint onto another node. Moving
    the target end retargets the entry *in place* — it keeps its position in
    the `to:` list (evaluation order is routing semantics when conditions
    overlap), its condition, and its comments. Moving the source end
    relocates the entry to the new source's edge.
  - **Edit an edge's condition** in the selection toolbar: type a condition
    (validated live by the same expression parser the generator uses — an
    invalid condition blocks *Apply*), or tick *default (unconditional)* to
    make it the fallback route. Declared `state` fields appear as chips that
    insert `state.<field>` at the cursor. *Apply* writes a single in-place
    edit — the entry keeps its list position, and editing only the condition
    value preserves the target's quoting and comments.
  - **Delete** a selected edge or node (toolbar button or `Backspace`).
    Deleting a node also removes every edge that references it. Synthetic
    edges (START→entry, supervisor delegation/return, parallel fan-out) are
    display-only.
  - **Add a node** via the *+ Node* button (top-level graph). The dialog
    covers every node type — agent (pick an agent), function (name an
    action), handoff (channel + optional message template), parallel (tick
    branches + a join node), subgraph (pick a defined subgraph + key→value
    `input_map`/`output_map` rows), and supervisor (pick an agent + tick
    workers). References are pickers drawn from the blueprint, and the result
    is strict-validated before it's written, so an incomplete or illegal
    combination is rejected with the reason shown rather than saved. (Wiring
    supervisor workers / parallel branches by *drawing* on the canvas is a
    later phase.)
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
- **Undo / redo** — the *↶ Undo* / *↷ Redo* buttons in the header (or
  `⌘/Ctrl+Z` and `⌘/Ctrl+Shift+Z` / `⌘/Ctrl+Y`) step through your edits this
  session. The history is a stack of whole-file snapshots, so a single model
  covers every kind of edit — canvas op, config form, *and* source save — and
  restoring one brings back its comments and quoting byte-for-byte. While the
  Source pane has unsaved changes the shortcuts defer to Monaco's own undo;
  external edits on disk don't clear the history (it stays your local edits).

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
- **Deploy…** — builds the agent image and starts it as a **local container**
  (docker or podman; the blueprint's `deploy.platform` pre-selects the
  engine when it names a local one). Deploy is outward-facing, so it always
  sits behind an explicit confirmation; each container command shows up in
  the progress view as it runs, and the result links the
  `http://localhost:<port>` endpoint. Cloud platforms (azure/aws/gcp) stay
  CLI-only — `abp deploy` — because credential/region forms are out of the
  editor's scope; if the blueprint targets one, the editor says so and
  offers the local-container deploy instead. Secrets are injected from the
  editor process's environment; missing ones are reported **by name only**
  (values never reach the browser).

One task runs at a time; starting a second is refused (`409`). A running
task shows a **Cancel** button — cancelling terminates the underlying
generated-project subprocess, so even a live-LLM scenario stops promptly.
Buttons that don't apply are disabled with a hint (no harness scenarios →
no Test; nothing to gate → no Gate).

### Live execution view (E3b)

While a Test / Run… / Gate task executes, the canvas shows the run live:
nodes pulse blue while running, then keep a green (finished) or red (an
error event — retry exhausted, policy violation, tool failure) ring. The
highlights survive task completion so you can read the path the run took;
they clear when the next task (or the next scenario within a task) starts.

Under the hood the generated project's trace observer registry streams each
event as one JSON line to a file named in `ABP_TRACE_STREAM_FILE`; the
editor tails it and forwards events over `/ws` as `task_trace`. The JSON
trace manifest is byte-identical with or without the stream — goldens,
harness diffs, and gate baselines are unaffected — and only hashes are
streamed, never message content. Subgraph nodes highlight inside their
group (trace events carry flattened runtime ids; the view-model maps them
back to canvas nodes).

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
  `add_edge`, `remove_edge`, `retarget_edge`, `set_edge_condition`,
  `set_field`, `unset_field`) applied as targeted ruamel mutations; `409`
  when `base_hash` is stale, `422` when an op cannot apply or the result
  fails validation (nothing written)
- `POST /api/expression/validate` — check an edge condition with the
  generator's expression parser (`{valid, error, referenced_fields}`);
  read-only, powers the live condition editor
- `PUT /api/layout` — persist canvas node positions to the layout sidecar
- `POST /api/actions/{test,run,gate,generate,doctor,deploy}` — start a
  background action task; `409` when one is already running
- `GET /api/tasks/current` — the running (or last finished) task, including
  accumulated progress — lets a reloaded tab resync
- `POST /api/tasks/current/cancel` — cancel the running task (terminates the
  generated-project subprocess)
- `WS /ws` — pushes `file_changed` when the blueprint changes on disk
  (`origin: disk`) or is saved through the editor (`origin: save`); the
  watcher suppresses the disk echo of the editor's own writes. Task events
  ride the same channel: `task_started`, `task_progress` (per
  scenario/suite), `task_trace` (per generated-project trace event, drives
  the live node highlights), `task_done`

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
