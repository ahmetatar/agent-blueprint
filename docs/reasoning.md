# Reasoning

`agent-blueprint` supports reasoning in two separate ways:

1. Graph-level reasoning patterns: explicit multi-step orchestration such as planning, ReAct loops, critique/refine, and self-reflection.
2. Native model thinking: a single LLM call where model/provider-specific kwargs are forwarded to the selected LangChain adapter.

ABP intentionally does not maintain a model capability registry. It does not know whether a model supports reasoning, thinking, reasoning summaries, hidden reasoning tokens, or visible thought blocks. The user chooses the provider adapter and passes the correct parameters for that adapter.

## Responsibility Split

| Concern | Owner |
|---|---|
| Select LangChain adapter class | ABP, via `model_providers[*].provider` |
| Select actual model | User, via `agents[*].model` |
| Know whether the model supports native reasoning | User/provider documentation |
| Know provider-specific reasoning kwargs | User/provider documentation |
| Forward kwargs into generated code | ABP |
| Validate semantic correctness of reasoning kwargs | Not ABP |

Example:

```yaml
model_providers:
  local_llm:
    provider: ollama
    base_url: "http://localhost:11434"

agents:
  thinker:
    model: "deepseek-r1:8b"
    model_provider: local_llm
    reasoning:
      enabled: true
      params:
        num_ctx: 8192
        num_predict: 2048
```

ABP sees `provider: ollama` and generates `ChatOllama(...)`. It does not know whether `deepseek-r1:8b` reasons natively or whether `num_ctx` and `num_predict` are sufficient.

## Native Model Thinking

Use native thinking when the selected model can perform reasoning inside one LLM call. This avoids splitting thinking and answering into separate graph nodes.

```yaml
model_providers:
  primary:
    provider: openai
    api_key_env: OPENAI_API_KEY

agents:
  analyst:
    model: "your-reasoning-model"
    model_provider: primary
    system_prompt: |
      Solve the task carefully. Return only the final answer.
    reasoning:
      enabled: true
      params:
        reasoning:
          effort: high
          summary: auto
```

Generated code passes `reasoning.params` directly into the selected adapter constructor:

```python
llm = ChatOpenAI(
    model='your-reasoning-model',
    temperature=0.7,
    reasoning={'effort': 'high', 'summary': 'auto'},
)
```

The shape of `params` is deliberately generic. It must match what the selected LangChain chat class accepts.

## Adapter Selection

The `model_provider` field selects the provider configuration. The provider configuration selects the LangChain adapter class.

| Provider | Generated adapter |
|---|---|
| `openai` | `ChatOpenAI` |
| `openai_compatible` | `ChatOpenAI` |
| `anthropic` | `ChatAnthropic` |
| `google` | `ChatGoogleGenerativeAI` |
| `ollama` | `ChatOllama` |
| `azure_openai` | `AzureChatOpenAI` |
| `bedrock` | `ChatBedrock` |

Reasoning params do not affect adapter selection. They are only kwargs passed to the adapter after it has been selected.

If `reasoning.enabled: true` is set but no `model_provider`, `settings.default_model_provider`, or `provider/model` prefix is available, ABP falls back to the default OpenAI adapter and emits a warning.

## Reasoning Params Examples

These examples show pass-through kwargs only. They are not capability declarations, and ABP does not verify that the selected model supports them.

### OpenAI-Style Reasoning

```yaml
reasoning:
  enabled: true
  params:
    reasoning:
      effort: medium
      summary: auto
```

### Anthropic-Style Thinking

```yaml
reasoning:
  enabled: true
  params:
    thinking:
      type: enabled
      budget_tokens: 10000
    temperature: 1
```

### Google/Gemini-Style Thinking

```yaml
reasoning:
  enabled: true
  params:
    thinking_budget: 8000
    include_thoughts: false
```

### Ollama Or Local Model Params

```yaml
reasoning:
  enabled: true
  params:
    num_ctx: 8192
    num_predict: 2048
```

