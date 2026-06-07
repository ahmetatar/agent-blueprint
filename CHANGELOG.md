# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project aims to follow Semantic Versioning.

## [Unreleased]

### Added

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
