# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project aims to follow Semantic Versioning.

## [Unreleased]

### Added

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

### Changed

- `abp test` and `abp gate` now persist failing harness traces to `.abp/traces/`
  by default (`--save-traces none` to disable)
- development workflow documentation now points contributors to explicit local checks
- release publishing now supports prerelease uploads to TestPyPI and published releases to PyPI
- root `abp` help now shows a branded welcome banner and supports no-arg help output
- product tagline is standardized as `Declarative, framework-agnostic AI agent orchestration via YAML`

### Removed

- tracked macOS metadata files from the repository