### Custom Adapter Params

```yaml
reasoning:
  enabled: true
  params:
    think_mode: deep
    chain_of_thought_tokens: 5000
```

## Generic LLM Params

Use `llm_params` for constructor kwargs that are not specifically tied to reasoning.

```yaml
agents:
  analyst:
    model: "some-model"
    model_provider: primary
    temperature: 0.2
    llm_params:
      timeout: 60
      max_retries: 3
```

Use `model_providers[*].extra` for provider-wide constructor kwargs shared by all agents using that provider.

```yaml
model_providers:
  primary:
    provider: openai
    api_key_env: OPENAI_API_KEY
    extra:
      timeout: 60
```

## Merge Order

Generated constructor kwargs are merged in this order:

1. Provider base args, such as `model`, `base_url`, `api_version`, or `region_name`.
2. Agent defaults, such as `temperature` and `max_tokens`.
3. `model_providers[*].extra`.
4. `agents[*].llm_params`.
5. `agents[*].reasoning.params`, if `reasoning.enabled` is true.

Later values override earlier values. For example, this uses `temperature=1` in generated code:

```yaml
agents:
  thinker:
    model: "some-model"
    model_provider: primary
    temperature: 0.2
    reasoning:
      enabled: true
      params:
        temperature: 1
        reasoning:
          effort: high
```

## Fields

### `agents[*].reasoning`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | Marks the agent as using native model reasoning params |
| `params` | dict | `{}` | Raw kwargs forwarded to the selected LangChain adapter constructor |

### `agents[*].llm_params`

| Field | Type | Default | Description |
|---|---|---|---|
| `llm_params` | dict | `{}` | Raw non-reasoning kwargs forwarded to the selected LangChain adapter constructor |

## Graph-Level Reasoning Patterns

Graph-level patterns are explicit orchestration patterns. They are useful when you want reasoning steps to be modeled as nodes, stored in state, reviewed, looped, or routed.

These patterns usually perform multiple LLM calls because each reasoning step is a graph node.

## Chain Of Thought As Graph Nodes

Split reasoning and answering into two sequential agent nodes. The first node writes analysis into the conversation or state; the second node produces the final answer.

```yaml
agents:
  think:
    model: "openai/gpt-4o"
    system_prompt: |
      Analyze the question step by step. Write the analysis needed to answer.
  answer:
    model: "openai/gpt-4o"
    system_prompt: |
      Use the previous analysis to produce the final answer.

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

## ReAct Loop

Use a loop when the agent should reason, call tools, observe results, and continue until a state condition is met.

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
    model: "openai/gpt-4o"
    system_prompt: |
      Decide the next tool call. Set done=true when no more steps are needed.
    tools: [web_search, calculator]

graph:
  entry_point: reason
  nodes:
    reason:
      agent: reason
      description: "Reason and act"
  edges:
    - from: reason
      to:
        - condition: "state['done'] == True"
          target: END
        - default: reason
```

## Self Reflection

Generate a draft, critique it, refine it, and repeat until a quality threshold is met.

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
    model: "openai/gpt-4o"
    system_prompt: "Write an initial answer."
  critique:
    model: "openai/gpt-4o"
    system_prompt: |
      Rate the previous answer from 1 to 10 and store it in quality_score.
      Suggest specific improvements.
  refine:
    model: "openai/gpt-4o"
    system_prompt: "Rewrite the answer using the critique."

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
      to: critique
```

## Choosing The Right Approach

| Need | Use |
|---|---|
| One model call, provider-native reasoning | `reasoning.enabled` with `reasoning.params` |
| Explicit reasoning steps in the graph | Graph-level reasoning pattern |
| Tool loop with observations | ReAct-style graph loop |
| Review and improve output over multiple passes | Self-reflection graph |
| Provider-specific constructor configuration | `llm_params`, `model_providers[*].extra`, or `reasoning.params` |
