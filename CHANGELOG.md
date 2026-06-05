# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project aims to follow Semantic Versioning.

## [Unreleased]

### Added

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

- development workflow documentation now points contributors to explicit local checks
- release publishing now supports prerelease uploads to TestPyPI and published releases to PyPI
- root `abp` help now shows a branded welcome banner and supports no-arg help output
- product tagline is standardized as `Declarative, framework-agnostic AI agent orchestration via YAML`

### Removed

- tracked macOS metadata files from the repository
