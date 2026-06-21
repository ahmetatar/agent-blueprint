# Design draft: Agent Executor (`google/ax`) deployer/target

> **Status:** draft / proposal. Not implemented. Some `ax` protocol details below
> are marked **[VERIFY]** because they were read from the README/proto summary and
> must be confirmed against the actual `google/ax` source before coding.

## Why

Google's new agent-runtime stack (Mayıs–Haziran 2026) — **Agent Sandbox (GKE)**,
**Agent Substrate**, **Agent Executor / `ax`** — sits *below* ABP, not against it.
ABP is authoring/compile-time/governance; `ax` is a distributed runtime that gives
**durable execution, session consistency, connection recovery, trajectory
branching, isolation**. The two layers are orthogonal (ABP guarantees the agent is
*well-formed and policy-correct*; `ax` guarantees the process *survives and
scales*). `ax` is explicitly framework-agnostic and runs custom agents
(ADK/LangGraph/A2A) via a gRPC contract — so ABP's generated LangGraph project is a
natural *input* to it.

Goal: a new deploy target `agent-executor` (alias `ax`) that wraps the existing
generated LangGraph project as an `ax` **remote agent** and emits the registration +
container + (optional) Substrate manifest, reusing the `deployers/` pattern.

## What `ax` requires from a remote agent (integration contract)

From `proto/ax.proto` + `python/adk` wrapper:

- A remote agent is a **gRPC server** implementing `AgentService`:
  - `Connect(AgentRequest) returns (stream AgentResponse)` — the agentic turn.
    `AgentRequest.start` (`AgentStart`) carries `agent_id`, opaque `config` bytes,
    and the initial `Message` list (`role` + typed `content`, `internal_only` flag).
    The server streams one or more `AgentOutputs` (repeated `Message`) and
    terminates with `AgentEnd`.
  - `HealthCheck(HealthCheckRequest) returns (HealthCheckResponse)`.
- Default listen port **50051** (`grpc.aio`), async.
- Registered in `ax.yaml` under `registry.remote_agents`:
  ```yaml
  registry:
    remote_agents:
      - id: "<blueprint-slug>"
        name: "<blueprint name>"
        description: "<blueprint description>"
        address: "localhost:50051"
        metadata: { version: "1.0" }
  ```
- **[VERIFY] connection direction.** The proto summary says `Connect` is "agents
  initiate connection to the controller" (reverse-dial / agent streams *to* the
  controller), but the `ax.yaml` registry gives the agent an `address` the
  controller dials. These can't both be the literal transport. Confirm whether:
  (a) the controller dials the agent at `address` and opens the `Connect` stream, or
  (b) the agent dials out and `address` is advertisement/identity only.
  This decides whether our generated server *listens* or *dials* — a load-bearing
  design fork. Read `python/adk/adk_agent_server.py` to settle it.

## The adapter we generate

The generated LangGraph project already exposes a synchronous one-shot:
`main.run(user_input: str, thread_id: str = "default") -> str` (see
`templates/deploy/server.py.j2`, which wraps it in FastAPI `/invoke` + `/health`).
The `ax` adapter is the gRPC analogue of that FastAPI server.

New template: `templates/deploy/ax_server.py.j2` → generated as `_abp_ax_server.py`:

- Implement `AgentServiceServicer`:
  - `Connect`: read `AgentRequest.start`; map the initial messages → a single user
    input string (concatenate/last-user-message — mirror what `/invoke` does);
    derive `thread_id` from `conversation_id` (so `ax` session ↔ LangGraph
    checkpoint thread align); call `main.run(...)`; emit one `AgentOutputs` with an
    `assistant` `Message`; then `AgentEnd`.
    - **v1 (minimal):** one `AgentOutputs` then `AgentEnd` (no token streaming —
      consistent with the existing output-contract / no-streaming decision).
    - **v2 (later):** bridge to LangGraph `.stream(...)` and emit incremental
      `AgentOutputs`; still gated by the contract model.
  - `HealthCheck`: return OK + agent id (reuse the `/health` shape).
- Generate gRPC stubs: either vendor `ax_pb2.py` / `ax_pb2_grpc.py` from `ax.proto`,
  or codegen at build time. **[VERIFY]** whether `ax` ships a pip package exposing
  the servicer base (`python/adk` imports an `ADKAgentServicer` — there may be a
  reusable `ax`/`grpcio`-based base we should build on instead of hand-rolling).
- Entry: `python -m _abp_ax_server --port 50051` (env `PORT`/`AX_PORT` override).

