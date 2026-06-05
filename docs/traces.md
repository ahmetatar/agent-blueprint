# `abp traces` — The Trace → Eval Dataset Flywheel

Real failures should become regression tests automatically. ABP closes that
loop in three steps:

```
abp test / abp gate          # failing scenarios persist trace records
        │                    #   → .abp/traces/<id>-<stamp>-<uuid>.json
        ▼
abp traces export            # records become eval dataset cases
        │                    #   → datasets/regressions.yaml
        ▼
abp eval / abp gate          # the dataset runs as a permanent regression suite
```

Each exported case carries the **exact LLM and tool outputs** recorded during
the original run (the manifest's replay payloads become harness fixtures), so
it replays deterministically with `llm_mode: mock` / `tool_mode: stub` — no
live API calls.

## Saving trace records

`abp test`, `abp eval`, and `abp gate` (both surfaces) persist records to
`<blueprint_dir>/.abp/traces/` controlled by `--save-traces`:

| Mode | Behavior |
|---|---|
| `failed` (default) | Persist only failing scenarios — the scarce, valuable signal |
| `all` | Persist passing runs too (input for `--golden` exports) |
| `none` | Disable persistence |

Override the store location with `--trace-dir PATH`.

### Record schema (v1)

```json
{
  "schema_version": "1",
  "run_id": "refund_happy_path",
  "blueprint": "customer-support",
  "blueprint_version": "1.0",
  "scenario_id": "refund_happy_path",
  "status": "failed",
  "saved_at": "2026-06-05T14:22:33Z",
  "input": { "message": "Refund invoice 123" },
  "failures": ["route mismatch: expected 'billing', ended on 'support'"],
  "seed": 42,
  "manifest": { "...": "full trace manifest, including replay payloads" }
}
```

`status` reflects the **scenario assertion outcome** (`ScenarioResult.passed`),
not just whether the process completed. The record wraps the manifest because
the original scenario input is not part of the manifest itself.

## Eval-run traces and the `origin` tag

Eval-case runs (`abp eval`, and the eval half of `abp gate`) also persist
records, tagged `"origin": "eval"`; harness scenario runs are tagged
`"origin": "harness"` (records written before origin tagging count as
harness).

**`abp traces export` skips eval-origin records by default.** This prevents
the loop where an exported regression case fails under eval, gets persisted
again, and re-exports as a duplicate of itself. Use `--origin all` (or
`--origin eval`) to include them — you generally don't want to: an eval-origin
failure means an *existing* dataset case is still red, not that a new
regression case is needed.

## Listing records

```bash
abp traces list --dir .abp/traces --status failed --blueprint customer-support
```

## Exporting records as eval cases

### Recipe 1 — failed runs → TDD regression cases (the flywheel)

```bash
abp traces export --output datasets/regressions.yaml
```

Failed records export with an **intentionally empty `expected`** block: the
case starts red and stays red until you fix the blueprint (or fill in the
expectation by hand). Wire the dataset into your blueprint and the case
becomes a permanent part of `abp eval` / `abp gate`:

```yaml
evals:
  suites:
    - id: regressions
      metric: exact_match
      dataset: datasets/regressions.yaml
```

### Recipe 2 — passing runs → golden behavior locks

```bash
abp test agent.yml --save-traces all     # persist passing runs too
abp traces export --status passed --golden --output datasets/golden.yaml
```

`--golden` fills `expected` from the trace — `route` (the node the workflow
ended on) and `tools_called` (the exact ordered tool path) — locking current
behavior in as a regression case.

### Export properties

- **Idempotent merge**: re-exporting skips cases whose ids already exist in
  the output file; hand-edited `expected` blocks are never overwritten.
- Case ids are collision-safe (`<scenario_id>__<hash>`), so multiple failures
  of the same scenario export as distinct cases.
- Output format follows the file suffix: `.yaml` (default), `.json`, `.jsonl`.
- Records with unknown `schema_version` or unparseable JSON are skipped, never
  fatal.

## CI integration

`abp gate` saves failing traces by default — in CI, a red gate automatically
leaves behind the records needed to reproduce and pin the failure:

```bash
abp gate agent.yml || {
  abp traces export --dir .abp/traces --output datasets/regressions.yaml
  # commit/upload datasets/regressions.yaml as a review artifact
}
```

See [`abp gate`](gate.md) for the merge-gate semantics.
