# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project aims to follow Semantic Versioning.

## [Unreleased]

## [0.4.0] - 2026-06-21

First beta release. The YAML → IR → LangGraph → deploy pipeline and its
governance surfaces (lint, doctor, harness, eval, gate, traces, OTel, sandbox,
package, deploy) are validated end-to-end, including a live Azure Container
Apps deployment (GrowOps). Promoted to `Development Status :: 4 - Beta`.

Supported surface: the **LangGraph** target and the **Docker / Podman / AWS App
Runner / Azure Container Apps** deployers. Roadmap (not production-ready): the
`plain` target (minimal), CrewAI (not implemented), MCP tool generation
(guarded), and the GCP Cloud Run deployer (partial). The blueprint schema is
not yet frozen — breaking changes will be marked and documented.

### Added

- `abp editor` (phase E5.5): durable chat threads — chat sessions now survive
  editor restarts. The editor runs the generated project with a SQLite
  checkpointer at a stable path (`.abp/chat/<stem>.db`) and a **Threads**
  browser lists past conversations to *resume* (the agent's own state is
  restored from the checkpoint; the transcript is reloaded) or *reset* (drops
  the transcript and the checkpoint rows). Works for **any** blueprint
  regardless of its `memory.backend`. Core change: `graph.py.j2` honours a new
  `ABP_CHECKPOINT_DB` env override (forces a SQLite `checkpointer` at that
  path) — and this fixes a latent bug in the existing `sqlite` backend, which
  assigned `SqliteSaver.from_conn_string(...)` (a context manager) instead of a
  live saver; both paths now use `SqliteSaver(sqlite3.connect(...))` + `setup()`.
  New `editor/chat_store.py` (transcript index + checkpoint reset) and
  `/api/chat/threads` + `/api/chat/threads/{id}/delete` endpoints; the `editor`
  extra now includes `langgraph-checkpoint-sqlite`
- `abp editor` (phase E5.4): step-level state view — a **Run…** result now
  lists each node's *state delta* (the partial update that node returned — i.e.
  what it changed) in execution order, above the final state, so you can read
  how state evolved through the graph. Backed by an opt-in content-capture mode
  in the generated trace recorder (`ABP_TRACE_CONTENT`): when set, node events
  additionally carry the *summarized* state (scalars normalized, message lists
  as a count — never raw bodies) alongside the existing hashes. The editor
  enables it for the Run… action only; it is **off by default everywhere
  else**, so harness/eval/gate manifests stay byte-identical and goldens /
  baselines are unaffected
- `abp editor` (phase E5.1): persistent **Chat** tab — unlike the one-shot
  **Run…**, *Start chat* keeps one generated-project process alive, so the
  graph's in-memory checkpointer is built once and every message reuses the
  same `thread_id`: the conversation actually continues and follow-up
  questions see earlier turns. *New session* regenerates from the current
  blueprint (picking up edits) with a fresh thread; message history is kept
  server-side so a reloaded tab resyncs, and replies stream over `/ws`. The
  editor drives its own JSON-lines driver dropped next to the generated
  project (one JSON object per line over stdin/stdout) rather than parsing the
  human REPL — so the feature stays entirely inside `editor/`, with **no**
  template/core change. New `editor/session.py` and
  `/api/chat[/start|/send|/stop]` endpoints. Limits (v1): in-memory only (not
  durable across editor restarts), and message-shaped blueprints only (a
  structured `blueprint.input` schema still needs **Run…** with a JSON payload)
- `abp editor` (phase E5.3 + E5.2): state inspector — after a **Run…**
  finishes, the result view shows the run's *final state* (the same snapshot
  the trace manifest records for harness `state_assertions`): scalar fields
  verbatim, message lists as a count, structured values as compact JSON.
  Per-node values are deliberately not shown — the trace carries only hashes
  per node, so a per-node value view needs opt-in content capture (a later
  phase). The Run… form is now honest that every Run starts a *fresh* session
  (in-memory checkpointer, no history); a persistent chat session is the next
  phase. Editor-only — `_action_run` now surfaces `final_state` from the
  captured manifest
- `abp editor` (phase E4.5): the *+ Node* dialog now covers every node type —
  agent, function, handoff (channel + optional message template), parallel
  (branch checkboxes + a join picker), subgraph (subgraph-ref picker +
  key→value `input_map`/`output_map` rows), and supervisor (agent + worker
  checkboxes). References are pickers drawn from the blueprint (the graph
  view-model now exposes `subgraphs`), and the new node goes through the
  existing strict validate-before-write path, so an illegal combination is
  refused with the reason shown. Wiring supervisor workers / parallel
  branches by drawing on the canvas remains a later phase

