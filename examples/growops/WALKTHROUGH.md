# GrowOps — Build-It-Together Walkthrough

A hands-on lab that builds the **GrowOps** demo (see
`../multi-agent-scenario-ideas.md`) one feature at a time. We do this
**together**: each milestone is small, you run the commands, and we keep
`abp validate` green before moving on. No milestone leaves the blueprint broken.

**The golden rule of this lab:** after every milestone run
```bash
abp validate examples/growops/growops.yml && abp lint examples/growops/growops.yml
```
If it's green, we advance. If it's red, we fix it before adding anything new.

**Two things to know up front (the honest boundaries):**
- `type: function` is **not** a user-authored node (no `impl` field — it's an
  internal subgraph adapter). Anything "do X in code" is a **function tool**
  (`tools[].type: function`, `impl: dotted.path`) called by an agent.
- ABP doesn't emit cron/queues. "Autonomous & scheduled" = an external Azure
  **Container Apps Job (cron)** calling `POST /invoke`. We build the agent; the
  scheduler is wiring we add at the very end.

Legend per milestone: **🎯 Goal** · **⌨️ CLI** · **📝 YAML to add** · **✅ Verify** · **✋ Checkpoint**

---

## M0 — Workspace & the schema map

**🎯 Goal:** working `abp`, an empty example dir, and a feel for the schema.

**⌨️ CLI:**
```bash
pip install -e ".[dev]"          # we're in the repo; installs abp editable
abp --help                       # see: validate, lint, generate, run, test, eval, gate, deploy, package...
abp schema | less                # the full blueprint JSON schema — your reference
mkdir -p examples/growops/{tools,corpus,datasets,companion}
```

**✋ Checkpoint:** `abp --help` prints the command list. We're standing in the repo root for every command below.

---

## M1 — The smallest valid blueprint (one agent)

**🎯 Goal:** a single-agent graph that validates and inspects. Features:
`blueprint`, `settings`, `state`, one `agent`, `graph`, `memory`.

**⌨️ CLI:** (`--template blueprint` is the default — there is no `basic`; the
only choices are `blueprint` | `spec`)
```bash
abp init --template blueprint --output examples/growops/growops.yml
abp validate examples/growops/growops.yml
abp inspect examples/growops/growops.yml        # prints a Mermaid graph
```

**📝 YAML:** edit the generated file to our domain (rename agent → `grower`,
set the blueprint name to `growops`, give it a grow-room system prompt). Keep it
one node for now.

**✅ Verify:** `abp validate` → OK. `abp inspect` shows `grower → END`.

**✋ Checkpoint:** green validate + a one-node Mermaid diagram. **← we start here together.**

---

## M2 — Typed state for sensor telemetry

**🎯 Goal:** model the grow-room state. Features: `state.fields` with `type`,
`reducer`, `enum`, `nullable`, `default`.

**📝 YAML — add under `state.fields`:**
```yaml
state:
  fields:
    messages: { type: "list[message]", reducer: append }
    bed_id:        { type: string, description: "which grow bed (= thread id)" }
    soil_moisture: { type: number, description: "0-100 %" }
    air_temp_c:    { type: number }
    humidity:      { type: number }
    co2_ppm:       { type: number }
    findings:      { type: list, reducer: append, default: [] }
    status:        { type: string, enum: [nominal, attention, critical], nullable: true }
```

**✅ Verify:** `abp validate` → OK.

**✋ Checkpoint:** state carries real telemetry channels with the right reducers
(`findings` appends; scalars replace).

---

## M3 — Function & API tools (ESP32 sensing)

**🎯 Goal:** the agent reads hardware. Features: `tools[].type: function` (`impl`)
and `tools[].type: api` with `api_key` auth + `${env.*}` interpolation.

