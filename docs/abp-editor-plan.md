# `abp editor` — Visual Blueprint Editor (Plan)

Status: **PROPOSED** (not started)
Scope: a local, browser-based visual editor for blueprints — n8n-style canvas, two-way
YAML sync, and one-click access to the existing operational surfaces (validate, lint,
test, run, gate, deploy).

---

## 1. Goal

Filling a YAML file by hand is the highest-friction part of using ABP. The editor removes
that friction without changing what ABP *is*:

```
abp editor blueprint.yml
```

opens a browser app where the user designs the agent graph visually. Every change the UI
makes is written back to the YAML file; every change made to the YAML file (any external
editor) is reflected live in the UI. The canvas is a **view**, the YAML file stays the
**single source of truth**.

### Non-goals

- No hosted/multi-user service. Localhost only, one blueprint per session.
- No new persistence format. The editor reads/writes the same `*.yml` the CLI consumes.
- No editor-only blueprint features. If the canvas can express it, the schema already can.
- No replacement of the CLI. Every editor action shells through the same logic modules
  the CLI uses; the CLI remains fully sufficient.

## 2. Why ABP is unusually well-positioned

1. **Thin-CLI architecture.** All logic lives in importable modules (`linting.py`,
   `harness_runner.py`, `gating.py`, `runners/`, `deployers/`, `doctoring.py`); the CLI
   files are thin wrappers. The editor backend is just *another* thin wrapper: an HTTP/WS
   layer over the same modules. Zero logic migration.
2. **`abp schema` already exists.** The JSON Schema drives the UI's node-config forms
   (fields, enums, required/optional, descriptions) instead of hand-built panels. New
   blueprint features appear in the editor "for free" once the Pydantic model lands.
3. **`ruamel.yaml` is already a dependency.** Comment- and order-preserving round-trip is
   the technical foundation of two-way sync. Most projects bolt this on late and lose
   user comments; we get it from day one.
