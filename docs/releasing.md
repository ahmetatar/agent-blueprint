# Releasing

This project publishes to Python package indexes through GitHub Releases.

## Tag Standard

Use annotated tags in the form:

```text
vMAJOR.MINOR.PATCH
```

Examples:

```text
v0.3.1
v0.4.0
```

For pre-releases, use valid PEP 440 pre-release versions:

```text
v0.4.0a1
v0.4.0b1
v0.4.0rc1
```

## Version Bumps

This repository uses Commitizen with the PEP 621 version provider, so `cz bump`
updates `project.version` in `pyproject.toml` and updates `CHANGELOG.md`.

Common commands:

```bash
cz bump --check-consistency
cz bump --prerelease alpha
cz bump --prerelease beta
cz bump --prerelease rc
```

Review the version and changelog diff before pushing.

## Publish Flow

The GitHub Actions workflow in `.github/workflows/publish.yml` behaves like this:

- Release with `prerelease=true` publishes to TestPyPI
- Release with `prerelease=false` publishes to PyPI

Both paths run:

- `ruff check .`
- `mypy src`
- `pytest`
- `python -m build`

Publishing only happens if those checks pass.

## Required Trusted Publishers

Configure these trusted publishers on the package index side:

### PyPI

- project: `agent-blueprint`
- owner: `ahmetatar`
- repository: `agent-blueprint`
- workflow: `publish.yml`
- environment: `pypi`

### TestPyPI

- project: `agent-blueprint`
- owner: `ahmetatar`
- repository: `agent-blueprint`
- workflow: `publish.yml`
- environment: `testpypi`

## Recommended Release Steps

### Stable release

```bash
cz bump --check-consistency
git push origin main --follow-tags
```

Then create a GitHub Release from the new tag and publish it as a normal release.

### Pre-release

```bash
cz bump --prerelease rc
git push origin main --follow-tags
```

Then create a GitHub Release from the new tag and publish it as a pre-release.
