# Sandboxed Runs

`abp run` normally generates the blueprint into a temp directory and executes it
in your current Python environment. With the sandbox enabled, the generated
project is instead built into a container image and executed with
`<engine> run --rm` — isolated from your host environment and filesystem.

```bash
abp run my-agent.agents.yaml "hello" --sandbox
abp run my-agent.agents.yaml "hello" --sandbox --engine podman
```

## Declarative configuration

The sandbox is configured in the blueprint under the top-level `run:` section,
so the execution policy travels with the spec:

```yaml
run:
  sandbox:
    enabled: true        # plain `abp run` sandboxes by default
    engine: auto         # auto | docker | podman
    image: python:3.11-slim
    network: bridge      # none | bridge | host
    memory: 512m         # optional container memory limit
    cpus: 1.0            # optional CPU limit
    env_passthrough:     # extra env vars forwarded into the container
      - MY_CUSTOM_TOKEN
```

CLI flags always win over the blueprint: `--sandbox` / `--no-sandbox` override
`enabled`, and `--engine` overrides `engine`.

## Engine selection

With `engine: auto` (the default), podman is probed first — it is rootless and
daemonless, which fits the sandbox goal — then docker. An explicit `engine:`
or `--engine` value is used as-is and errors if that runtime is unavailable.

## How it runs

1. The blueprint is generated into a temp directory (same as a normal run).
2. A run-focused `Dockerfile` is rendered (`FROM <image>`, `pip install`
   requirements, `ENTRYPOINT _abp_runner.py`) and the image is built as
   `abp-run-<blueprint-name>:latest`. Layer caching keeps repeat runs fast —
   the pip layer only rebuilds when requirements change.
3. The container runs with `--rm`. One-shot input is passed as an argument;
   without input the REPL is attached interactively (`-i`).
4. The temp directory is mounted at `/abp-out` inside the container so the
   trace file (`abp_trace.json`) lands back on the host, keeping
   `run_capture` and trace tooling working unchanged.

## Environment forwarding

The host environment is **not** inherited. The container only receives an
allowlist:

- secrets the blueprint requires (`model_providers.*.api_key_env`, tool auth
  env vars),
- conventional API keys for providers the graph actually uses
  (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
  `AZURE_OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT`) — even when no explicit
  `model_providers` entry exists,
- anything listed in `env_passthrough`,
- ABP runtime vars (`ABP_THREAD_ID`, `ABP_TOOL_APPROVAL_MODE`,
  `ABP_TRACE_FILE`).

Values resolve from the host environment first, then the `--env` file —
matching the non-sandboxed runner. Unset allowlisted variables produce a
warning and are skipped.

When the blueprint's `settings.ollama_base_url` points at localhost and the
network is not `host`, the URL is rewritten to the engine's host alias
(`host.docker.internal` / `host.containers.internal`), matching the local
container deployers.

## Notes

- `--install/--no-install` is ignored in sandbox mode: dependencies are
  installed at image build time, never on the host.
- `network: none` gives the strongest isolation but blocks hosted LLM APIs;
  it is practical mainly with stubbed tools and mock/replay harness modes.
