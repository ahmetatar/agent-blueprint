# Runtime Guarantees

This page summarizes what ABP already enforces today.

If you are deciding whether ABP is ready for a real workflow and not just a prototype, this is the practical feature set to look at.

## Why This Matters

Most agent demos fail in predictable ways:

- the graph runs, but invalid input slips through
- a tool is called too many times
- a dangerous action happens without approval
- a model returns malformed structured output
- a low-confidence router silently picks a branch anyway
- a regression appears, but there is no deterministic replay surface

ABP now covers those failure modes directly in the runtime layer.

## What Is Enforced Today

### 1. Runtime entry and exit contracts

ABP validates top-level `input` before the graph starts and top-level `output` before the run returns.

What this gives you:

- bad requests fail before any LLM or tool call happens
- malformed final responses fail loudly instead of leaking partial junk downstream
- API-facing workflows can depend on structured contracts instead of prompt discipline alone

Example:

```yaml
input:
  schema:
    user_input:
      type: string
      required: true
    department:
      type: string
      required: true
      enum: [billing, support]

output:
  schema:
    answer:
      type: string
      required: true
    confidence:
      type: number
      required: true
```

Real-life use case:

- a support triage endpoint must always return `answer` and `confidence`
- a bad client payload should fail before the workflow touches billing tools

### 2. Step limits and unsupported semantics fail loudly

ABP enforces `settings.max_graph_steps` during runtime. `parallel` nodes are implemented for LangGraph targets with explicit branch fan-out, join edges, fail-fast branch errors, and deterministic state reducer behavior. `subgraph` nodes are expanded through named reusable graph definitions with explicit input and output maps so internal state does not merge back into outer state unless declared.

What this gives you:

- accidental loops do not run forever
- unsupported workflow syntax does not degrade into silent no-op behavior

Real-life use case:

- a router keeps bouncing between two nodes because of a bad condition; the run terminates with a concrete step-limit error instead of hanging

### 3. Tool approvals and human review

ABP enforces both tool-level approvals and agent-level human review triggers.

Use cases covered today:

- `requires_approval` on sensitive tools
- `policies.approvals` with `mode: all` (every tool call needs approval) or
  `mode: selective` (only the tools listed in `policies.approvals.tools`)
- `policies.approvals.on_violation: block` (default — unapproved calls raise) or
  `warn` (unapproved calls continue but emit `approval_denied` + `policy_violation`)
- `human_in_the_loop` before a tool call
- `human_in_the_loop` after a tool call
- `human_in_the_loop` before a final response
- `human_in_the_loop: always`

Example:

```yaml
tools:
  issue_refund:
    type: function
    requires_approval: true

policies:
  approvals:
    mode: selective
    tools: [issue_refund]
    on_violation: block

agents:
  billing_agent:
    model: gpt-4o
    tools: [issue_refund]
    human_in_the_loop:
      enabled: true
      trigger: before_tool_call
      tools: [issue_refund]
```

Approval grants are environment-driven in both modes (`ABP_TOOL_APPROVAL_MODE=allow`,
`ABP_APPROVED_TOOLS`, or a tool-specific `ABP_APPROVE_TOOL_*` variable); `on_violation`
only changes what happens when approval is required and not granted.

Real-life use case:

- a refund workflow can inspect invoices automatically, but the actual refund tool cannot run without approval

### 4. Deterministic traces, harness scenarios, and replay

Every run emits machine-readable trace events. Harness scenarios can execute with live, mock, stubbed, or replay-oriented modes depending on the fixture setup.

Important runtime events already available:

- `node_started`
- `node_finished`
- `tool_called`
- `tool_failed`
- `approval_requested`
- `approval_granted`
- `approval_denied`
- `contract_failed`
- `policy_violation`
- `run_finished`

Example:

```yaml
harness:
  defaults:
    llm_mode: mock
    tool_mode: stub
    seed: 42

  scenarios:
    - id: refund_happy_path
      input:
        message: "Refund invoice 123"
      expected:
        tools_called: [lookup_invoice, issue_refund]
        approvals_triggered: true
```

Real-life use case:

- a team changes the router prompt and wants to confirm that the same tool path still happens before merging

### 5. Node and state contracts

ABP can enforce:

- required state before a node runs
- required produced fields after a node runs
- forbidden mutation for node-local fields
- immutable state fields across the workflow
- structured node output contracts
- `state.required_fields` must be non-null when the workflow completes
  (checked at graph exit, before output-schema validation; failures emit
  `contract_failed` with stage `state_required_fields`)