**📝 YAML — add a `tools:` section and a `tools/impl.py`:**
```yaml
tools:
  parse_telemetry:
    type: function
    impl: "tools.impl.parse_telemetry"
    description: "Parse a raw ESP32 telemetry payload into typed fields"
  compute_vpd:
    type: function
    impl: "tools.impl.compute_vpd"
    description: "Vapor-pressure-deficit from temp + humidity"
  read_sensors:
    type: api
    method: GET
    url: "${env.SENSOR_GATEWAY_URL}/beds/{bed_id}/sensors"
    auth: { type: api_key, key_env: SENSOR_API_KEY, header: "X-API-Key" }
    description: "Pull the latest reading for a bed from the Sensor Gateway"
```
Then wire `tools: [parse_telemetry, compute_vpd, read_sensors]` onto the `grower`
agent and stub the impls in `examples/growops/tools/impl.py`.

**⌨️ CLI:** `abp doctor examples/growops/growops.yml` (checks tool/env wiring).

**✅ Verify:** `abp validate` + `abp doctor` → OK.

**✋ Checkpoint:** the agent has hands (function tools) and eyes (sensor API).

---

## M4 — Parallel sensing (fan-out / join)

**🎯 Goal:** three specialist analysts run concurrently. Features: `parallel`
node (`branches`, `join`), multiple agents, `contracts.nodes` (`produces`).

**📝 YAML:** add agents `irrigation_analyst`, `climate_analyst`, `pest_analyst`
(each appends to `findings`), then restructure the graph:
```yaml
graph:
  entry_point: ingest
  nodes:
    ingest: { agent: grower, description: "read + parse telemetry" }
    sense:
      type: parallel
      branches: [irrigation_analyst, climate_analyst, pest_analyst]
      join: synthesize
    irrigation_analyst: { agent: irrigation_analyst }
    climate_analyst:     { agent: climate_analyst }
    pest_analyst:        { agent: pest_analyst }
    synthesize: { agent: synthesizer }
  edges:
    - { from: ingest, to: sense }
    - { from: synthesize, to: END }
```
Note: parallel **branch** nodes must NOT declare their own outgoing edges (the
validator enforces this — a good error to see on purpose once).

**✅ Verify:** `abp validate` + `abp lint` (watch for `parallel-branch-conflict`).
`abp inspect` shows the fan-out.

**✋ Checkpoint:** a real barrier — three analysts → one `synthesize`.

---

## M5 — Output contract + state invariants

**🎯 Goal:** `synthesize` must emit a structured, checkable verdict. Features:
`contracts.outputs`, `contracts.nodes[].output_contract`, `contracts.state`
(`immutable_fields`, `invariants`).

**📝 YAML:**
```yaml
contracts:
  state:
    immutable_fields: [bed_id]
    invariants:
      - "0 <= soil_moisture <= 100"
  nodes:
    synthesize: { produces: [status], output_contract: GrowAssessment }
  outputs:
    GrowAssessment:
      type: object
      required: [status, risk_level, confidence]
      properties:
        status:     { type: string }
        risk_level: { type: string }
        confidence: { type: number }
```

**✅ Verify:** `abp validate` → OK; `abp lint` (contract-usage check).

**✋ Checkpoint:** the brain's output is now a contract, not vibes.

---

## M6 — Escalation + handoff (the human in the loop)

**🎯 Goal:** when confidence is low, ask a human. Features: conditional edges,
`policies.escalation.on_low_confidence`, `handoff` node (`channel: slack`).

**📝 YAML:**
`policies.escalation` is a **dynamic reroute**: the generator injects the
escalation target into every node's router, so no explicit `confidence < 0.7`
edge is needed. `synthesize → END` stays plain; `confidence`/`status`/
`risk_level` are consumed by the top-level `output` schema instead.

