# Reasoning Patterns

`agent-blueprint` supports two different reasoning approaches:

1. Graph-level reasoning patterns: multi-node flows such as Chain-of-Thought, ReAct, and Self-Reflection.
2. Native model thinking: a single LLM call where provider-specific thinking/reasoning kwargs are passed to the selected LangChain adapter.

ABP does not know whether a specific model supports native reasoning. The `model_provider` selects the LangChain adapter class; `reasoning.params` is forwarded as-is to that adapter's constructor.

## Chain-of-Thought (CoT)

Split reasoning and answering into two sequential agent nodes. The first node writes its thinking to state; the second node reads it and produces the final answer.

```yaml
agents:
  think:
    model: "claude-opus-4-6"
    system_prompt: |
      Analyse the question step by step. Write your reasoning process.
  answer:
    model: "claude-opus-4-6"
    system_prompt: |
      Given the analysis in the conversation, produce the final answer.

graph:
  entry_point: think
  nodes:
    think:
      agent: think
      description: "Reasoning step"
    answer:
      agent: answer
      description: "Final answer step"
  edges:
    - from: think
      to: answer
    - from: answer
      to: END
```

## ReAct (Reason → Act → Observe)

Implement the ReAct loop by connecting reasoning, tool-calling, and observation nodes in a cycle. The loop exits when the agent signals completion via a state field.

```yaml
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    done:
      type: boolean
      default: false

agents:
  reason:
    model: "claude-opus-4-6"
    system_prompt: |
      Decide which tool to call next. Set done=true when no more steps are needed.
    tools: [web_search, calculator]

graph:
  entry_point: reason
  nodes:
    reason:
      agent: reason
      description: "Decide next action"
  edges:
    - from: reason
      to:
        - condition: "state['done'] == True"
          target: END
        - default: reason    # loop until done
```

## Self-Reflection

Generate a draft, critique it, then refine — repeat until quality meets a threshold stored in state.

```yaml
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    quality_score:
      type: integer
      default: 0

agents:
  draft:
    model: "gpt-4o"
    system_prompt: "Write an initial answer."
  critique:
    model: "gpt-4o"
    system_prompt: |
      Rate the previous answer 1–10 and store it in quality_score.
      Suggest specific improvements.
  refine:
    model: "gpt-4o"
    system_prompt: "Rewrite the answer incorporating the critique."

graph:
  entry_point: draft
  nodes:
    draft:
      agent: draft
      description: "First draft"
    critique:
      agent: critique
      description: "Evaluate draft quality"
    refine:
      agent: refine
      description: "Improve based on critique"
  edges:
    - from: draft
      to: critique
    - from: critique
      to:
        - condition: "state['quality_score'] >= 8"
          target: END
        - default: refine
    - from: refine
      to: critique    # loop until quality_score >= 8
```

## Native Model Thinking

The `reasoning` field marks an agent as using a model's native thinking capability. `params` is a raw dict of kwargs passed directly to the LangChain chat model constructor. ABP does not validate whether those params are semantically correct for your model.

Adapter selection remains explicit:

```yaml
model_providers:
  claude:
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY
```

The `provider` value chooses the generated LangChain class (`ChatAnthropic` here). The reasoning params are then passed through unchanged.

```yaml
agents:
  deep_thinker:
    model: "claude-opus-4-6"
    model_provider: claude
    system_prompt: |
      You are an expert analyst. Think carefully before answering.
    reasoning:
      enabled: true
      params:              # passed as-is to the LLM constructor
        thinking:
          type: enabled
          budget_tokens: 10000
        temperature: 1
```

`abp generate` will produce the following in `nodes.py`:

```python
llm = ChatAnthropic(
    model="claude-opus-4-6",
    temperature=1,
    thinking={'type': 'enabled', 'budget_tokens': 10000},
)
```

### Provider examples

These examples are pass-through constructor kwargs. ABP does not hard-code frontier model names or capability lists.

**Anthropic-style thinking:**
```yaml
reasoning:
  enabled: true
  params:
    thinking:
      type: enabled
      budget_tokens: 10000
    temperature: 1
```

**OpenAI Responses API reasoning via LangChain:**
```yaml
reasoning:
  enabled: true
  params:
    reasoning:
      effort: medium
      summary: auto
```

**Google/Gemini-style thinking:**
```yaml
reasoning:
  enabled: true
  params:
    thinking_budget: 8000
    include_thoughts: false
```

**Ollama / local reasoning models:**
```yaml
reasoning:
  enabled: true
  params:
    num_ctx: 8192
    num_predict: 2048
```

**Custom / fine-tuned models:**
```yaml
reasoning:
  enabled: true
  params:
    think_mode: deep
    chain_of_thought_tokens: 5000
```

### Generic LLM Params

Use `llm_params` for non-reasoning constructor kwargs:

```yaml
agents:
  analyst:
    model: "some-model"
    model_provider: my_provider
    llm_params:
      timeout: 60
      max_retries: 3
```

Merge order is deterministic: provider base args, agent defaults, `model_providers[*].extra`, `agent.llm_params`, then `reasoning.params`. Later values override earlier values. For example, `reasoning.params.temperature` overrides `agent.temperature`.

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Mark this agent as a reasoning agent |
| `params` | dict | `{}` | Raw kwargs forwarded to the LLM constructor |
| `llm_kwargs` | dict | `{}` | Legacy alias for `params` |

> **Warning:** If `reasoning.enabled: true` is set but `params` is empty, both `abp validate` and `abp generate` will print a warning. If no `model_provider`, `settings.default_model_provider`, or `provider/model` prefix is present, ABP will warn that the OpenAI adapter is being used by default.
