# Model Providers

Defines named model provider connections. Agents reference these by name via `model_provider`. If omitted, the generated code assumes the framework's default provider resolution (e.g. `OPENAI_API_KEY` environment variable).

## Configuration

```yaml
model_providers:
  openai_gpt:
    provider: openai
    api_key_env: OPENAI_API_KEY

  gemini:
    provider: google
    api_key_env: GOOGLE_API_KEY

  local_ollama:
    provider: ollama
    base_url: "http://localhost:11434"   # or ${env.OLLAMA_URL}

  azure_gpt4:
    provider: azure_openai
    base_url: "${env.AZURE_OPENAI_ENDPOINT}"
    api_key_env: AZURE_OPENAI_KEY
    deployment: "gpt-4o-prod"
    api_version: "2024-02-01"

  bedrock_claude:
    provider: bedrock
    region: "us-east-1"
    aws_profile_env: AWS_PROFILE

  my_local_server:
    provider: openai_compatible   # Any OpenAI-compatible endpoint (vLLM, LM Studio, etc.)
    base_url: "http://localhost:8000/v1"
    api_key_env: LOCAL_API_KEY    # optional
    pricing:                      # optional, used by budget enforcement
      input_per_1k_tokens_usd: 0.001
      output_per_1k_tokens_usd: 0.002
    extra:                        # optional raw constructor kwargs
      timeout: 60
```

## Provider Reference

| Provider | Required fields | Optional fields |
|---|---|---|
| `openai` | — | `api_key_env`, `pricing`, `extra` |
| `anthropic` | — | `api_key_env`, `pricing`, `extra` |
| `google` | — | `api_key_env`, `pricing`, `extra` |
| `ollama` | `base_url` | `pricing`, `extra` |
| `azure_openai` | `base_url`, `deployment` | `api_key_env`, `api_version`, `pricing`, `extra` |
| `bedrock` | — | `region`, `aws_profile_env`, `pricing`, `extra` |
| `openai_compatible` | `base_url` | `api_key_env`, `pricing`, `extra` |

## Usage in Agents

Agents reference a provider with `model_provider`. If not set, `settings.default_model_provider` is used:

```yaml
settings:
  default_model_provider: openai_gpt

agents:
  researcher:
    model: "gemini-2.0-flash"
    model_provider: gemini         # ← references model_providers.gemini

  writer:
    model: "llama3.2"
    model_provider: local_ollama

  router:
    model: "gpt-4o"
    # model_provider omitted → falls back to settings.default_model_provider
```

`provider` selects the generated LangChain adapter class. ABP does not infer model capabilities from the model name. Provider-specific native reasoning or thinking params belong under `agents[*].reasoning.params` and are forwarded unchanged to the selected adapter.

## Pricing Metadata for Budget Enforcement

`pricing` is optional, but it becomes important when you use:

```yaml
policies:
  budgets:
    max_cost_usd: 1.50
```

If the runtime or fixture already supplies explicit `cost_usd`, ABP uses that.
If not, ABP computes cost from:

- `pricing.input_per_1k_tokens_usd`
- `pricing.output_per_1k_tokens_usd`

Example:

```yaml
model_providers:
  openai_prod:
    provider: openai
    api_key_env: OPENAI_API_KEY
    pricing:
      input_per_1k_tokens_usd: 0.005
      output_per_1k_tokens_usd: 0.015
```

This is mainly useful for:

- CI budget checks
- internal cost ceilings on long workflows
- deterministic mock or replay tests that still enforce `max_cost_usd`