The adapter is **conditional generation** — only emitted for this target, so the
default langgraph output and all goldens/harness/gate stay byte-identical (same
discipline as the OTel bridge in PR-7).

## Deployer wiring (mirrors existing deployers exactly)

1. **Config model** — `models/deploy.py`:
   ```python
   class AgentExecutorDeployConfig(BaseModel):
       mode: str = "container"          # container | substrate (k8s manifest)
       agent_id: str | None = None      # defaults to blueprint slug
       listen_port: int = 50051
       controller_address: str | None = None   # ax controller, if dial-out [VERIFY]
       emit_ax_yaml: bool = True        # render registry block
       # substrate mode:
       namespace: str = "default"
       image: str | None = None
   ```
   Add `agent_executor: AgentExecutorDeployConfig | None = None` to `DeployConfig`.

2. **Deployer** — `deployers/agent_executor.py`, `AgentExecutorDeployer(BaseDeployer)`:
   - `check_prerequisites()`: probe `ax --version` (and `docker info` for container
     mode; `kubectl`/`gcloud container` for substrate mode). Same `_probe` helper.
   - `deploy(code_dir, secrets, *, image_tag, dry_run)`:
     - **container mode:** render an `ax`-aware Dockerfile (CMD = the gRPC server,
       EXPOSE 50051), `docker build`, optionally run locally / push; write `ax.yaml`
       registry block next to it. Return `DeployResult` with the gRPC address.
     - **substrate mode:** additionally render a K8s `Deployment` + `Service`
       manifest into `manifests/` (Agent Substrate is K8s-native) and `kubectl
       apply` (or just emit + print for `dry_run`). **[VERIFY]** Substrate CRDs /
       expected labels.
   - Reuse `secrets` injection exactly like other deployers (provider keys via
     `collect_required_secrets`).

3. **Dispatch** — `cli/deploy_cmd.py`: add `elif resolved_platform in
   ("agent-executor", "ax")` branch constructing `AgentExecutorDeployer`. Keep the
   `isinstance(platform_config, AgentExecutorDeployConfig)` guard like the others.

4. **Templates** — new `templates/deploy/ax_server.py.j2`,
   `templates/deploy/Dockerfile.ax.j2`, `templates/deploy/ax.yaml.j2`, and (substrate)
   `templates/deploy/k8s/{deployment,service}.yaml.j2`.

## Scope cuts for v1 (honest)

- No token streaming (v1 = one `AgentOutputs` + `AgentEnd`).
- No nested/sub-agent `Connect` calls back through the controller (ax supports
  agents initiating nested agentic calls — out of scope until the base contract works).
- No `ax`-side durable-execution wiring beyond what the controller gives for free;
  ABP doesn't try to reimplement snapshots.
- Substrate mode may ship after container mode — container mode alone is a complete,
  testable target (run the gRPC server, register in `ax.yaml`, `ax exec`).

## Tests (match repo conventions)

- Generator: `ax_server.py` is emitted only for this target; default output unchanged
  (golden byte-identical).
- Deployer: `_record_cmds` fake-`_cmd` pattern (as in `tests/test_cli/test_deploy_cmd.py`)
  — assert the `docker build` / `kubectl apply` / file-render argv for both modes +
  `dry_run`. No real `ax`/docker needed.
- Doctor: add a target-compatibility note if any blueprint feature can't map onto the
  `ax` message contract.

## Open questions (resolve before implementing)

1. **[VERIFY]** `Connect` direction (listen vs dial-out) — see above; gates the server shape.
2. Is there a reusable `ax` Python servicer base (pip), or do we vendor `ax_pb2`?
3. How does `ax` pass per-turn config (`AgentStart.config` bytes) — do we need to read
   anything from it, or is the initial `Message` list sufficient for ABP agents?
4. `conversation_id` ↔ LangGraph `thread_id` mapping: 1:1 safe? (checkpointer is
   in-memory by default — durable runtime wants a real checkpoint backend; tie to the
   existing memory/checkpoint config.)
5. Versioning/stability: `ax` is **preview** (`github.com/google/ax`). Pin the proto
   commit; treat the target as experimental until `ax` stabilizes.

## Related

- ABP positioning vs Google's stack: memory `google-agent-runtime-positioning`.
- GCP Cloud Run deployer (`deployers/gcp.py`) is the closest existing analogue and is
  itself only "partial" — evolving it toward Substrate/`ax` is the natural path.
- GrowOps demo currently deploys to Azure; an `ax`/Substrate variant is a candidate
  acceptance demo for this target.