- `abp editor` (phase E4.6): undo / redo — *↶ Undo* / *↷ Redo* buttons in the
  header and `⌘/Ctrl+Z` / `⌘/Ctrl+Shift+Z` (or `⌘/Ctrl+Y`) step through this
  session's edits. The history is a stack of whole-file YAML snapshots, so one
  uniform model covers every edit kind (canvas op, config form, source save)
  and restoring a snapshot brings back comments and quoting byte-for-byte
  (implemented over the existing whole-file save — no new endpoint). The
  shortcuts defer to Monaco's own undo while the Source pane has unsaved
  changes; live/disk reloads leave the history untouched
- `abp editor` (phase E4b): edge condition editor — selecting a route edge
  now shows an inline editor in the selection toolbar to change its
  `condition` or mark it the `default` (unconditional) route. The condition
  is validated live by the same expression parser the generator uses (an
  invalid condition blocks *Apply*), and declared `state` fields appear as
  chips that insert `state.<field>` at the cursor. *Apply* writes a single
  in-place `set_edge_condition` op that keeps the entry's position in the
  `to:` list and, for a condition-value-only change, preserves the target's
  quoting and comments. New read-only `POST /api/expression/validate`
  endpoint and `state_fields` in the graph view-model
- `abp editor` (phase E4a): edge interaction repair — edges now have a
  generous click target and a clearly visible selected state, selecting one
  shows a floating toolbar (`edge router → worker` + Delete button, so the
  Backspace shortcut that existed since E2b is finally discoverable), and
  edge endpoints can be dragged onto another node to reconnect. Moving the
  target end retargets the entry in place via a new `retarget_edge` op —
  keeping its position in the `to:` list (evaluation order is routing
  semantics for overlapping conditions), its condition, and its comments;
  moving the source end relocates the entry to the new source's edge
- `abp editor` (phase E3c): confirm-gated Deploy — build the agent image and
  start it as a local container (docker or podman) from the Actions tab,
  mirroring `abp deploy`'s flow (generate → deploy packager → secrets from
  the editor's environment → container deployer). Each container command
  streams into the progress view; Cancel terminates a running build (new
  optional `process_hook` on `BaseDeployer`). Cloud platforms (azure/aws/gcp)
  stay CLI-only; missing secrets are reported by name only, values never
  reach the browser. `GET /api/blueprint`'s action surface gained
  `deploy_platform`
- `abp editor` (phase E3b): live execution view — while a Test / Run / Gate
  task executes, canvas nodes pulse blue as they run and keep a green
  (finished) or red (error event) ring afterwards, including nodes inside
  subgraph groups. Powered by a new env-activated stream observer in the
  generated `_abp_trace.py`: when `ABP_TRACE_STREAM_FILE` is set, every
  trace event is appended to that file as one JSON line (hashes only, like
  the manifest); the editor tails it and forwards events over the WebSocket
  as `task_trace`. The JSON trace manifest is byte-identical with or
  without the stream. `run_harness_scenario` / `run_eval_suite(s)` gained a
  pass-through `extra_env` keyword; the graph view-model now exposes each
  node's flattened `runtime_id`

- `abp editor` (phase E3a): Actions tab — run `abp test` / `run` / `gate` /
  `generate` / `doctor` from the editor as background tasks (one at a time,
  `409` when busy) with per-scenario/per-suite progress streamed over the
  existing WebSocket (`task_started` / `task_progress` / `task_done`), a
  harness scenario picker, a one-shot run input (REPL stays CLI-only;
  sandboxed blueprints are refused with a hint rather than run unsandboxed),
  gate with confirm-gated baseline update, and a Cancel button that
  terminates the generated-project subprocess (new `process_hook` on
  `LocalRunner`, threaded through `run_harness_scenario` and
  `run_eval_suite(s)`). New endpoints: `POST /api/actions/{action}`,
  `GET /api/tasks/current`, `POST /api/tasks/current/cancel`;
  `GET /api/blueprint` now includes the action surface (scenario/suite ids,
  baseline presence, sandbox flag)
- `abp editor` (phase E2c): schema-driven config forms — selecting a node
  opens a Config panel with the node's fields (per node type, plus retry
  policy) and, for agent-backed nodes, the linked agent definition (model,
  system prompt, tools, temperature, …). Forms are generated from the
  blueprint JSON Schema (new `GET /api/schema`), so new model fields appear
  without editor changes; Apply writes only changed fields as targeted
  mutations, and clearing a field removes the key (new `unset_field` op)
- `abp editor` (phase E2b): canvas editing — draw edges between node handles
  (a second outgoing edge converts a scalar `to:` into the conditional list
  form, keeping the original target as the default route), delete nodes/edges
  (node deletion cascades edge cleanup; synthetic edges are display-only),
  and add agent/function nodes via a minimal dialog. Edits are applied as
  targeted ruamel mutations (`POST /api/blueprint/ops`) so comments, key
  order, and quoting on untouched lines survive byte-for-byte; the mutated
  document is validated before anything is written, and a stale base hash
  (file changed underneath) refuses the edit with a canvas refresh. The
  shared ruamel dump settings now round-trip conventionally formatted
  blueprints byte-identically (sequence offset 2, explicit `null`) — this
  also improves `abp fix` output fidelity
