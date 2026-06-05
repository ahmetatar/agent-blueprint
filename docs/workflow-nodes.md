# Workflow Nodes: Parallel, Subgraph, Handoff

Beyond `agent` and `function` nodes, blueprints support three workflow
semantics. All three are fully generated for the LangGraph target. See
[examples/incident-response.yml](../examples/incident-response.yml) for a
blueprint combining all of them.

## Parallel

```yaml
graph:
  entry_point: analyze
  nodes:
    analyze:
      type: parallel
      branches: [logs, metrics]
      join: triage
    logs:    { agent: log_analyst }
    metrics: { agent: metric_analyst }
    triage:  { agent: triage_writer }
```

- Branches run concurrently in one LangGraph superstep; the `join` node is a
  barrier — it starts only after **all** branches finish.
- Branch nodes cannot declare their own outgoing edges, and the parallel node
  itself routes only through `branches`/`join` (validated).
- `failure_policy: fail_fast` (the only policy today): any branch exception
  aborts the run.
- Trace events: `parallel_started` fires at fan-out; `parallel_finished`
  fires at the join, after the branches merged.

**Concurrent writes.** Two branches writing the same state field with the
default `replace` reducer collide at runtime. Declare branch outputs in
`contracts.nodes.<branch>.produces` and the
`parallel-branch-conflict` lint check flags collisions statically; use an
`append`/`merge` reducer or distinct fields.

## Loops

Cycles are a first-class agent pattern (reflection, revision, retry-until-ok)
and are fully supported — route back with a conditional edge and exit on a
state condition. Two safety nets apply:

- the `unbounded-loop` lint check (error) statically flags any cycle with
  **no route out** — no conditional exit to `END` or to a node outside the
  loop — in the main graph and inside every subgraph;
- at runtime, `settings.max_graph_steps` (default 25) bounds total steps via
  LangGraph's `recursion_limit`.

## Subgraph

```yaml
graph:
  nodes:
    triage:
      type: subgraph
      ref: triage_flow
      input_map:  { findings: raw_findings }   # outer field -> inner field
      output_map: { verdict_summary: summary } # inner field -> outer field

subgraphs:
  triage_flow:
    entry_point: write
    nodes: { write: { agent: triage_writer } }
    edges: [ { from: write, to: END } ]
```

- Subgraphs are **flattened at compile time**: inner nodes are namespaced as
  `<node>__<inner>` (e.g. `triage__write`), and entry/exit adapter nodes
  translate state through `input_map`/`output_map`.
- Mapped inner fields live under namespaced state keys
  (`<node>__<inner_field>`); unmapped field references inside a subgraph
  resolve to the **shared outer state** — that is the mechanism for global
  fields like `messages`.
- Inner edge conditions are namespaced automatically: a condition on
  `state.verdict` inside the subgraph compiles against the namespaced key.
- **Nesting is supported** (a subgraph node inside a subgraph), up to 10
  levels. Reference cycles (`sg1 -> sg2 -> sg1`) are a compile error that
  prints the offending chain.
- Trace events: `subgraph_entered` / `subgraph_exited` at the adapters.

## Handoff

```yaml
graph:
  nodes:
    page_oncall:
      type: handoff
      channel: slack            # console | webhook | slack | email
      action: page_oncall
      message_template: "Incident severity {severity}: {summary}"
```

- `message_template` is rendered with `str.format` against the state —
  `{field}` placeholders resolve to state fields; unknown placeholders are
  kept as-is.
- Channels and their environment variables:

| Channel | Delivery | Required env |
|---|---|---|
| `console` | prints `[HANDOFF] <message>` | — |
| `webhook` | `POST` full JSON payload | `ABP_HANDOFF_WEBHOOK_URL` |
| `slack` | `POST {"text": ...}` (incoming webhook) | `ABP_HANDOFF_SLACK_WEBHOOK_URL` |
| `email` | SMTP (STARTTLS) | `ABP_HANDOFF_SMTP_HOST`, `ABP_HANDOFF_EMAIL_FROM`, `ABP_HANDOFF_EMAIL_TO` (optional: `ABP_HANDOFF_SMTP_PORT`, `ABP_HANDOFF_SMTP_USER`, `ABP_HANDOFF_SMTP_PASSWORD`) |

  Used channels are added to the generated `.env.example`.
- Trace events: `handoff_requested` before delivery, `handoff_failed` (and a
  raised error) when delivery fails. A handoff that cannot reach a human is
  treated as a hard failure, not silently ignored.
- **Harness determinism:** when the harness runs with `tool_mode: stub` or
  `replay` (`ABP_TOOL_MODE != live`), delivery is skipped — the
  `handoff_requested` event is still emitted, so scenarios can assert on it.
- Escalation policies (`policies.escalation`) can target a handoff node to
  route low-confidence outputs to a human.
