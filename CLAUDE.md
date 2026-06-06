# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`agent-blueprint` (CLI: `abp`) — declarative, framework-agnostic AI agent orchestration via YAML. A validated YAML blueprint compiles to an intermediate representation (IR) and generates runnable agent projects (LangGraph today; plain Python minimal; CrewAI not implemented). Published on PyPI, v0.3.x Alpha, Python 3.11+.

## Commands

```bash
pip install -e ".[dev]"        # dev setup (venv recommended)

pytest                          # full test suite
pytest tests/test_ir/test_compiler.py -v                 # one file
pytest tests/test_gating.py::test_name -v                # one test

ruff check .                    # lint (line-length 100)
mypy src                        # strict mode — CI enforces it
python -m build                 # package build (also a CI step)

abp validate examples/basic-chatbot.yml                  # CLI smoke test
```

CI (`.github/workflows/ci.yml`) runs exactly: ruff, mypy src, pytest (with `--cov-fail-under=79` coverage floor), build, CLI smoke on Python 3.11 + 3.12. Conventional Commits enforced (commitizen + pr-title check).

## Architecture: the compilation pipeline

The core flow is a strict one-way pipeline; understanding it explains where any change belongs:

```
YAML blueprint
  → utils/yaml_loader.py      (${} env interpolation)
  → models/blueprint.py        (BlueprintSpec — root Pydantic model; cross-ref validation
                                lives in model validators here, e.g. escalation targets,
                                approval tools, artifact contract refs)
  → ir/compiler.py             (BlueprintSpec → AgentGraph IR: subgraph expansion,
                                LLM resolution via _resolve_llm)
  → generators/langgraph.py    (Jinja2 PackageLoader over templates/langgraph/*.j2)
  → generated project          (graph.py, nodes.py, state.py, _abp_runner.py,
                                _abp_harness.py, _abp_trace.py, and _abp_otel.py
                                when observability.tracing is enabled)
```

`ir/expression.py` is a safe ast-based condition parser used both for code generation (`to_dict_access()` renders `state["key"]` access) and static analysis (route overlap detection, disjunct normalization) consumed by `linting.py`.

Around the pipeline sit the operational surfaces, each a thin CLI wrapper (`cli/*_cmd.py`) over a logic module:

- `linting.py` — 9 lint checks, 2 autofixes (incl. `unbounded-loop`: SCC-based detection of cycles with no route out; loops with conditional exits are a legitimate pattern and not flagged). Note: `lint_cmd.py` compiles **before** linting, so compile errors surface before lint findings.
- `doctoring.py` — target-compatibility + env diagnostics; blocks unsupported features (parallel nodes, subgraph nesting) at generation time.
- `harness_runner.py` — deterministic test harness (`abp test`): mock/replay/live LLM, stub/live tools; route / state / artifact / output-contract assertions; writes a manifest incl. `final_state`.
- `eval_runner.py` — eval suites (exact_match, policy_violations, rubric).
- `gating.py` — `abp gate`: runs harness + eval, diffs against `.abp/gate-baseline.json`, exit 1 on regression (rule: all-green AND no-regression); `--update-baseline` only writes when green.
- `trace_store.py` — `.abp/traces/` persistence with origin tagging (harness|eval); `abp traces list/export` turns failed/golden traces into eval cases (merge-dedup idempotent; default `--origin harness` prevents export-eval-export loops).
- `runners/local.py` — `abp run`: generates into a temp dir and executes via subprocess. `runners/sandbox.py` (`--sandbox` / blueprint `run.sandbox`) builds a container image instead and runs `--rm` with an allowlist-only env; engine `auto` probes podman before docker.
- `packagers/cli.py` — `abp package`: restructures generator output into a src-layout, pipx-installable CLI package (console script named after the blueprint; cross-imports rewritten to relative via a known-module regex; requirements.txt folded into pyproject).
- `deployers/` — Docker/Podman/AWS App Runner complete; Azure ACI + GCP Cloud Run partial.
- Observability: top-level `observability.tracing` exports the JSON trace events as OTel spans via an observer registry inside generated `_abp_trace.py` — the JSON manifest stays byte-identical (goldens/harness/gate unaffected). Only hashes are exported, never content. Standard `OTEL_*` env vars win over blueprint values; `ABP_OTEL=off` is the kill switch.

### Declared vs. enforced (the recurring theme)

Many blueprint features started as Pydantic models + lint checks with incomplete runtime enforcement; most are now generated: state contracts, approval policies, retry, escalation routing, parallel fan-out/join (with `parallel-branch-conflict` lint), nested subgraphs (compile-time flattening with namespaced ids/state keys and condition remapping, cycle detection), handoff channel delivery (console/webhook/slack/email; delivery skipped when `ABP_TOOL_MODE != live`), and supervisor nodes (dynamic delegation via generated `transfer_to_*` tools + LangGraph `Command`; iteration budget in the injected `_abp_supervisor_iters` state channel; `agent_handoff` trace events). Before adding a new lint or validator, check `models/blueprint.py` model validators — cross-reference validation often already exists there.

## Gotchas

- **Jinja2 templates**: env uses `trim_blocks=True` + `lstrip_blocks=True`. Do NOT use `{%- %}` (dash trim) inside generated Python function bodies — it breaks line/indent structure. Use plain `{% %}`.
- `reasoning.llm_kwargs` is serialized in templates with the `to_python` filter (`repr()`) — produces Python literals, not JSON.
- `EdgeTarget` supports the `- default: END` YAML shorthand via a `model_validator(mode="before")` in `models/graph.py`.
- State invariants render in generated code as a lambda list (no `eval`): `STATE_INVARIANTS = [(source, lambda merged: ...)]`.
- typer ≥0.26 vendors click (`typer._click`) — do not `import click` in src or tests; use `tmp_path` + `monkeypatch.chdir` instead of `CliRunner.isolated_filesystem`.
- Rich wraps long lines (CI tmp paths are long) — in CLI output assertions, unwrap with `" ".join(output.split())` or assert short substrings.
- `BlueprintSettings.max_graph_steps` (default 25) maps to LangGraph `recursion_limit`.
- Escalation's `__abp_escalation_*` keys are intentionally NOT declared state channels: LangGraph hands a node's raw updates to conditional-edge routers within the same superstep but does not persist undeclared keys (verified empirically on langgraph 0.3→1.0) — ephemeral same-superstep signal, no stale state. Anything that must survive across supersteps needs a declared channel (see `_abp_supervisor_iters`).

## Contribution contracts (from CONTRIBUTING.md)

- No backward-incompatible schema changes without marking them breaking + documenting migration.
- Validation, generator, and CLI changes must include tests; user-facing changes must update `docs/`.
- Keep PRs focused — no mixed refactor/feature/docs PRs.
