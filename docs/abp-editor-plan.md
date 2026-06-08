# `abp editor` — Visual Blueprint Editor (Plan)

Status: **ALL PLANNED PHASES SHIPPED** — E0 (skeleton), E1 (read-only
visualizer), E2 (E2a: editable source pane + layout sidecar; E2b: canvas
ops; E2c: schema-driven config forms), and E3 (E3a: action buttons —
background task runner with cancel, scenario picker, per-scenario progress
over WS; E3b: live trace stream + canvas node highlight; E3c: confirm-gated
local-container deploy). Candidate follow-ups live in §6 under E3c.
User-facing docs: [editor.md](editor.md).
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
| UI kit | hand-rolled CSS + a small schema-driven form resolver | REVISED at E2c (June 2026): shadcn/ui + Tailwind + react-hook-form + zod was judged too heavy for a single-maintainer repo — the schema → field-spec resolver (`panel/schemaForm.ts`) plus plain inputs covers the config forms; anything it can't render degrades to a read-only "edit in Source" view |
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

Split into a 3-PR series (June 2026, user-approved), mirroring E2:

- **E3a — task backend + action buttons (SHIPPED).** `editor/tasks.py`: a
  single-slot background task runner (worker thread, one task at a time,
  `409` when busy) over the same logic modules the CLI uses; actions
  test / run / gate / generate / doctor. Cancellation terminates the
  generated-project subprocess via a new `process_hook` threaded through
  `LocalRunner` → `run_harness_scenario` → `run_eval_suite(s)`. Per-scenario
  / per-suite progress streams over the existing `/ws` channel
  (`task_started` / `task_progress` / `task_done`) — this level of progress
  needs no subprocess bridge because the harness loop runs in the editor
  process. Frontend: Actions tab with scenario picker, run-input form,
  gate/update-baseline (confirm-gated), live progress, per-action results,
  Cancel. One-shot run only (REPL stays CLI-only); blueprints with
  `run.sandbox.enabled` are refused rather than silently run unsandboxed.
