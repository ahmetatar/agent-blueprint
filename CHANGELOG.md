# Changelog

All notable changes to this project will be documented in this file.

The format is based on Keep a Changelog, and this project aims to follow Semantic Versioning.

## [Unreleased]

### Added

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

### Removed

- tracked macOS metadata files from the repository
