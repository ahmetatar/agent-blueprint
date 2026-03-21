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

## Extended Thinking (Claude)

The `reasoning` field on an agent enables [Claude's extended thinking](https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking) feature. The model reasons internally for up to `budget_tokens` tokens before producing its response. **Only supported for Anthropic models.**

```yaml
model_providers:
  claude:
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY

agents:
  deep_thinker:
    model: "claude-opus-4-6"
    model_provider: claude
    system_prompt: |
      You are an expert analyst. Think carefully before answering.
    reasoning:
      enabled: true
      budget_tokens: 10000   # tokens reserved for internal reasoning (default: 8000)
```

`abp generate` will produce the following in `nodes.py`:

```python
llm = ChatAnthropic(
    model="claude-opus-4-6",
    temperature=1,  # required for extended thinking
    thinking={"type": "enabled", "budget_tokens": 10000},
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Activate extended thinking |
| `budget_tokens` | int | `8000` | Max tokens the model may use for internal reasoning |

> **Note:** Extended thinking forces `temperature=1` (required by the Anthropic API). The `temperature` field on the agent is ignored when `reasoning.enabled: true`.

> **Provider restriction:** `reasoning` is only effective when the agent's resolved provider is `anthropic`. For other providers the field is ignored in generated code. Both `abp validate` and `abp generate` will print a yellow warning if `reasoning.enabled: true` is set on a non-Anthropic agent:
>
> ```
> ⚠  Warning: Node 'thinker': reasoning.enabled is set but provider is 'openai'
>              — extended thinking is only supported for Anthropic models and
>              will be ignored in generated code.
> ```