- `state.invariants` must hold throughout execution (checked after every
  agent node against the post-reduction state; failures emit `contract_failed`
  with stage `state_invariant` and the violated expression in metadata).
  Invariants that reference not-yet-populated fields are skipped, not failed.
  Note: invariants are currently checked after agent nodes only — function,
  handoff, and parallel adapter nodes do not mutate blueprint state fields.

Example:

```yaml
contracts:
  state:
    required_fields: [messages]
    immutable_fields: [request_id]
    invariants:
      - "state.confidence >= 0"

  nodes:
    router:
      requires: [messages]
      produces: [route, confidence]
      output_contract: route_payload

  outputs:
    route_payload:
      type: object
      required: [route, confidence]
      properties:
        route:
          type: string
        confidence:
          type: number
```

Real-life use case:

- a triage router must always emit both a route decision and a confidence score
- a request ID must never be overwritten by later nodes

### 6. Tool usage policies

ABP now enforces the main tool controls:

- `max_calls_per_node`
- `max_calls_per_run`
- `require_explicit_arguments`
- `on_unknown_tool: fail`

Example:

```yaml
policies:
  tool_usage:
    max_calls_per_node: 2
    max_calls_per_run: 5
    require_explicit_arguments: true
    on_unknown_tool: fail
```

What this gives you:

- runaway ReAct loops fail fast
- unknown tool names do not quietly degrade into text responses
- malformed tool calls become policy violations instead of random Python errors

Real-life use case:

- a research agent should not call web search ten times because the model keeps retrying with slightly different phrasing

### 7. Budgets

ABP currently enforces:

- `max_tokens_per_run`
- `max_latency_seconds`
- `max_cost_usd`

Cost tracking works in two ways:

- explicit `cost_usd` from the runtime or fixtures
- computed pricing from `model_providers.*.pricing`

Example:

```yaml
model_providers:
  openai_prod:
    provider: openai
    pricing:
      input_per_1k_tokens_usd: 0.005
      output_per_1k_tokens_usd: 0.015

policies:
  budgets:
    max_tokens_per_run: 30000
    max_latency_seconds: 45
    max_cost_usd: 1.50
```

Real-life use case:

- a document-analysis flow used by internal teams should hard-stop before it burns through a large token budget on a pathological prompt

### 8. Retry policies

ABP supports per-node retry policies for transient runtime failures:

```yaml
graph:
  nodes:
    researcher:
      agent: researcher
      retry:
        max_attempts: 2
        backoff_seconds: 1
        on: [exception]
```

`max_attempts` includes the first attempt. Every scheduled retry and exhausted retry emits a trace event, so mock, replay, and live runs expose the same retry lifecycle. Until fallback routing is added, exhausted retries fail deterministically.

Real-life use case:

- a model provider returns a transient transport error; the node retries once, then either succeeds with a visible retry trace or fails with a clear exhausted-retry event

### 9. Low-confidence escalation

ABP can reroute a workflow when a node emits a low confidence score.

Example:

```yaml
policies:
  escalation:
    on_low_confidence: handoff_review
    confidence_threshold: 0.75
```

Combined with a router contract:

```yaml
contracts:
  nodes:
    router:
      produces: [route, confidence]
      output_contract: route_payload
```

Real-life use case:

- a normal support request routes automatically
- an ambiguous request still gets classified, but a confidence of `0.42` reroutes it to a human review or compliance handoff node

## Practical Blueprint Pattern

If you want a strong ABP workflow with the current feature set, the most useful shape is:

1. `input` and `output` at the top level
2. `contracts` for the router and final writer nodes
3. `policies.tool_usage` for noisy tools
4. `policies.budgets` for expensive runs
5. node-level `retry` for transient runtime failures
6. `policies.escalation` for ambiguity
7. `artifacts` for PRD-ready work products
8. `harness` scenarios for the critical happy path and one failure path

That gives you a workflow that is:

- validated before runtime
- guarded during runtime
- able to persist declared artifact outputs
- replayable after runtime

## Artifact-Centric Workflows

ABP supports first-class artifact declarations for LangGraph targets. A blueprint can declare
which node produces a work product, where it should be written, what format it uses, and which
contract validates its payload before persistence:

```yaml
artifacts:
  prd_doc:
    format: markdown
    producer: writer
    contract: prd_contract
    path: "artifacts/prd.md"
    metadata:
      kind: prd
```

During runtime, generated LangGraph code writes the artifact under `ABP_ARTIFACT_DIR` when set,
or relative to the current working directory by default. Successful writes emit an
`artifact_written` trace event with the artifact name, path, format, validation status, and
metadata. Invalid artifact payloads fail before the file is written.

See [examples/prd-factory.yml](../examples/prd-factory.yml) for a PRD-first workflow with a
validated markdown artifact and harness scenario.
