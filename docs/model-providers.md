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
    extra:                        # optional raw constructor kwargs
      timeout: 60
```

## Provider Reference

| Provider | Required fields | Optional fields |
|---|---|---|
| `openai` | — | `api_key_env`, `extra` |
| `anthropic` | — | `api_key_env`, `extra` |
| `google` | — | `api_key_env`, `extra` |
| `ollama` | `base_url` | `extra` |
| `azure_openai` | `base_url`, `deployment` | `api_key_env`, `api_version`, `extra` |
| `bedrock` | — | `region`, `aws_profile_env`, `extra` |
| `openai_compatible` | `base_url` | `api_key_env`, `extra` |

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
