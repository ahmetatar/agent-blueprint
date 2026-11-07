# Examples

Small blueprints, each showing one runtime guarantee. Every example ships its
own harness scenarios, so you can see the guarantee fire **offline** — mocked
LLM, stubbed tools, no API key:

```bash
pip install agent-blueprint
abp test --install examples/<example>/<file>.agents.yaml
```

`--install` installs the generated project's dependencies (LangGraph) on the
first run.

| Example | Guarantee | What the scenario proves |
|---|---|---|
| [`low-confidence-escalation`](low-confidence-escalation/support.agents.yaml) | `policies.escalation` | A ticket the router labels `billing` with only 0.42 confidence is rerouted to `human_review` mid-run, while a 0.94-confidence ticket routes normally |
| [`runaway-tool-loop`](runaway-tool-loop/research.agents.yaml) | `agents.*.max_turns` | A model that asks for a search on every turn gets exactly two searches executed; the node then finishes instead of looping |

## Break it on purpose

The fastest way to understand a guarantee is to remove it and watch the test
fail:

- In `low-confidence-escalation`, lower `confidence_threshold` to `0.3`. The
  ambiguous ticket now ends on `billing` and `abp test` fails.
- In `runaway-tool-loop`, raise `max_turns` to `6`. The loop runs past the
  mocked responses and the scenario fails.

Wire the same scenarios into CI with [`abp gate`](../docs/gate.md) and a PR
that weakens a guarantee is caught before merge.