- `abp editor` (phase E2a): editable source pane + layout persistence — the
  Monaco pane saves back to the YAML file (`PUT /api/blueprint/yaml`,
  `Cmd/Ctrl+S`; unparseable YAML is rejected with the file untouched,
  spec-invalid content is written with the error surfaced in Issues), a
  conflict banner when the file changes on disk under unsaved edits
  (last-writer-wins on save), and dragged node positions persisted to a
  `.abp/editor-layout.json` sidecar (auto-layout fills in anything missing);
  editor saves push `file_changed` to other tabs while the file watcher
  suppresses the disk echo of the editor's own writes
- `abp editor` (phase E1): read-only blueprint visualizer — React Flow canvas
  with ELK auto-layout (typed node cards with resolved provider/model,
  condition-labeled edges, supervisor delegation/parallel fan-out edges,
  subgraph groups, START/END terminals, minimap), an Issues panel
  (validation + lint findings, click-through to source), a read-only Monaco
  source pane with lint markers, and live reload over WebSocket when the
  YAML changes on disk (`watchfiles`); `/api/blueprint` now returns the graph
  view-model and position-mapped lint findings
- `abp editor <blueprint>` (phase E0): local web server with an embedded React
  UI placeholder — `/api/health` + `/api/blueprint`, per-session URL token,
  random free port on 127.0.0.1; `editor` extra (`fastapi`, `uvicorn`) and a
  hatch build hook that embeds the built frontend into published packages
- `abp package <blueprint>`: package the agent as a pip/pipx-installable
  command-line tool — src-layout package, console-script entry point named
  after the blueprint, `requirements.txt` folded into `pyproject.toml`
  (`--output-dir`, `--dry-run`)
- trace records carry an `origin` tag (`harness`|`eval`); `abp traces list/export`
  gain `--origin` filters and export skips eval-origin records by default to
  prevent re-exporting already-exported cases
- `abp eval` gains `--save-traces`/`--trace-dir`; failing eval cases now persist
  trace records, and `abp gate` applies its `--save-traces` policy to both surfaces
- `abp traces list/export`: persisted trace records under `.abp/traces/` and a
  merge-with-dedup export into eval dataset cases — failed runs become regression
  tests (empty `expected`), passing runs become golden behavior locks (`--golden`)
- `abp gate <blueprint>` CI merge-gate command: runs harness scenarios and eval suites,
  diffs the result against a committed baseline (`.abp/gate-baseline.json`), and exits
  non-zero on regression (`--update-baseline`, `--tolerance`, `--json`)
- harness scenario assertions for `route`, `state_assertions`, `output_contract`, and
  `artifacts`; trace manifests now record a summarized `final_state`
- runtime enforcement for state contracts (`required_fields`, `invariants`) and
  approval policy (`mode: all|selective`, `on_violation: block|warn`)
- contributor standards, governance documents, issue and PR templates
- GitHub Actions CI and PR title validation workflows
- pre-commit and Commitizen configuration for local quality and Conventional Commits
- repository editor configuration via `.editorconfig`
- release documentation for version bumping, tag format, TestPyPI, and PyPI publishing
- ABP vNext RFC and implementation plan documentation

### Fixed

- Generated plain `function` nodes now emit `node_started` / `node_finished`
  trace events like every other node type (previously they were invisible in
  traces, which also made harness `expected.route` assertions unable to match
  a function node). Replay goldens recorded against earlier generated projects
  with function nodes will report drift after regeneration — re-record with
  `abp test --save-traces all` / `abp traces export --golden`

### Changed

- `abp editor`: node cards restyled for a cleaner, more professional look —
  larger corner radius, a soft layered elevation shadow (with a gentle hover
  lift and a clear selected ring), a hairline divider between the title
  (type + name) and the body, a larger node name, and more generous padding.
  The per-type accent bar was dropped for a uniform white card (the node type
  is still signalled by the colour of the type label); content is unchanged
- `abp editor`: canvas polish — connection lines are slightly heavier (1.5px)
  for legibility; the blue edge reconnect handles no longer sit on every edge
  (they appear only when an edge is hovered or selected, which is when you'd
  grab an endpoint to reconnect it); and the connection handles now hug their
  node's border consistently — they were anchored to the (taller,
  layout-estimated) node container, so source/bottom handles floated below the
  card — and are toned from stark black to a soft slate
- `abp test` and `abp gate` now persist failing harness traces to `.abp/traces/`
  by default (`--save-traces none` to disable)
- development workflow documentation now points contributors to explicit local checks
- release publishing now supports prerelease uploads to TestPyPI and published releases to PyPI
- root `abp` help now shows a branded welcome banner and supports no-arg help output
- product tagline is standardized as `Declarative, framework-agnostic AI agent orchestration via YAML`

### Removed

- tracked macOS metadata files from the repository
