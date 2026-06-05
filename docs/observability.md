# Observability (OpenTelemetry Export)

Generated agents already record a detailed JSON trace (`abp_trace.json`) used
by the harness, gate, and the trace→eval flywheel. The `observability:`
section adds a second output: the same events exported live as
**OpenTelemetry spans**, so production runs are visible in any OTLP-compatible
backend (Jaeger, Grafana Tempo, Datadog, Honeycomb, LangSmith, Langfuse, …).

The JSON trace is unchanged — OTel export is a pure observer on top of it.

## Declarative configuration

```yaml
observability:
  tracing:
    enabled: true
    exporter: otlp            # otlp | console (stdout, for local debugging)
    endpoint: http://localhost:4318   # optional; OTEL_* env vars win
    protocol: http/protobuf   # http/protobuf | grpc
    service_name: my-agent    # defaults to the blueprint name
    sample_ratio: 1.0         # 0.0–1.0, ParentBased(TraceIdRatioBased)
```

When `enabled: true`, generation adds:

- `_abp_otel.py` — the bridge module,
- `opentelemetry-sdk` (+ the matching OTLP exporter package) to
  `requirements.txt`,
- a one-line `init_tracing()` call in `main.py`.

When `enabled` is false or the section is absent, generated output is
identical to before — no new imports, no new dependencies.

## Span model

| ABP concept | OTel mapping |
|---|---|
| run | root span `abp.run` (`abp.run_id`, `abp.blueprint`, `abp.mode`, …) |
| node execution | child span `abp.node <id>` |
| agent node LLM info | `gen_ai.operation.name`, `gen_ai.system`, `gen_ai.request.model` |
| token usage / cost | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `abp.usage.total_tokens`, `abp.usage.cost_usd` |
| route / escalation | `abp.route`, `abp.escalated` on the node span |
| tool calls, retries, artifacts, subgraphs | span events on the active span |
| `tool_failed`, `policy_violation`, `contract_failed`, `retry_exhausted` | span event **plus** span status `ERROR` |
| run result | root span status `OK` / `ERROR` |

### Privacy

Only event names, ids, routes, and **state hashes** are exported — never
prompts, completions, tool arguments, or state contents. This matches the
OTel GenAI semantic conventions' default of not capturing message content.

## Runtime precedence

Standard OpenTelemetry environment variables always win over blueprint
values:

- `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`
  override `endpoint`
- `OTEL_SERVICE_NAME` overrides `service_name`
- `OTEL_TRACES_SAMPLER` overrides `sample_ratio`
- `OTEL_EXPORTER_OTLP_HEADERS` etc. are read by the SDK as usual

`ABP_OTEL=off` disables export entirely at runtime (kill switch).

## Failure behavior

Observability never breaks the agent:

- missing `opentelemetry-*` packages → one warning, export disabled,
  run continues;
- exporter/init errors → one warning, export disabled;
- exceptions inside the bridge are swallowed per event.

## Quick local test

```bash
# 1. enable tracing with the console exporter in your blueprint
# 2. generate + run; spans print to stdout
abp run my-agent.yml "hello"
```

Or point `endpoint` at a local collector (e.g. `docker run -p 4318:4318
otel/opentelemetry-collector`) and use the default `otlp` exporter.
