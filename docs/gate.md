# `abp gate` — Regression Merge Gate

`abp gate` turns every change to an agent blueprint into a gated experiment:
it runs all harness scenarios and eval suites, condenses the results into a
small deterministic snapshot, and compares that snapshot against a baseline
committed to your repository. Any regression fails the command with a
non-zero exit code, which makes it a drop-in CI merge gate.

## The gate rule

```
gate passes  ⟺  (current run is all green)  AND  (no regression vs baseline)
```

Both halves matter. A diff against the baseline alone would miss a *new*
scenario that fails (it has no baseline entry); the all-green rule catches it.

## Quick start

```bash
# 1. Create the baseline (only written when the run is fully green)
abp gate agent.yml --update-baseline

# 2. Commit it
git add .abp/gate-baseline.json && git commit -m "chore: add gate baseline"

# 3. Gate every change (CI or locally)
abp gate agent.yml
```

## Baseline file

Default location: `<blueprint_dir>/.abp/gate-baseline.json` — override with
`--baseline PATH`. Commit it to the repository; it is small and diff-stable
by design (only aggregates, no timestamps, stdout, or trace data):

```json
{
  "schema_version": "1",
  "blueprint": "customer-support",
  "blueprint_version": "1.0",
  "harness": {
    "scenarios": {
      "refund_happy_path": { "passed": true }
    }
  },
  "evals": {
    "suites": {
      "router_accuracy": {
        "passed": true,
        "score": 1.0,
        "total": 3,
        "passed_cases": 3
      }
    }
  }
}
```

The `blueprint` name in the baseline must match the current run — if you keep
multiple blueprints in one directory, pass an explicit `--baseline` per
blueprint.

## Regression rules

| Situation | Verdict |
|---|---|
| Baselined scenario passed, now fails | **Regression** |
| Baselined scenario missing from current run | **Regression** (intentional deletion requires `--update-baseline`) |
| New scenario, passing | OK — reported as `NEW` |
| New scenario, failing | Gate fails via the all-green rule |
| Eval suite `score < baseline - tolerance` | **Regression** (strict `<`; `score == baseline - tolerance` is allowed) |
| Eval suite passed → failed | **Regression** (even if the score is within tolerance) |
| Baselined eval suite missing | **Regression** |
| Scenario failed → passed / score improved | Reported as `IMPROVED` (does not auto-update the baseline) |

## Options

| Option | Default | Meaning |
|---|---|---|
| `--baseline PATH` | `<dir>/.abp/gate-baseline.json` | Baseline file location |
| `--update-baseline` | off | Run everything and overwrite the baseline — **only when the run is all green**; a red run exits 1 without writing (a red baseline would hide future regressions) |
| `--tolerance FLOAT` | `0.0` | Allowed eval score drop before flagging a regression |
| `--json` | off | Machine-readable gate report on stdout (`passed`, `all_green`, `regressions`, `improvements`, `new_entries`, `current`) |
| `--install/--no-install` | no-install | pip install dependencies before running scenarios and eval cases |

If the blueprint defines **no harness scenarios and no eval suites**, the gate
fails (`exit 1`): a gate over nothing is a false green.

If no baseline exists yet, the gate fails with a pointer to
`--update-baseline` — it never auto-creates one (CI safety).

## CI example (GitHub Actions)

```yaml
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install agent-blueprint
      - run: abp gate agent.yml
```

Because harness scenarios run with mock/replay fixtures and eval results carry
no timestamps, gate runs are deterministic — the same blueprint and fixtures
produce a byte-identical snapshot, so baseline diffs in code review show only
real behavioral change.

## Failed runs become regression tests

The gate persists failing scenario traces to `.abp/traces/` by default
(`--save-traces failed|all|none`). Export them as eval dataset cases with
[`abp traces export`](traces.md) — the trace → eval flywheel.