4. **Observer registry (PR #16).** Harness/run trace events can stream over a WebSocket
   with a thin bridge — enabling a live execution view (nodes lighting up as they run)
   without touching the JSON manifest or goldens.

## 3. Architecture

```
abp editor blueprint.yml
  → FastAPI + uvicorn on 127.0.0.1:<random-port>, then open browser
      ├─ static:  built frontend (shipped inside the wheel; no Node at install time)
      ├─ REST:    /api/* — wraps validate / lint / fix / schema / generate / actions
      └─ WS:      /ws   — file-change push, live task events (trace stream)

  File watch: watchfiles → YAML changed on disk → WS push → UI re-renders
  UI edit    → PUT/POST  → server mutates the ruamel document → writes file
                           (writer records a content hash; the watcher suppresses
                            the echo for that hash)
```

### 3.1 Single source of truth & comment preservation

The server holds the blueprint as a **ruamel `CommentedMap`**, not a Pydantic model.
UI edits are applied as *targeted mutations* to that document (set this key, append this
list item, delete this node) — never "re-serialize the Pydantic model", which would
destroy comments, key order, and anchors. Pydantic validation runs on every mutation
(parse the mutated document → `BlueprintSpec`); invalid edits are rejected with the
validation error surfaced in the UI, and the file is not written.

The Monaco "View Source" pane edits raw text. Saving from Monaco replaces the document
wholesale (it *is* the source); saving from the canvas goes through targeted mutations.

### 3.2 Node positions: layout sidecar

Canvas coordinates must NOT pollute the blueprint schema. Positions live in a sidecar:

```
.abp/editor-layout.json      { "<blueprint-stem>": { "<node-id>": {"x": 340, "y": 120}, ... } }
```

- Missing/partial layout → automatic hierarchical layout (ELK.js) on load. Hand-written
  blueprints open looking good with zero metadata.
- The sidecar is editor-private state, like `.abp/traces/` and `.abp/gate-baseline.json`.
  It is never required, never validated, safe to delete.

### 3.3 API surface (sketch)

| Endpoint | Wraps | Notes |
|---|---|---|
| `GET /api/blueprint` | yaml_loader + compiler | raw YAML + a UI view-model (nodes, edges, conditions) + layout |
| `PUT /api/blueprint/yaml` | ruamel + BlueprintSpec | full-text save from Monaco; validate-before-write |
| `POST /api/blueprint/ops` | ruamel mutations | canvas ops: `add_node`, `remove_node`, `set_field`, `add_edge`, `remove_edge`, ... |
| `GET /api/schema` | `abp schema` | drives config forms |
| `POST /api/validate` / `POST /api/lint` / `POST /api/fix` | existing modules | lint findings also surface as Monaco diagnostics |
| `PUT /api/layout` | sidecar | debounced position saves |
| `POST /api/actions/{test,run,gate,generate,deploy,doctor}` | existing modules | returns a task id; events stream over WS |
| `WS /ws` | watchfiles + observer bridge | `file_changed`, `validation`, `task_event`, `task_done` |

The view-model is computed server-side from `BlueprintSpec` (reusing what `abp inspect`
already extracts), so the frontend never re-implements YAML semantics — subgraphs,
supervisor workers, parallel branches arrive pre-digested.

### 3.4 Action execution

Actions run in a worker thread per task (same process), reusing `harness_runner`,
`runners.local`/`sandbox`, `gating`, `deployers`. Trace events are tee'd to the WS via a
registered observer; JSON manifests stay byte-identical (the PR #16 guarantee). One task
at a time per session — a `409` if a task is already running keeps the first version
simple.

## 4. Frontend stack

Decision: **React + React Flow** (`@xyflow/react`). This is the de-facto standard for
exactly this product category — Langflow, Flowise, Dify, and LangGraph Studio are all
built on it. (n8n itself uses Vue Flow, the same family's Vue port; viable, but the
React Flow ecosystem is significantly deeper.)

| Layer | Choice | Why |
|---|---|---|
| Canvas | React Flow | custom nodes/edges, sub-flows (→ subgraphs), minimap, handle validation |
| Auto-layout | ELK.js | hierarchical layout handles subgraph nesting; dagre as fallback option |
| State | Zustand | React Flow's own recommendation; minimal |
| Source pane | Monaco | YAML editing + lint findings as inline diagnostics |
| UI kit | shadcn/ui + Tailwind | config panels, dialogs; react-hook-form + zod for schema-driven forms |
| Build | Vite | outputs `dist/` → embedded as `agent_blueprint/editor/static/` |

Mapping ABP concepts to canvas:

- node `type` → custom React Flow node component (agent / supervisor / parallel /
  handoff / subgraph / tool-ish nodes each get a distinct visual)
- subgraph → React Flow sub-flow (parent/group node), collapsible
- conditional edges → edge labels showing the condition; `default` edge styled distinctly
- lint findings → badges on the offending node/edge + Monaco markers

## 5. Packaging & repo layout

```
frontend/                      # Vite + React app (repo root, not inside src/)
src/agent_blueprint/editor/    # server.py, ops.py, viewmodel.py, watch bridge
src/agent_blueprint/editor/static/   # build output — NOT committed; embedded at build time
```

- **Python deps**: new optional extra `editor = ["fastapi", "uvicorn", "watchfiles"]`,
  same pattern as the `deploy-*` extras. `abp editor` without the extra prints an
  actionable install hint (mirrors deployer behavior).
- **Wheel embedding**: a hatch build hook runs `npm ci && npm run build` and copies
  `frontend/dist/` into the wheel. PyPI users never need Node.
- **Editable installs without built assets**: `abp editor` detects the missing static dir
  and errors with the `cd frontend && npm run build` hint. `abp editor --dev` proxies to
  the Vite dev server for frontend work.
- **CI**: a new job (Node 22) runs `npm ci`, lint, `npm run build`, and the frontend unit
  tests; the build job gains a Node setup step so `python -m build` can run the hook.
  Python-side coverage floor is unaffected (editor server code is tested with pytest +
  httpx/TestClient like any other module).

## 6. Phases (PR-sized)

### Phase E0 — server skeleton + packaging spine
FastAPI app behind `abp editor`, `editor` extra, hatch build hook, CI node job, a
placeholder frontend that renders "hello blueprint". Proves the packaging story
end-to-end *before* any canvas work.

### Phase E1 — read-only visualizer
- Canvas renders the compiled graph (custom nodes, condition-labeled edges, subgraph
  groups), ELK auto-layout, minimap.
- Side panel: validate + lint results; Monaco source pane (read-only) with lint markers.
- `watchfiles` → live reload on external YAML edits.
- **Standalone value**: "show me my blueprint" is a real need even if editing never ships.

### Phase E2 — editing + two-way sync
- Canvas mutations (add/remove node, draw edge, edit config via schema-driven forms) →
  targeted ruamel ops → validate → write file (comment-preserving).
- Monaco becomes editable (full-text save path); echo suppression between writer and
  watcher; layout sidecar persistence.
- Conflict rule: last-writer-wins on whole-file saves; canvas ops re-validate against the
  current document and surface a "file changed underneath" prompt on mismatch.

### Phase E3 — actions + live execution view
- Run / `abp test` / gate / generate / doctor buttons; trace events stream over WS;
  nodes highlight as the corresponding `node_started`/`node_finished` events arrive.
- Harness scenario picker; failed-assertion results pinned to nodes.
- Deploy behind an explicit confirm (it is outward-facing).

Each phase is independently shippable and lands as its own PR (or small PR series),
per the repo's no-mixed-PRs rule.

## 7. Risks & honest costs

- **Maintenance weight.** The repo is ~6k lines of Python with one maintainer; a frontend
  codebase plus Node toolchain roughly doubles the surface. The phase structure is the
  mitigation: E1 alone is useful, and we can stop at any phase boundary.
- **Schema drift.** Mitigated by deriving forms from `abp schema` and the view-model from
  `BlueprintSpec` — but custom node *visuals* (supervisor, parallel) still need manual
  updates when those models change. Keep the custom-visual set small.
- **Round-trip edge cases.** ruamel handles comments/order, but anchors/merge-keys under
  mutation need targeted tests. The mutation layer (`ops.py`) gets its own round-trip
  test suite (load → op → dump → byte-level expectations on untouched regions).
- **Security posture.** Bind 127.0.0.1 only; no auth in v1 (same trust model as
  `jupyter notebook`'s pre-token era is *not* acceptable long-term — add a random
  URL token from day one, it is cheap).

## 8. Open questions

1. **Sequencing vs. examples ladder.** RESOLVED (June 2026): the editor takes priority;
   the examples portfolio (L1–L8) is deprioritized for now. The "open an example"
   gallery becomes a later editor enhancement once examples land.
2. **`inspect` reuse vs. dedicated view-model.** Decide during E1 whether
   `inspect_cmd`'s extraction is reusable as-is or the editor needs its own
   `viewmodel.py` (likely the latter; inspect is presentation-oriented).
3. **Frontend testing depth.** Minimum: vitest unit tests for the ops/view-model mapping.
   Playwright e2e is valuable but heavy for CI — decide at E2.
4. **Multi-blueprint workspaces** (open a directory, switch between files) — explicitly
   deferred; single file per session in all three phases.
