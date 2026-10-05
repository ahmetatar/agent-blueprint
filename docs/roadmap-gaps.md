# Product Gap Roadmap

Real product gaps found by mapping ABP against agentic-system design practice
(agent necessity, orchestration patterns, state/context, tools, safety, evals,
observability). Guidance-only topics (when to use an agent, how many agents,
production checklist) are intentionally out of scope here; only code gaps are
listed. Items are ordered; each ships as its own focused PR.

Status legend: `todo` · `in progress` · `done`

## Recurring theme

Most gaps below are "declared vs. enforced" cases: the schema exists (or is an
obvious next field) but the generated runtime does not honor it. Every item
must land with validation, generator/runtime enforcement, tests, and docs.

---

## 1. Agent memory enforcement: `todo`

**Gap.** `agents.*.memory` (`conversation_buffer` / `summary` / `vector`,
`max_tokens`, `max_messages`) is accepted by the schema but the generated
`nodes.py` does not apply it. Only thread checkpointing (`memory.backend`) works.

**Not the same as graph state or checkpointing.** Graph state (`state.fields`)
is the shared working memory; checkpointing persists it per `thread_id`. Agent
memory is a *context-view policy*: what the LLM is shown. Today every agent
node passes the full `messages` history to the LLM. Trimming must therefore
apply to the LLM-bound working list only, never to the state `messages`
channel, so checkpoints, replay and harness results stay intact.

Split into two steps:

### 1a. `conversation_buffer` trimming: `done`
- Enforce `max_messages` / `max_tokens` on the working list just before the
  LLM call (keep system prompt; keep tool-call/tool-result pairs together).
- `summary` and `vector` fail at compile/doctor with a clear error (no silent
  no-ops) until 1b.
- Trace event when context is trimmed.

### 1b. `summary` and `vector`: `todo`
- `summary`: rolling summarization (extra LLM call: must count toward budgets
  and stay deterministic in mock/replay).
- `vector`: a retrieval concern overlapping `retrievers`/RAG; implement via
  that plumbing or keep rejected.

### 1c. Tool-output compaction: `done` (agent-level limits; per-tool overrides and `summarize` mode deferred)
- Long-running tool loops grow the working list with bulky tool results. Add a
  per-agent (and optional per-tool) cap on tool-result size applied to the
  LLM-bound working list only (truncate / head+tail / summarize), never to the
  checkpointed `messages` channel.
- Older tool results beyond a configurable window are replaced by a stub
  (tool name + hash + size) so tool-call/tool-result pairs stay valid.
- Trace event on compaction (hashes and sizes only, no content); deterministic
  in mock/replay (summarize mode counts toward budgets like 1b).

**Done when.** No memory option is silently ignored; harness scenario proves
trimming and tool-output compaction without changing checkpointed state;
`docs/memory.md` updated.

## 2. Tool idempotency and side-effect metadata: `in progress`

**Gap.** Tools have `requires_approval` but no notion of side effects or
idempotency, so retry and approval cannot be reasoned about safely.

**Finding.** Node-level `retry` today wraps only the LLM call
(`_invoke_llm_with_retry`); tool executions are never re-run by it. An
`unsafe-retry` lint on node retry would therefore be a false alarm. The real
hazard appears once tools themselves are retried or re-entered (per-tool retry,
fallback routes, verification loops), so the safety checks are tied to those.

### 2a. Metadata and approval implication: `done`
- `tools.*.side_effect: none | read | write | irreversible`, `idempotent: bool`,
  `approval_waived: bool`.
- `irreversible` implies the approval gate unless explicitly waived; retrieval
  tools cannot be `write`/`irreversible`.

### 2b. Per-tool retry and `unsafe-retry`: `todo`
- `tools.*.retry` (max_attempts, backoff, on) enforced around tool execution.
- Validator/lint `unsafe-retry`: retry on a `write`/`irreversible` tool that is
  not declared `idempotent: true` is an error.
- Optional idempotency-key argument.

**Done when.** Retry + approval interplay is checked statically and enforced at
runtime; docs in `docs/tools.md` and `docs/runtime-guarantees.md`.

## 3. Retry exhaustion fallback and parallel failure policies: `todo`

**Gap.** Exhausted retries fail deterministically with no fallback route;
`parallel.failure_policy` only supports `fail_fast`; retry condition is only
`exception`.