- **E3b — live trace stream + node highlight (SHIPPED).** The observer
  registry lives in the *generated* process, and the runner executes it as
  a subprocess — so node-level events need a bridge: an env-activated
  stream observer in `_abp_trace.py.j2` (`ABP_TRACE_STREAM_FILE` → one JSON
  line per event, flushed) that the editor tails (`_TraceStreamTailer`,
  polling with torn-line handling) and forwards over WS as `task_trace`.
  The JSON manifest stays byte-identical (PR #16 guarantee), hashes only.
  Canvas nodes highlight on `node_started`/`node_finished` (blue pulse →
  green ring; error events → red, sticky); the view-model exposes
  `runtime_id` (the compiler's `__` namespacing chain) so flattened-graph
  event ids map back to canvas nodes incl. subgraph children. Found &
  fixed along the way: plain function nodes emitted NO node_started/
  node_finished at all — a pre-existing trace-coverage gap that also
  affected `expected.route` assertions on function nodes. Scope cut:
  pinning failed *assertions* (route/state mismatches) to specific nodes
  needs a failure→node mapping model — deferred to E3c as a candidate.
- **E3c — deploy (docker/podman only) behind an explicit confirm (SHIPPED).**
  `_action_deploy` mirrors the CLI's flow (generate → `DeployPackager` →
  secrets from the editor's env → container deployer) but accepts only
  local engines; cloud platforms stay CLI-only (credential/region forms are
  too heavy for editor v1 — the UI says so when `deploy.platform` names
  one). `BaseDeployer` gained an optional `process_hook` (same pattern as
  `LocalRunner`'s) so Cancel terminates a long `docker build`, and the hook
  doubles as per-command progress (`deploy_cmd` events). Missing secrets
  are reported by name only — values never reach the task record or the
  browser. Candidate follow-ups (not scheduled): pinning failed harness
  assertions to specific nodes (needs a failure→node mapping model), an
  "open an example" gallery once the examples ladder lands.

Each phase is independently shippable and lands as its own PR (or small PR series),
per the repo's no-mixed-PRs rule.

## 6.1 Phase ladder 2 — depth (E4–E7, June 2026)

User feedback after E3 (June 2026): the editor only surfaces a shallow slice
of what ABP can do. Three concrete complaints anchored this ladder: (1) edge
management on the canvas is undiscoverable/incomplete, (2) the trace story
ends at node highlights — no trace browser, no Grafana path, (3) runs give
no state/session visibility (every editor Run starts a fresh session because
the runner is a one-shot subprocess with an in-memory checkpointer — and the
UI never says so).

Standing rule (user directive, June 2026): editor work that needs changes to
ABP **core** (anything under `src/agent_blueprint/` outside `editor/`, incl.
templates) requires explicit user approval *before* the change is made.
`editor/` and `frontend/` are free.

### Phase E4 — canvas/edge depth (priority 1, mostly UX repair)
- **E4.1** Edge interaction repair: wide hit area (`interactionWidth`),
  visible selected-edge style, a delete affordance on selection + shortcut
  hint. Half discoverability bug-fix: deletion has existed since E2b.
- **E4.2** Edge reconnect: drag an endpoint to a new node (React Flow
  `onReconnect`). In-place retarget op (preserves list position — order is
  routing semantics for overlapping conditions — and comments).
- **E4.3** Edge config popover: edit condition / default flag on the canvas,
  validated live by the expression parser.
- **E4.4** Condition autocomplete from declared state fields.
- **E4.5** Node palette: all node types (handoff/parallel/subgraph/
  supervisor too); wire supervisor workers / parallel branches by drawing.
- **E4.6** Undo/redo: ops are already discrete mutations — an op journal
  with inverse ops.

### Phase E5 — sessions & state (priority 2, highest impact)
- **E5.1** Chat session (SHIPPED): a *persistent* runner process kept alive via
  `Popen` so the conversation actually continues — message history panel,
  active `thread_id`, "New session". Editor-only: rather than driving the
  generated `_abp_runner.py` REPL and parsing its `You:`/`Agent:` text (a
  robust fix would touch the template), the editor drops its **own** JSON-lines
  driver (`editor/session.py` → `_abp_editor_chat.py`) next to the generated
  project and runs that; it imports the same `run` from `main.py`, so the
  module-level `MemorySaver` carries history across turns. Replies stream over
  `/ws`; "New session" regenerates (picking up edits) with a fresh thread.
  Verified end-to-end against a real local LLM (multi-turn memory works). v1
  limits: in-memory only (durable = E5.5), message-shaped blueprints only.
- **E5.2** Immediate honesty stop-gap (SHIPPED, with E5.3): the Run… form
  states that every Run starts a fresh session (in-memory checkpointer, no
  history) until E5.1 lands.
- **E5.3** State inspector (SHIPPED): the Run… result view shows the run's
  `final_state` (already in the trace manifest — scalars verbatim, message
  lists as a count, structured values as compact JSON). Editor-only:
  `_action_run` surfaces `final_state` from the captured manifest. The
  planned **per-node last-update view was cut** — trace events carry only
  state *hashes* per node, so a per-node *value* view requires opt-in content
  capture, which is E5.4 (a core/template change, needs approval).
- **E5.4** Step-level state diffs: needs opt-in content capture
  (`ABP_TRACE_CONTENT`-style mode; default stays hashes-only) — core/template
  change, needs approval.
- **E5.5** Durable checkpoints (sqlite) + thread browser: continue/reset
  sessions across editor restarts — core change, needs approval.

### Phase E6 — observability suite (priority 3; runtime is ready, editor isn't)
- **E6.1** Traces tab: browse `.abp/traces/` (origin/status filters), open a
  record → event timeline + replay the run onto the canvas highlights.
- **E6.2** Run timeline: per-node duration gantt, token usage and cost
  (`NODE_PRICING` + `on_llm_usage` already exist in generated code).
- **E6.3** Grafana path: `observability.tracing` config form, one-click OTLP
  endpoint test, ready-made otel-collector + Tempo + Grafana compose snippet.
- **E6.4** Live budget meter during runs; warn near `policies.budgets` limits.
- **E6.5** Trace → eval flywheel in the UI (`abp traces export` as a button,
  incl. `--golden`).

### Phase E7 — blueprint coverage (continuous, mechanical)
- **E7.1** Top-level config panels via the existing schema-form resolver:
  state fields/contracts, policies, approvals, artifacts, harness defaults,
  evals, observability, run.sandbox, deploy.
- **E7.2** Tool library: define/edit tools (incl. API auth), usage view, MCP
  status.
- **E7.3** Scenario recorder: save a finished run as a harness scenario
  (input + route/tools expectations from its trace).
- **E7.4** Eval/gate panel: suite results table, score trend, baseline diff.

Horizon (noted, not scheduled): blueprint copilot (LLM assistant editing the
blueprint through ops), multi-blueprint workspace, example gallery.

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
2. **`inspect` reuse vs. dedicated view-model.** RESOLVED (E1): dedicated
   `editor/viewmodel.py`. It builds from `BlueprintSpec` (not the flattened
   IR) so subgraphs stay groups, and reuses the compiler's `_resolve_llm` so
   node cards show the exact provider/model the generated project would use.
   `inspect`'s mermaid extraction stays presentation-oriented and untouched.
3. **Frontend testing depth.** Minimum: vitest unit tests for the ops/view-model mapping.
   Playwright e2e is valuable but heavy for CI — decide at E2.
4. **Multi-blueprint workspaces** (open a directory, switch between files) — explicitly
   deferred; single file per session in all three phases.
