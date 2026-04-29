# ABP vNext Implementation Plan

## Objective

Translate the vNext RFC into an execution-ready delivery plan with:

- milestones
- epics
- issue-sized work items
- dependency order
- acceptance criteria
- recommended first implementation slices

This plan assumes the RFC in [abp-vnext-rfc.md](./abp-vnext-rfc.md) is the product direction.

## Delivery Strategy

Implementation should not begin with new syntax breadth. It should begin by closing correctness gaps between declared schema and actual runtime behavior.

Recommended execution order:

1. make current declarations real
2. add deterministic testability
3. add explicit contracts and linting
4. add stronger workflow semantics
5. add evals and scoring

## Milestones

## M0: Runtime Integrity

Goal:

Make existing schema promises real or fail loudly.

Why first:

There is already a gap between what ABP allows in YAML and what generated/runtime behavior actually enforces. That gap must close before expanding the spec surface.

### Epics

- enforce declared runtime controls
- fail loudly on unsupported semantics
- expose target limitations explicitly

### Issues

#### M0.1 Enforce `max_graph_steps`

Implement:

- runtime step counter
- hard failure when graph exceeds configured limit
- trace event for step-limit termination

Acceptance criteria:

- blueprint with `settings.max_graph_steps: 2` fails on step 3
- failure is visible in runtime output
- covered by tests

#### M0.2 Enforce `input` contract at run entry

Implement:

- validate inbound payload before graph execution
- reject malformed input with structured error

Acceptance criteria:

- invalid input never starts graph
- error names missing or invalid fields
- unit tests cover required, nullable, enum, and default behavior

#### M0.3 Enforce final `output` contract at run exit

Implement:

- validate final workflow output
- fail or mark run invalid on contract mismatch

Acceptance criteria:

- invalid final output exits with contract error
- successful run returns validated output
- tests cover scalar and object outputs

#### M0.4 Enforce `requires_approval` for tools

Implement:

- block tool call when approval gate not satisfied
- add approval hook abstraction
- default local runner behavior should be explicit and deterministic

Acceptance criteria:

- protected tool cannot run silently
- blocked run emits approval-required event
- local mode can use deterministic auto-deny or explicit approval stub

#### M0.5 Enforce `human_in_the_loop`

Implement:

- support triggers:
  - `before_tool_call`
  - `after_tool_call`
  - `before_response`
  - `always`

Acceptance criteria:

- each trigger can be tested independently
- runtime behavior matches YAML config
- unsupported combinations fail during validation or compilation

#### M0.6 Fail loudly on unsupported node types

Implement:

- `parallel` and `subgraph` should fail during compile or generation until implemented
- no silent fallback to no-op behavior

Acceptance criteria:

- blueprints using unsupported semantics fail deterministically
- error message points to exact node and unsupported type

#### M0.7 Publish target capability matrix

Implement:

- internal target feature table
- generator emits warning or error when blueprint uses unsupported features

Acceptance criteria:

- unsupported feature use is surfaced before runtime
- matrix is documented

## M1: Harness, Trace, Replay

Goal:

Make workflows testable and replayable without live providers.

Why second:

This is the foundation for determinism, regression safety, and confidence in multi-agent workflows.

### Epics

- structured trace emission
- scenario-driven testing
- replay engine

### Issues

#### M1.1 Define run trace schema

Implement:

- run manifest format
- event schema
- state hash strategy
- normalization rules

Acceptance criteria:

- each run emits machine-readable trace data
- trace schema versioned
- state and tool events represented consistently

#### M1.2 Emit runtime events from generated LangGraph target

Implement:

- `node_started`
- `node_finished`
- `tool_called`
- `tool_failed`
- `approval_requested`
- `approval_granted`
- `contract_failed`
- `run_finished`

Acceptance criteria:

- a normal run produces ordered events
- failure runs produce terminal error events
- tests assert event order and minimum fields

#### M1.3 Add `harness` top-level schema

Implement:

- defaults
- scenarios
- expected assertions
- mode selection

Acceptance criteria:

- blueprint validates with harness block
- scenarios can reference routes, tools, outputs, state assertions

#### M1.4 Add `abp test`

Implement:

- run all scenarios
- run one scenario by ID
- summary output with pass/fail counts

Acceptance criteria:

- deterministic test report
- non-zero exit on failure
- scenario filtering supported

#### M1.5 Add replay mode

Implement:

- run from recorded trace
- compare new run against golden trace
- normalization for timestamps, generated IDs, whitespace

Acceptance criteria:

- same scenario can replay without live provider calls
- drift is reported as diffable mismatch

#### M1.6 Add tool stubs and LLM mocks