```yaml
  nodes:
    # ...
    ask_agronomist:
      type: handoff
      channel: slack
      action: ask_agronomist
      message_template: "Bed {bed_id}: status {status}, confidence {confidence} — needs a human call."
  edges:
    - { from: ingest, to: sense }
    - { from: synthesize, to: END }
    - { from: ask_agronomist, to: END }

policies:
  escalation: { on_low_confidence: ask_agronomist, confidence_threshold: 0.7 }

output:
  schema:
    status:     { type: string, enum: [nominal, attention, critical] }
    risk_level: { type: string, enum: [low, medium, high] }
    confidence: { type: number }
```

> **Lint fix landed here.** The `unreachable-node` lint used to flag a node
> reachable *only* via `policies.escalation` (static reachability ignored the
> policy target). Fixed in `linting.py` (+ `tests/test_linting_unreachable.py`):
> when the policy is set, its target is treated as reachable from every node
> with an outgoing edge — mirroring `templates/langgraph/graph.py.j2`. Tradeoff:
> the escalation route is dynamic, so it does **not** appear in `abp inspect`.

**✅ Verify:** `abp validate`; `abp lint` → 0 errors, 0 warnings.

**✋ Checkpoint:** low confidence routes to a Slack handoff via the policy alone
(no explicit edge), and the lint is clean.

---

## M7 — Supervisor + actuators (dynamic delegation)

**🎯 Goal:** a lead dynamically delegates remediation. Features: `supervisor`
node (`workers`, `max_iterations`, `on_finish`), actuator `api` tool with
`bearer` auth, multiple agents as workers.

**📝 YAML:**
```yaml
tools:
  set_actuator:
    type: api
    method: POST
    url: "${env.ACTUATOR_GATEWAY_URL}/beds/{bed_id}/actuate"
    auth: { type: bearer, token_env: ACTUATOR_TOKEN }
  # ...
  nodes:
    plan:
      type: supervisor
      agent: grow_ops_lead
      workers: [irrigation_actuator, climate_actuator, nutrient_dosing]
      max_iterations: 6
      on_finish: safety_gate          # 'safety_gate' arrives in M8; use 'report' or END for now
    irrigation_actuator: { agent: irrigation_actuator }
    climate_actuator:    { agent: climate_actuator }
    nutrient_dosing:     { agent: nutrient_dosing }
```
Worker nodes must be `agent` nodes and must NOT have explicit outgoing edges
(they auto-return to the supervisor — the validator enforces it).

**✅ Verify:** `abp validate`; `abp inspect` shows the supervisor + workers.

**✋ Checkpoint:** the lead delegates only the specialists the situation needs.

---

## M8 — Subgraph reuse (the safety review)

**🎯 Goal:** a reusable propose→critique→finalize safety flow. Features:
`subgraphs`, `subgraph` node (`ref`, `input_map`, `output_map`), reuse.

**📝 YAML:**
```yaml
subgraphs:
  safety_flow:
    entry_point: propose
    nodes:
      propose:  { agent: safety_proposer }
      critique: { agent: safety_critic }
      finalize: { agent: safety_finalizer }
    edges:
      - { from: propose, to: critique }
      - { from: critique, to: finalize }
      - { from: finalize, to: END }
graph:
  nodes:
    safety_gate:
      type: subgraph
      ref: safety_flow
      input_map:  { recommended_actions: candidate_plan }
      output_map: { approved_plan: recommended_actions, risk_level: risk_level }
```
`input_map`/`output_map` are **required** on subgraph nodes. Invoke the same
`ref` a second time (e.g. `nutrient_safety_gate`) with a different `input_map`
to show reuse.

**✅ Verify:** `abp validate`; `abp lint` (unbounded-loop / cycle checks).

**✋ Checkpoint:** one safety flow, reused for two plans.

---

## M9 — Approvals, tool caps, retry (guardrails)

**🎯 Goal:** dangerous actuators need a human yes; tool loops can't run up a
bill; flaky hardware retries. Features: `policies.approvals`,
`policies.tool_usage`, node `retry`.

