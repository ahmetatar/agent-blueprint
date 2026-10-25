# ADR 0001: The blueprint is the single source of truth

- **Status:** Accepted
- **Date:** 2026-10-25

## Context

In this ADR, *blueprint* and *spec* mean the validated `BlueprintSpec`: the
YAML file after env interpolation (`utils/yaml_loader.py`) and Pydantic
validation (`models/blueprint.py`). YAML is only the authoring format.
`abp schema` publishes the same model as JSON Schema. Code referenced through
`impl` is outside the spec, but the contracts, policies and approvals around
it are inside. The generated project is output and is never the source.

Every guarantee ABP offers comes from a declaration in the blueprint.
Contracts, policies, retry, escalation and `max_turns` are compiled into the
generated runtime. Harness scenarios run against the declared graph. Lint
checks (route overlap, `unbounded-loop`, `parallel-branch-conflict`) read the
declared structure. The planned `abp diff` compares two declarations. The spec
feeds the whole loop of tests, enforcement and gating. That is what sets ABP
apart from eval and guardrail tools that only watch runs.

The current plan pushes ABP toward working with systems people already have:
coding-agent integration (P5), a runtime library (P6) and a "wrap mode" spike
(T6.4) that would add policies and traces to an existing LangGraph graph with
no blueprint. A graph without a spec can only get the subset of guarantees
that can be enforced from outside: budgets, tool limits, approvals and
traces. Deterministic scenarios, static diff, compile-time lint, escalation
routing and contracts would all be missing.

Shipping that path would split ABP into two products with different
guarantees, and the weaker one would compete with tools that already do it
well.

## Decision

1. **The blueprint comes first.** A new capability is first something a
   blueprint can declare. Its enforcement, lint, tests, diff and trace events
   are derived from that declaration.
2. **There is no spec-less path.** ABP does not ship a supported API or command
   that applies its guarantees to an agent without a blueprint, for example
   `govern(graph, policies=...)`. There is one path and one set of guarantees.
3. **Existing code comes in through the blueprint.** User code joins a
   blueprint through declared extension points (`impl` on function tools and
   retrievers today), behind declared contracts and policies. If integrating
   existing code needs a new extension point, it is added to the schema and
   gets the same validation, lint and enforcement as everything else.
4. **Refactors that the spec cannot see are allowed.** Moving the generated
   runtime into a library (T6.1–T6.3) is allowed when, for the same blueprint,
   the generated behaviour and trace output are unchanged (golden
   equivalence). The library is an implementation detail of generated
   projects. Its API is internal and is not documented or supported for direct
   use.
5. **Consumers do not change what ABP is.** Coding agents, CI systems and
   other harnesses may drive `abp` (`--json`, skills, AGENTS snippets). They
   write and verify blueprints. They do not get a way around them.

## Consequences

- T6.4 (wrap mode) is dropped in its spec-less form. Any future work on
  existing LangGraph code must fit point 3.
- The README and positioning keep describing ABP as a tool for agents defined
  with ABP. The "layer for any agent" claim is not made.
- A feature that only makes sense without a spec does not go into ABP, even
  when there is demand for it.
- Review question for every feature PR: *Where is this declared in the
  blueprint, and what derives from that declaration?*
- Adoption is slower: teams with existing agents have to write a blueprint
  before they get any guarantee.

Changing this decision requires a new ADR that supersedes this one.
