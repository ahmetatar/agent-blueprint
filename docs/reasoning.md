# Reasoning Patterns

`agent-blueprint` supports multiple strategies for giving agents explicit reasoning capabilities — from simple multi-node chains to Claude's native extended thinking API.

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

The `reasoning` field on an agent activates a model's built-in thinking capability. `llm_kwargs` is a raw dict of kwargs passed directly to the LangChain LLM constructor — the blueprint makes no assumptions about which parameters are valid. You are responsible for providing the correct kwargs for your provider and model.

```yaml
agents:
  deep_thinker:
    model: "claude-opus-4-6"
    model_provider: claude
    system_prompt: |
      You are an expert analyst. Think carefully before answering.
    reasoning:
      enabled: true
      llm_kwargs:          # passed as-is to the LLM constructor
        thinking:
          type: enabled
          budget_tokens: 10000
        temperature: 1     # Anthropic extended thinking requires temperature=1
```

`abp generate` will produce the following in `nodes.py`:

```python
llm = ChatAnthropic(
    model="claude-opus-4-6",
    thinking={'type': 'enabled', 'budget_tokens': 10000},
    temperature=1,
)
```

### Provider examples

**Anthropic** — [extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking):
```yaml
reasoning:
  enabled: true
  llm_kwargs:
    thinking:
      type: enabled
      budget_tokens: 10000
    temperature: 1   # required by Anthropic API
```

**OpenAI o-series** — reasoning effort:
```yaml
reasoning:
  enabled: true
  llm_kwargs:
    reasoning_effort: medium   # low | medium | high
```

**Google** — Gemini thinking:
```yaml
reasoning:
  enabled: true
  llm_kwargs:
    thinking_config:
      include_thoughts: true
      thinking_budget: 8000
```

**Custom / fine-tuned models** — use whatever kwargs the model accepts:
```yaml
reasoning:
  enabled: true
  llm_kwargs:
    think_mode: deep
    chain_of_thought_tokens: 5000
```

### Temperature handling

When `temperature` is included in `llm_kwargs`, it overrides the agent-level `temperature` setting. When it is absent, the agent's `temperature` (or `settings.default_temperature`) is used as normal.

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Mark this agent as a reasoning agent |
| `llm_kwargs` | dict | `{}` | Raw kwargs forwarded to the LLM constructor |

> **Warning:** If `reasoning.enabled: true` is set but `llm_kwargs` is empty, both `abp validate` and `abp generate` will print a warning — no reasoning parameters will be sent to the model.