**📝 YAML:**
```yaml
policies:
  approvals:
    mode: selective
    tools: [set_actuator]        # or a dedicated set_grow_light / dose_nutrients tool
    on_violation: block
  tool_usage:
    max_calls_per_node: 4
    max_calls_per_run: 12
    require_explicit_arguments: true
    on_unknown_tool: fail
  # escalation + budgets too
  budgets: { max_tokens_per_run: 40000, max_cost_usd: 0.50, max_latency_seconds: 60 }

graph:
  nodes:
    commit:
      agent: committer
      retry: { max_attempts: 3, backoff_seconds: 1.5 }
```

**✅ Verify:** `abp validate`; `abp lint`.

**✋ Checkpoint:** the autonomous loop now has a budget and a circuit-breaker.

---

## M10 — RAG (pest knowledge), reasoning, multi-provider

**🎯 Goal:** `pest_analyst` consults an agronomy corpus; the planner *thinks*;
models come from the right provider. Features: `retrievers`,
`tools[].type: retrieval`, `agents[].rag`, `agents[].reasoning`,
`model_providers` (multi).

**📝 YAML:**
```yaml
model_providers:
  azure: { provider: azure_openai, base_url: "${env.AZURE_OPENAI_ENDPOINT}", deployment: "gpt-4o", api_version: "2024-06-01" }
  thinker: { provider: anthropic, api_key_env: ANTHROPIC_API_KEY }
retrievers:
  agronomy: { impl: "tools.impl.agronomy_retriever", config: { corpus: "corpus/" } }
tools:
  search_agronomy: { type: retrieval, retriever: agronomy, top_k: 4 }
agents:
  pest_analyst:
    tools: [search_agronomy]
    rag: { tool: search_agronomy, mode: hybrid }
  grow_ops_lead:
    model_provider: thinker
    reasoning: { enabled: true, params: { thinking_budget: 2000 } }
```

**✅ Verify:** `abp validate`; `abp doctor` (provider env keys).

**✋ Checkpoint:** RAG + extended thinking + the model living on Azure.

---

## M11 — Artifacts (the cycle report)

**🎯 Goal:** every cycle emits a markdown report. Features: `artifacts` bound to
an output contract.

**📝 YAML:**
```yaml
  nodes:
    report: { agent: report_writer }
artifacts:
  grow_report:
    format: markdown
    producer: report
    path: "reports/{bed_id}-cycle.md"
    contract: GrowReport            # add GrowReport to contracts.outputs
```

**✅ Verify:** `abp validate`.

**✋ Checkpoint:** a per-bed report artifact, contract-bound.

---

## M12 — Offline tests: harness scenarios

**🎯 Goal:** the whole grow room runs with **no API key and no hardware**.
Features: `harness` (`llm_mode: mock`, `tool_mode: stub`), route/artifact assertions.

> **This was the milestone that stress-tested ABP the hardest.** Getting `abp test`
> green end-to-end surfaced three real product bugs (fixed in the same session) and
> several non-obvious runtime realities. They're baked into `growops.yml` now — but
> here's *why* each piece is shaped the way it is, because every one of them is a
> trap you'd otherwise hit.

**Prerequisite — install the generated runtime deps** (`abp test` runs the real
LangGraph project as a subprocess; default is `--no-install`):
```bash
pip install langgraph langchain-anthropic   # langchain-openai usually already present
```

**The five realities baked into the blueprint:**

1. **`"*"` default fixture.** Mock mode needs a fixture for *every* compiled LLM
   node — including namespaced subgraph internals (`safety_gate__propose`…) you
   never named. A single `llm_outputs."*"` entry is the default for all of them;
   you only spell out the nodes whose output an assertion depends on
   (`synthesize`, `report`, the analysts).

2. **An agent only writes a state field via an `output_contract`.** A plain agent
   emits messages, not named state. So the three analysts carry
   `output_contract: AnalystFinding` to actually `produce: [findings]`, and their
   mock content must be **valid JSON** (the contract parses it with `json.loads`).

