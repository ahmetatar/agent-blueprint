# Contributing

Thanks for contributing to `agent-blueprint`.

This project is a declarative specification and code generation tool. That means small changes can affect schema compatibility, generated code, CLI behavior, and deployment workflows at the same time. The contribution rules below are designed to keep those contracts stable.

## Ground Rules

- Discuss large or breaking changes in an issue before opening a PR.
- Keep pull requests focused. Do not mix refactors, feature work, and docs cleanup in one PR.
- Do not introduce backward-incompatible schema changes without explicitly marking them as breaking and documenting the migration path.
- User-facing changes must update docs.
- Validation, generator, and CLI changes must include tests.

## Development Setup

```bash
git clone https://github.com/ahmetatar/agent-blueprint
cd agent-blueprint
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
pre-commit install --hook-type commit-msg
```

## Local Quality Checks

Run these before opening a PR:

```bash
ruff check .
mypy src
pytest
python -m build
abp --help
abp validate examples/basic-chatbot.yml
```

## Commit Message Standard

This repository uses Conventional Commits.

Format:

```text
type(scope): summary
```

Scope is optional, but recommended for non-trivial changes.

Allowed types:

- `feat`
- `fix`
- `docs`
- `refactor`
- `test`
- `chore`
- `ci`
- `build`

Rules:

- Use lowercase for `type` and `summary`.
- Write the summary in imperative mood.
- Do not end the summary with a period.
- Mark breaking changes with `!` or a `BREAKING CHANGE:` footer.

Examples:

```text
feat(cli): add schema export command
fix(generator): preserve tool ordering in langgraph output
docs(readme): clarify blueprint positioning
```

## Branch and PR Expectations

- Branch names should be descriptive, for example `feat/langgraph-tool-routing` or `fix/schema-provider-validation`.
- PR titles should also follow Conventional Commits.
- Link the relevant issue in the PR description when one exists.
- Keep PR descriptions explicit about schema, generator, CLI, and deployment impact.

## Required Tests by Change Type

### Schema changes

If you change any Pydantic model, YAML field, or reference validation:

- add or update fixture coverage under `tests/fixtures/`
- add validation tests under `tests/test_models/` or `tests/test_ir/`
- document the new field in `README.md` or the relevant file in `docs/`

### Generator changes

If you change generated project structure or rendered code:

- add or update generator tests under `tests/test_generators/`
- verify the generated output still reflects the intended IR contract
- document any new target-specific behavior

### CLI changes

If you change a command, flags, output semantics, or UX:

- add or update tests under `tests/test_cli/`
- update README usage examples when relevant

### Deploy or provider changes

If you change deployers, providers, MCP integrations, or memory backends:

- add targeted tests
- document required environment variables and expected behavior
- avoid introducing provider-specific behavior into generic schema paths unless intentional

## Design Constraints for This Project

Keep these architectural boundaries intact unless the change explicitly aims to revise them:

- `models/` defines the validated blueprint contract
- `ir/` is the intermediate representation shared by generators
- `generators/` consume IR rather than raw YAML
- `templates/` stay target-specific
- CLI commands should remain thin orchestration layers

When in doubt, prefer extending the existing IR and validation model instead of letting generator-specific behavior leak into the schema.

## Documentation Expectations

Update documentation when changing:

- blueprint schema
- CLI commands or flags
- supported providers, MCP servers, retrievers, memory backends, or deploy options
- generator capabilities or limitations

## Release Notes and Changelog

Add a short entry to `CHANGELOG.md` for notable user-facing changes. Keep entries concise and grouped under `Unreleased`.

## Questions

If you are unsure whether a change is in scope, open an issue first. That is strongly preferred for new schema fields, new generators, and breaking behavior changes.