**Scope.**
- `retry.on_exhausted: <node>` (fallback route / escalation target), validated
  in `models/blueprint.py` cross-refs.
- Parallel policies: `continue` (collect partial results) and `quorum`.
- Extra retry conditions (e.g. output-contract violation, timeout).
- Trace events for fallback taken.

### 3b. Verification loop (self-check): `todo`
- First-class `verify` block on agent nodes: after the agent produces output,
  run declared checks (output contract, state invariants, custom function, or
  an LLM/rubric judge) and, on failure, re-enter the agent with the failure
  reason as feedback, bounded by `max_attempts`.
- Exhaustion routes through `retry.on_exhausted` (above) instead of failing
  silently; attempts and verdicts are trace events and count toward budgets.
- Reuses the output-contract validator and the rubric scorer from evals, so
  the same check works at runtime, in `abp test`, and in `abp gate`.
- Static guard: the verify loop is a bounded, conditional-exit cycle and must
  not trip `unbounded-loop`.

**Done when.** Failure paths are routable in the graph, a failed verification
is retried with feedback then falls back deterministically, covered by harness
scenarios (mock/replay), and documented in `docs/workflow-nodes.md`.

## 4. Guardrails and prompt-injection defenses: `todo`

**Gap.** Approvals, sandbox, and tool policies exist, but there is no
input/output guardrail layer and no handling of untrusted content.

**Scope.**
- `policies.guardrails`: input and output checks (pattern / schema / custom
  function) with `block | warn | redact` actions.
- Mark tool/retrieval output as untrusted; delimiting in prompts; optional
  rule that untrusted content cannot trigger `write`/`irreversible` tools
  without approval (depends on item 2).
- `policy_violations` eval metric covers guardrail events.

**Done when.** Guardrail hits appear as trace events, are gateable via
`abp gate`, and are documented (new `docs/guardrails.md`).

## 5. Least-privilege tool scoping: `todo`

**Gap.** Agents get tools by list; there is no per-tool scope, per-node tool
narrowing, or credential scoping beyond env vars.

**Scope.**
- Node-level `tools` allowlist narrower than the agent's (deny by default).
- Lint: agent holds tools no reachable node uses / write tools on read-only
  agents.
- Per-tool auth scope declaration surfaced in `abp doctor`.

**Done when.** Runtime rejects out-of-scope calls (trace + policy violation);
lint and doctor findings covered by tests.

## 6. Typed handoff and context packages: `todo`

**Gap.** `handoff` nodes only deliver a notification message; there is no typed
context package (what state slice the receiver gets) and no context budget.

**Scope.**
- `handoff.package`: declared state-field slice / summary with a schema.
- Supervisor worker calls receive only the declared slice (`input_map`-style).
- Optional context-size budget with trace event on truncation.

**Done when.** Receivers see only declared context; contract validation applies
to the package; docs updated.

## 7. Model routing and fallback models: `todo`

**Gap.** Model is chosen statically per agent; pricing metadata exists but is
only used for budget accounting.

**Scope.**
- `fallback_models` per agent (on provider error / rate limit).
- Rule-based routing (condition on state → model) using the safe expression
  parser.
- Budget-aware downgrade when nearing `max_cost_usd`.
- Trace event recording the chosen model and reason.

**Done when.** Deterministic in mock/replay modes; harness assertion for
"model used"; documented in `docs/model-providers.md`.

## 8. Trajectory and multi-turn evals: `todo`

**Gap.** Harness asserts final `route` and unordered `tools_called`; there is no
ordered trajectory match, per-node metrics, or multi-turn scenarios.

**Scope.**
- `expected.trajectory`: ordered node/tool sequence with `exact | subsequence |
  any_order` matching.
- Per-node outcome assertions (state/output at a given node).
- Multi-turn scenarios (sequence of inputs on one thread).
- Gate baseline diffs include trajectory regressions.

**Done when.** New assertions work in mock/replay/live modes and in `abp gate`;
`docs/gate.md` and harness docs updated.

---

## Suggested order rationale

1 → closes the last obvious declared-not-enforced field (1c extends it to
tool-output context growth). 2 → prerequisite for safe retry (3, including the
3b verification loop) and injection rules (4). 4 → 5 build the safety story. 6–8 are
independent and can be reordered by demand.