3. **Budgets need metadata.** `max_tokens_per_run` requires `usage` on every mock
   response → each fixture entry has `usage: {input_tokens, output_tokens,
   total_tokens}`. `max_cost_usd` needs provider pricing, so it's omitted here.

4. **Seed the cycle's input.** Declare an `input:` schema (the sensor telemetry +
   `bed_id`); the scenario `input` is validated against it and seeded into state,
   which satisfies the analysts' `requires:` and gives `bed_id` for the artifact
   path. Output fields that can be `None` at run end (`approved_plan`) need
   `nullable: true`.

5. **Impl module name.** The function-tool impl file is **`farm_impl.py`**, *not*
   `tools/` — a module named `tools` collides with the generated `tools.py`.
   `abp test`/`run`/`gate`/`eval` add the blueprint's own directory to
   `PYTHONPATH`, so `farm_impl` (next to the blueprint) resolves from any cwd.

**📝 YAML:** see the committed `harness:` block in `growops.yml` — `defaults` with
the `"*"` default + per-node JSON for the analysts/synthesize/report, `tool_outputs`
with a `"*"` default, and two scenarios (`nominal_cycle`, `low_confidence_escalation`).

**⌨️ CLI:**
```bash
abp test examples/growops/growops.yml     # offline, deterministic, no API key
```

**✅ Verify:** both scenarios PASS — `nominal_cycle` checks `route: report` +
`artifacts: [grow_report]`; `low_confidence_escalation` checks `route: ask_agronomist`.

**✋ Checkpoint:** the example is now a living test, not just YAML.

---

## M13 — Evals, gate, traces (behavior under CI control)

**🎯 Goal:** grow behavior as a gateable artifact. Features: `evals`
(`exact_match`, `policy_violations`, `rubric`) + `datasets/`, `abp gate` baseline,
traces flywheel.

Three eval suites + their `datasets/*.jsonl` are committed in `growops.yml`:
- **`status_routing`** (`exact_match`) — two cases assert `route`: nominal →
  `report`, low-confidence → `ask_agronomist` (the low-conf case overrides the
  `synthesize` fixture to emit `confidence: 0.4`).
- **`grow_policy`** (`policy_violations`) — a compliant cycle emits zero
  `policy_violation` trace events.
- **`report_quality`** (`rubric`) — `suite.metadata.rubric` checks the
  `grow_report` artifact for required sections (`Status`, `Summary`, `Actions`)
  and a min word count.

> **Realities discovered here:**
> - A dataset case is just a harness scenario (`id` + `input` + `expected` +
>   optional `fixtures`); `exact_match` passes when the scenario's `expected`
>   holds, `policy_violations` when the trace has zero violation events.
> - The rubric reads the artifact **from the trace**. The harness runs in a temp
>   dir, so artifacts must be written to an absolute path or the eval (run from
>   another cwd) can't find them — fixed so `ABP_ARTIFACT_DIR` defaults to the
>   absolute tempdir. (ABP does **not** template the artifact path, so `{bed_id}`
>   stays literal in the filename — harmless; the rubric reads the file content.)

**⌨️ CLI:**
```bash
abp eval examples/growops/growops.yml                       # 3 suites, all pass
abp gate examples/growops/growops.yml --update-baseline     # write .abp/gate-baseline.json
abp gate examples/growops/growops.yml                       # PASSED — 5 checks, 0 regressions
abp test examples/growops/growops.yml --save-traces all     # persist trace records
abp traces list --dir examples/growops/.abp/traces          # inspect them
abp traces export --dir examples/growops/.abp/traces -o examples/growops/datasets/from_traces.jsonl --golden --status passed
```

> **Note:** `.abp/` is git-ignored, so shipping the committed gate baseline needs
> `git add -f examples/growops/.abp/gate-baseline.json` (handled in the example PR).

