# Verification notes — task list baseline

Status snapshot taken before the runtime-guarantee work (supervisor parity,
`max_turns`, `abp diff`). Each claim was checked against the code at
`2b7b0c1` (2026-10-19), not just the docs.

## Baseline

| Check | Result |
|---|---|
| `ruff check .` | clean |
| `mypy src` | clean (70 files) |
| `pytest --cov=agent_blueprint` | 729 passed, coverage 87% (CI floor: 79%) |
| `abp test docs/assets/gate-demo/support.yml` | 2/2 scenarios pass on real LangGraph 1.0.10 |

## Claims

**No turn limit on inner loops — confirmed.** `nodes.py.j2` contains three
`while True:` loops: the LLM retry loop (bounded by `retry.max_attempts`), the
supervisor ReAct loop and the agent ReAct loop. The last two have no turn
counter; `grep -rn max_turns src` is empty. The supervisor's `max_iterations`
counts supervisor *invocations* (via `_abp_supervisor_iters`), not LLM turns
inside one invocation. Only `policies.budgets` can stop such a loop today, and
only indirectly.

**Supervisor skips agent-node guarantees — confirmed.** Comparing the calls
made by a generated supervisor function with those of an agent node declaring
the same features (tools, HITL, `produces`, `output_contract`, invariants,
low-confidence escalation), these helpers run only on agent nodes:

| Guarantee | Agent node | Supervisor node |
|---|---|---|
| `contracts.nodes.*.requires` | ✅ | ✅ |
| tool argument validation (`validate_tool_arguments`) | ✅ | ✅ |
| budgets (`register_llm_budget_usage`) | ✅ | ✅ |
| `contracts.state.invariants` (`_check_state_invariants`) | ✅ | ❌ |
| `produces` (`_require_produced_fields`) | ✅ | ❌ |
| `output_contract` (`_validate_node_output_contract`) | ✅ | ❌ |
| artifacts (`_write_node_artifacts`) | ✅ | ❌ |
| `human_in_the_loop` (`_require_human_review`) | ✅ | ❌ |
| low-confidence escalation (`_apply_low_confidence_escalation`) | ✅ | ❌ |

Neither `abp lint` nor `abp doctor` warns about these, so the declarations are
silently ignored on supervisor nodes.

**Plain `type: function` nodes are stubs — confirmed.** Outside the subgraph
entry/exit adapters, a function node renders `# TODO: Implement action: ...`
and returns `{}`. No lint or doctor finding flags it.

**`abp diff` does not exist — confirmed.** `cli/app.py` registers validate,
schema, generate, inspect, fix, init, lint, doctor, run, test, eval, gate,
traces, deploy, package and github.

**Version 0.4.0 with a large `[Unreleased]` section — confirmed.** The section
includes two breaking removals (`abp editor`, `examples/`), so the next release
is 0.5.0.

**Machine-readable output only on `eval` and `gate` — confirmed.** No other
command takes `--json`.

**Tool-usage policies apply to supervisor tool calls — confirmed, with a
caveat.** `tests/test_supervisor.py::TestSupervisorToolPolicies` shows the
supervisor's regular tool calls go through `validate_tool_arguments` and, on
real LangGraph, emit a `policy_violation` event attributed to the supervisor
node.

## New finding: tool-usage violations do not stop the run

`_policy_violation` in `tools.py.j2` raises `RuntimeError`. Both ReAct loops
(agent and supervisor) wrap the tool call in
`except PermissionError: raise / except Exception: content = f"Tool error: {exc}"`,
so the violation becomes a tool message to the model and the loop continues.
Reproduced with the gate demo plus `require_explicit_arguments: true` and a
bogus argument: the process exits 0, the trace holds `policy_violation`
followed by `node_finished`, and the agent answers normally.

This contradicts `docs/runtime-guarantees.md` ("runaway ReAct loops fail
fast"): once `max_calls_per_node` is hit, every further call is a violation
that is fed back to the model, and nothing ends the loop if the model keeps
calling. Approval violations are unaffected (they raise `PermissionError`,
which is re-raised). To be resolved together with `max_turns`.

## Resolution (phase B)

- Supervisor parity: every guarantee in the table above is now enforced on
  supervisors (`tests/test_supervisor.py::TestSupervisorGuaranteeParity`,
  runtime cases in `TestSupervisorGuaranteesRuntime`). See
  `docs/workflow-nodes.md` for which checks run on delegation vs. answer.
- Tool-usage violations raise `ToolPolicyViolation`, which both ReAct loops
  re-raise, so the run stops.
- Inner loops can be bounded with `max_turns` / `settings.default_max_turns`;
  the `unbounded-tool-loop` lint warns when nothing bounds them.
- Plain function nodes still generate a stub, but `abp doctor` now warns.
