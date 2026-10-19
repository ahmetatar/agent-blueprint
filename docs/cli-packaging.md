# CLI Packaging

`abp package` turns a blueprint into a **pip/pipx-installable command-line
tool** — the local-machine counterpart of `abp deploy` (which exposes the same
agent over HTTP). No trigger code to write: the command *is* the trigger.

## Usage

```bash
abp package my-agent.yml
pipx install ./my-agent-cli      # or: pip install ./my-agent-cli

my-agent "What's the weather?"   # single-shot
my-agent                          # interactive mode (exit/quit to leave)
my-agent --thread-id support-7    # continue a named conversation
my-agent --version
```

| Flag | Default | Description |
|---|---|---|
| `--output-dir` / `-o` | `./<blueprint-name>-cli` | Where to write the package |
| `--target` / `-t` | `langgraph` | Only `langgraph` is supported |
| `--dry-run` | `false` | List the files without writing |

## What gets generated

```
my-agent-cli/
├── pyproject.toml           # name, deps, console-script entry point
├── .env.example             # required environment variables
└── src/my_agent/
    ├── cli.py               # argparse entry point: input, --thread-id, REPL
    ├── main.py              # run(user_input, thread_id) — unchanged agent core
    ├── graph.py / nodes.py / state.py / tools.py
    ├── _abp_trace.py / _abp_harness.py
    └── _abp_otel.py         # only when observability.tracing is enabled
```

Compared to plain `abp generate` output:

- the flat modules move into a `src/<package>/` layout and their
  cross-imports are rewritten to package-relative form (`from .graph import …`);
  your own `impl:` imports are left untouched
- `requirements.txt` is folded into `pyproject.toml` dependencies
- a console script named after the blueprint is registered
  (`my-agent = "my_agent.cli:main"`), built with hatchling

The command name and package name are derived from `blueprint.name`:
`"My Cool Agent"` → command `my-cool-agent`, package `my_cool_agent`.

## Runtime behavior

- **All runtime guarantees apply unchanged** — contracts, approval policies,
  budgets, escalation live inside `main.run()`, independent of the transport.
- **Secrets** come from the environment or a `.env` file in the working
  directory (loaded automatically when `python-dotenv` is present).
- **Conversation persistence**: each invocation is a new process, so
  `memory.backend: in_memory` forgets everything between calls. For a CLI
  tool that remembers conversations across invocations, declare
  `memory.backend: sqlite` — the checkpointer then lives in a local file and
  `--thread-id` picks the conversation to continue.

## When to use which

| You want | Use |
|---|---|
| A local command-line tool | `abp package` |
| An HTTP service in the cloud or a container | [`abp deploy`](deploy.md) |
| A quick local run without installing anything | `abp run` ([sandboxed](sandbox.md) if needed) |