**✋ Checkpoint:** `abp gate` is green; loosen a grow rule and it goes red.

---

## M14 — Observability + sandbox

**🎯 Goal:** spans + isolation. Features: `observability.tracing`, `run.sandbox`.

**📝 YAML:**
```yaml
observability:
  tracing: { enabled: true, exporter: console }   # otlp → Azure Monitor in prod
run:
  sandbox: { enabled: false, engine: auto, image: "python:3.11-slim" }
```

**⌨️ CLI:** `abp run examples/growops/growops.yml --sandbox "bed-A1"` (needs a container engine).

**✋ Checkpoint:** trace spans on the console; sandbox declared.

---

## M15 — Package + memory backend (the local rig)

**🎯 Goal:** a real CLI for a single-bed rig that remembers last week. Features:
`abp package`, `memory.backend: sqlite`.

`growops.yml` now uses `memory.backend: sqlite` (`connection_string_env:
GROWOPS_DB_PATH`, default `growops.db`) — each `--thread-id` (a bed id) is that
bed's persistent journal across invocations. `abp test` stays green (the sqlite
checkpointer writes into the temp dir per run).

> **Reality discovered here:** `abp package` rewrites the generated modules into a
> src-layout package but originally left **user `impl` modules** out — so the
> packaged `tools.py` kept `from farm_impl import …` to a module that wasn't in
> the package, and the installed CLI raised `ModuleNotFoundError`. Fixed: `abp
> package` now copies sibling `impl` modules (e.g. `farm_impl.py`) into the
> package and rewrites their imports to relative. Impl roots that are installed
> packages (not sibling files) keep their absolute import.

**⌨️ CLI:**
```bash
abp package examples/growops/growops.yml          # → ./growops-cli (farm_impl included)
pipx install ./growops-cli
growops "bed-A1: morning check"                    # needs provider API keys for a real run
```

**✋ Checkpoint:** the packaged CLI imports cleanly (`farm_impl` resolves);
`--thread-id bed-A1` is the bed's persistent sqlite journal.

---

## M16 — Deploy to Azure + the autonomous scheduler

**🎯 Goal:** the demo goes live and runs itself. Features: `deploy.azure`,
`memory.backend: postgres`, the external cron trigger.

**📝 YAML:**
```yaml
deploy:
  platform: azure
  azure:
    resource_group: "growops-rg"
    location: "westeurope"
    acr_name: "growopsacr"
    container_app_env: "growops-env"
    min_replicas: 0        # sleeps between cycles
    max_replicas: 3
memory:
  backend: postgres        # shared, survives cold starts / replicas
  connection_string_env: DATABASE_URL
```

**⌨️ CLI + Azure wiring (the non-ABP part):**
```bash
abp deploy examples/growops/growops.yml --platform azure
# then, in Azure: a Container Apps JOB on a cron schedule that does:
#   curl -X POST https://<app-fqdn>/invoke -d '{"input":"...","thread_id":"bed-A1"}'
# + the companion Sensor/Actuator gateways + the ESP32 sketch (examples/growops/companion/)
```

**✋ Checkpoint:** a scheduled, hardware-in-the-loop, human-gated grow-room
brain — running on Azure. Record the demo. 🎉

### As actually deployed (2026-06-17)

The blueprint was deployed live to Azure Container Apps with a real Azure OpenAI
`gpt-4o` deployment. What it took, end to end:

**1. Provision the cloud resources `abp deploy` assumes already exist** (the
deployer builds + ships the app; it does not create the model, registry, or
environment):
```bash
az group create -n growops-rg -l westeurope
az acr create -n <globally-unique-acr> -g growops-rg --sku Basic
az containerapp env create -n growops-env -g growops-rg -l westeurope
# Azure OpenAI account + a gpt-4o deployment (region with gpt-4o quota, e.g. swedencentral):
az cognitiveservices account create -n <aoai-name> -g growops-rg -l swedencentral \
  --kind OpenAI --sku S0 --custom-domain <aoai-name> --yes
az cognitiveservices account deployment create -n <aoai-name> -g growops-rg \
  --deployment-name gpt-4o --model-name gpt-4o --model-version 2024-11-20 \
  --model-format OpenAI --sku-name Standard --sku-capacity 10
```

