# Native Reasoning vs Graph-Level Reasoning

Reasoning-enabled models and graph-level reasoning patterns solve different problems.

Native reasoning improves the quality of a single model call. Graph-level reasoning controls the workflow around multiple calls. Do not split a task into reasoning and answering nodes just to simulate thinking when the selected model already supports native reasoning.

## Native Reasoning

Native reasoning means the model performs its own internal thinking inside one LLM call. In ABP, this is configured with `agents[*].reasoning.params`.

```yaml
agents:
  analyst:
    model: "your-reasoning-model"
    model_provider: primary
    reasoning:
      enabled: true
      params:
        reasoning:
          effort: high
```

Use native reasoning when:

- The task has one agent and one final answer.
- You do not need to store intermediate reasoning in state.
- Tool usage is absent or simple.
- The goal is better answer quality, not workflow control.
- Latency and cost matter.
- You do not need an auditable reasoning trace.

A strong reasoning model may internally plan, decompose, check its answer, or revise before responding. ABP does not control that internal process and does not assume which pattern the model uses.

## Graph-Level Reasoning

Graph-level reasoning means reasoning is represented explicitly in the graph. Each step is a node, edge, condition, tool call, or state update.

```yaml
graph:
  entry_point: research
  nodes:
    research:
      agent: researcher
    verify:
      agent: verifier
    answer:
      agent: writer
  edges:
    - from: research
      to: verify
    - from: verify
      to: answer
    - from: answer
      to: END
```

Use graph-level reasoning when:

- Tool calls must happen in a controlled sequence.
- One node's output must be stored in state for another node.
- Quality gates, retries, fallback, or human approval are required.
- Multiple agents have distinct roles.
- The workflow needs deterministic routing.
- Each step must be observable, testable, or auditable.

Graph-level reasoning usually performs multiple LLM calls because each reasoning step is a graph node.

## Does A Native Reasoning Model Already Apply Patterns?

Usually, yes, but not in a way ABP can rely on.

A reasoning-capable model may internally use behaviors similar to planning, decomposition, self-checking, or ReAct-style deliberation. That does not make it equivalent to an ABP graph pattern.

Internal model reasoning is:

- Not guaranteed.
- Not deterministic.
- Not directly inspectable as graph state.
- Not a substitute for explicit tool orchestration.
- Not a substitute for human approval, routing, retries, or quality gates.

A model may think in a ReAct-like way, but an ABP ReAct loop is different: the graph controls when tools run, where observations are stored, when the loop exits, and what happens next.

## When To Use Only Native Reasoning

Use only native reasoning for simple single-call tasks:

```yaml
agents:
  analyst:
    model: "your-reasoning-model"
    model_provider: primary
    system_prompt: |
      Analyze the request carefully and return the final answer.
    reasoning:
      enabled: true
      params:
        reasoning:
          effort: high
```

This is usually better than creating a `think -> answer` graph just to force a visible thinking step.

Benefits:

- Lower latency.
- Lower cost.
- Less graph complexity.
- Less state bloat.
- Lower risk of exposing intermediate reasoning text.

## When To Use Graph-Level Reasoning

Use graph-level reasoning when reasoning is part of the product workflow, not just the model's answer quality.

Examples:

- Research, then verify, then answer.
- Draft, critique, then refine until a quality threshold is met.
- Route to billing, technical support, or sales based on state.
- Call tools repeatedly until enough evidence is collected.
- Ask a human before a dangerous tool call.
- Fall back to another model or path when validation fails.

In these cases, the graph is not simulating thinking. It is enforcing process.

## When To Combine Both

Combining native reasoning and graph-level reasoning can be useful when each graph node benefits from stronger local reasoning.

Good combinations:

- A router node uses native reasoning to choose a route, while the graph applies the route deterministically.
- A researcher node uses native reasoning while the graph controls the research, verification, and writing stages.
- A verifier node uses native reasoning to evaluate an answer before the graph decides whether to retry.
- A ReAct loop controls tool execution while the agent uses native reasoning to choose the next tool call.

The graph controls the workflow. Native reasoning improves each model decision.

## Anti-Patterns

Avoid this pattern when the only goal is to make the model think:

```text
think_node -> answer_node
```

If both nodes use the same reasoning-capable model, this often adds cost and latency without improving control.

Problems:

- Extra LLM calls.
- More tokens.
- More latency.
- Intermediate reasoning may leak into state or output.
- The second node may overfit to flawed visible reasoning from the first node.
- The graph becomes more complex without adding meaningful orchestration.

Use this pattern only if the thinking output is a required artifact or must be reviewed, stored, routed, or audited.

## Practical Rule

| Need | Recommended approach |
|---|---|
| Better single-call answer quality | Native reasoning |
| Explicit workflow control | Graph-level reasoning |
| Tool loop with observations | Graph-level reasoning, optionally with native reasoning per node |
| Multi-agent process | Graph-level reasoning, optionally with native reasoning per agent |
| Quality gate, retry, or fallback | Graph-level reasoning |
| Visible or auditable intermediate artifacts | Graph-level reasoning |
| Hidden internal thinking | Native reasoning |

## Design Principle For ABP

ABP should keep these concepts separate:

- `reasoning.params` configures native model behavior for one LLM call.
- `graph` configures orchestration behavior across nodes.

A reasoning-enabled model can make a node smarter. A graph pattern makes the system more controlled.