Implement:

- deterministic stubbed tool results
- mocked LLM response adapter
- fixture-driven outputs

Acceptance criteria:

- harness scenarios can run offline
- tool and model behavior is reproducible by fixture

## M2: Contracts and Lint

Goal:

Make workflow correctness explicit and statically checkable.

### Epics

- contract compiler support
- runtime contract enforcement
- static linting

### Issues

#### M2.1 Add `contracts` top-level schema

Implement:

- state-level contracts
- node-level contracts
- output contracts

Acceptance criteria:

- schema validated
- compiler stores contract metadata in IR

#### M2.2 Add node precondition and postcondition enforcement

Implement:

- `requires`
- `produces`
- `forbids_mutation`
- immutable field checks

Acceptance criteria:

- bad node mutation fails at runtime
- missing required state fields fail before node execution

#### M2.3 Replace weak `output_schema` extraction with structured validation

Implement:

- strict node output contract validation
- optional backward-compat layer for legacy `output_schema`

Acceptance criteria:

- node outputs validated structurally
- failures can route to fallback or error

#### M2.4 Add `abp lint`

Implement checks for:

- unreachable nodes
- dead state fields
- condition overlap ambiguity
- missing default route
- unsupported mutation patterns
- artifact declared but never produced
- contract declared but never consumed

Acceptance criteria:

- linter reports file and object references clearly
- lint exits non-zero on error severity
- warning severity supported for softer findings

#### M2.5 Add `abp doctor`

Implement:

- env var checks
- unresolved `impl` imports
- provider configuration checks
- target compatibility checks

Acceptance criteria:

- doctor can run pre-generation
- missing prerequisites are reported clearly

## M3: Policies and Budgets

Goal:

Turn low-noise and safe execution into explicit declarative behavior.

### Epics

- approval policy layer
- tool usage policy
- execution budgets

### Issues

#### M3.1 Add `policies` top-level schema

Implement:

- approvals
- tool usage
- escalation
- budgets

Acceptance criteria:

- schema validated
- policy metadata available in IR and runtime

#### M3.2 Enforce tool usage limits

Implement:

- `max_calls_per_node`
- `max_calls_per_run`
- unknown tool handling
- explicit args enforcement

Acceptance criteria:

- run fails or reroutes on limit breach
- trace records policy violation

#### M3.3 Enforce budget ceilings

Implement:

- token ceiling
- latency ceiling
- optional cost tracking abstraction

Acceptance criteria:

- budget exceedance terminates or reroutes deterministically
- live and mock modes behave predictably

#### M3.4 Add escalation policy support

Implement:

- low confidence routing
- policy-driven handoff

Acceptance criteria:

- low confidence can trigger specific node or handoff path

## M4: Artifacts

Goal:

Make PRD-ready outputs first-class workflow products.

### Epics

- artifact declaration
- artifact persistence
- artifact validation

### Issues

#### M4.1 Add `artifacts` top-level schema

Implement:

- named artifact definitions
- producer node references
- path and format metadata

Acceptance criteria:

- schema validated
- compiler maps artifact ownership

#### M4.2 Persist artifacts during runtime

Implement:

- artifact write API
- local filesystem persistence for local runner
- trace event for artifact creation

Acceptance criteria:

- artifact files written deterministically
- missing producer or contract mismatch fails run

#### M4.3 Add artifact contract validation

Implement:

- validate artifact contents against declared contract
- support markdown, json, yaml, text with typed metadata

Acceptance criteria:

- invalid artifact fails the run or is marked invalid in trace

#### M4.4 Add PRD-first templates and examples

Implement:

- example blueprint for PRD generation workflow
- example harness scenarios for PRD output

Acceptance criteria:

- repo contains at least one end-to-end PRD-ready example

## M5: Workflow Semantics

Goal:

Expand orchestration power after correctness and testability are in place.

### Epics

- parallel execution
- subgraph reuse
- retry and fallback DSL
- condition grammar and semantic analysis

### Issues

#### M5.1 Implement `parallel` node semantics

Implement:

- fan-out
- join
- branch result merge behavior
- partial failure policy

Acceptance criteria:

- parallel branches execute with deterministic merge rules
- state reducers are respected

#### M5.2 Implement `subgraph` node semantics

Implement:

- referenced reusable graph
- input mapping
- output mapping
- namespace isolation or controlled merge

Acceptance criteria:

- subgraph reuse works without implicit state collisions

#### M5.3 Add retry DSL

Implement:

- max attempts
- backoff
- retry conditions

Acceptance criteria:

- retries visible in trace
- exhausted retry triggers defined fallback or failure

#### M5.4 Add fallback DSL

Implement:

- fallback target
- fallback trigger types

Acceptance criteria:

- contract failure, tool error, or policy failure can route predictably

#### M5.5 Formalize condition expression grammar and semantic analysis

Implement:

- publish the supported condition expression grammar explicitly
- distinguish compile-supported syntax from statically analyzable syntax
- add deeper overlap and ambiguity analysis for compound boolean conditions
- define target-portability guarantees for condition evaluation semantics
- document unsupported or intentionally deferred constructs

Acceptance criteria:

- docs state exactly which boolean and comparison constructs ABP supports
- parser behavior and documentation match
- lint can explain when a condition is valid but only partially analyzable
- ambiguous compound routes are surfaced more accurately than simple equality-only heuristics
- target generators use the same normalized condition semantics

## M6: Evals

Goal:

Support benchmark-style and rubric-based evaluation beyond deterministic harness checks.

### Epics

- dataset-driven evals
- metrics
- scoring output

### Issues

#### M6.1 Add `evals` top-level schema

Acceptance criteria:

- eval suites validate
- datasets referenceable by path

#### M6.2 Add `abp eval`

Implement:

- run eval suite
- aggregate metric output
- machine-readable results

Acceptance criteria:

- eval results persisted and comparable across runs

#### M6.3 Add rubric evaluation support

Implement:

- exact match
- policy violation count
- rubric score

Acceptance criteria:

- at least one rubric-capable evaluator exists for artifact quality

## Recommended First Slices

These are the first implementation slices worth doing immediately.

### Slice A: Integrity Patch

Scope:

- M0.1 `max_graph_steps`
- M0.4 `requires_approval`
- M0.6 unsupported node types fail loudly

Reason:

Smallest set that improves runtime honesty and reduces hidden behavior.

### Slice B: Trace Foundation

Scope:

- M1.1 trace schema
- M1.2 runtime events

Reason:

Everything in harness, replay, debugging, and eval depends on trace shape.

### Slice C: Harness MVP

Scope:

- M1.3 `harness` schema
- M1.4 `abp test`
- M1.6 tool stubs / LLM mocks

Reason:

This delivers immediate user value and proves deterministic workflow testing.

### Slice D: Contracts MVP

Scope:

- M2.1 `contracts`
- M2.2 node pre/post enforcement
- M2.3 structured outputs

Reason:

This is the line between “agent graph” and “workflow system”.

## Suggested GitHub Issue Titles

Use these directly or with small edits.

- Enforce `max_graph_steps` in generated runtimes
- Enforce top-level input schema before graph execution
- Enforce top-level output schema before returning final result
- Add approval gate enforcement for protected tools
- Implement `human_in_the_loop` runtime triggers
- Fail generation on unsupported node types like `parallel` and `subgraph`
- Introduce target capability matrix and preflight compatibility checks
- Define ABP run trace schema
- Emit structured runtime events from LangGraph target
- Add `harness` schema and scenario model
- Add `abp test` command for scenario execution
- Add replay mode with golden trace comparison
- Add deterministic tool stubs and mocked LLM adapter
- Add `contracts` schema and IR support
- Enforce node preconditions and postconditions
- Replace weak `output_schema` extraction with strict structured outputs
- Add `abp lint` command for workflow correctness
- Add `abp doctor` command for runtime and environment preflight checks
- Add `policies` schema for approvals, limits, and budgets
- Enforce per-node and per-run tool call limits
- Add latency, token, and cost budget enforcement
- Add `artifacts` schema and runtime persistence
- Add artifact contract validation
- Implement `parallel` node execution semantics
- Implement reusable `subgraph` node semantics
- Add retry and fallback DSL to graph nodes
- Formalize condition expression grammar and deep ambiguity analysis
- Add `evals` schema and `abp eval` command
- Add PRD-ready example blueprint with harness coverage

## Suggested Milestone Labels

- `vnext-m0-integrity`
- `vnext-m1-harness`
- `vnext-m2-contracts`
- `vnext-m3-policies`
- `vnext-m4-artifacts`
- `vnext-m5-semantics`
- `vnext-m6-evals`

## Definition of Done

ABP vNext is not done when new syntax exists. It is done when:

- declared behavior is actually enforced
- unsupported semantics fail loudly
- workflows can run in deterministic test mode
- traces can be replayed and diffed
- contracts validate state and outputs
- PRD-style artifacts can be produced and checked
- policy violations are surfaced before release

## Recommended Immediate Next Action

Start with a short branch focused only on:

- `max_graph_steps`
- unsupported node type failure
- trace schema draft

That branch is small, de-risks the architecture, and prepares the path for Harness MVP without prematurely expanding syntax.