**2. A mock sensor gateway** (the ESP32 fleet stand-in, in `companion/`) gives
the agent a live endpoint to read telemetry from:
```bash
az acr build --registry <acr> --image mock-sensors:latest examples/growops/companion
az containerapp create -n growops-sensors -g growops-rg --environment growops-env \
  --image <acr>.azurecr.io/mock-sensors:latest --registry-server <acr>.azurecr.io \
  --ingress external --target-port 8080 --min-replicas 1 --max-replicas 1
```

**3. Deploy the agent**, with the model/secrets/gateway env wired at deploy time:
```bash
export AZURE_OPENAI_ENDPOINT="https://<aoai-name>.openai.azure.com/"
export AZURE_OPENAI_API_KEY="$(az cognitiveservices account keys list -n <aoai-name> -g growops-rg --query key1 -o tsv)"
export SENSOR_GATEWAY_URL="https://<sensors-fqdn>"
export SENSOR_API_KEY=demo-sensor-key ACTUATOR_TOKEN=demo-actuator-token
export ACTUATOR_GATEWAY_URL="https://actuators.growops.invalid"   # placeholder — actuators are human-gated
abp deploy examples/growops/growops.yml
```

**4. Hit it.** A healthy bed runs the full graph and returns clean:
```bash
curl -X POST https://<app-fqdn>/invoke -H 'Content-Type: application/json' \
  -d '{"input":{"bed_id":"bed-B2","soil_moisture":55,"air_temp_c":22,"humidity":60,"co2_ppm":800},"thread_id":"bed-B2"}'
# → {"response":{"status":"nominal","risk_level":"low","confidence":0.95,"recommended_actions":[],"approved_plan":null}}
```

**What the real deploy proved — and fixed.** Running on a real model (not the
mock harness) surfaced three ABP bugs, all fixed in the same cycle: the Azure
deployer bound env vars with a non-existent `az containerapp env vars` command;
`azure_openai` agents shipped without `langchain-openai`; and node
`output_contract` extraction couldn't parse JSON that the model wrapped in
```` ```json ```` fences. It also drove example hardening: status-based routing
(`synthesize → report` when `nominal`, else `→ plan`) so a healthy bed skips
remediation; `max_graph_steps: 60` for the deep remediation path; the report node
emits **markdown** (its natural form) instead of a brittle JSON contract; and
`default_temperature: 0` for tighter JSON adherence. The runtime guarantees all
fired in production: the output contract caught an off-enum status, the step
limit caught a runaway, and the approval gate blocked `set_actuator` headless —
exactly the human-in-the-loop behavior the design intends.

> **Note on the autonomous scheduler & `memory.backend: postgres`:** the shipped
> example uses `memory.backend: sqlite` (per-bed journal, M15). A Container Apps
> cron Job doing `curl … /invoke` per bed on a schedule is the autonomous driver;
> for multi-replica shared memory, switch to `postgres` + `DATABASE_URL`.

---

## Where we update CI

When `growops.yml` first validates (after ~M5), repoint the CI smoke step:
`.github/workflows/ci.yml:53` still names the deleted `basic-chatbot.yml`.
Replace it with `growops.yml` (validate + lint; add `abp test` after M12). We do
this in its own small commit so CI goes green again.

---

## Our cadence

One milestone per session (or a couple if they're small). You drive the CLI; I
write/paste the YAML and fix breakage with you. We never advance on a red
`abp validate`. Ready when you are — **M1** is the first move.
