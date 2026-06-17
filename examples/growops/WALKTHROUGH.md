# GrowOps — run this example end to end

**GrowOps** is the flagship `agent-blueprint` example: an autonomous vertical-farm
operations brain. One sensor reading per grow bed flows through a multi-agent
graph that assesses the bed, and — only when something is wrong — proposes
remediation behind a human approval gate. It is one blueprint that exercises
essentially every ABP feature, and it runs **live on Azure Container Apps**
against a real Azure OpenAI model.

This document takes you from zero to a deployed, working agent — and back down to
~$0 when you are done.

---

## What this example exercises

- **Multi-agent graph**: a `parallel` fan-out of three analysts → a `synthesize`
  join → a `supervisor` (`grow_ops_lead`) that delegates to actuator workers →
  two nested `subgraph` safety gates → a report writer.
- **Tools**: an `api` tool (`read_sensors`), `function` tools (`parse_telemetry`,
  `compute_vpd`), a `retrieval` tool (RAG over an agronomy corpus), and a
  human-gated `api` actuator (`set_actuator`).
- **Runtime guarantees**: state invariants, node `output_contract`s (enum status),
  an `approval` policy on the actuator, a `max_graph_steps` budget, escalation to
  a Slack `handoff` on low confidence, and conditional routing (a healthy bed
  skips remediation entirely).
- **Ops**: a deterministic offline harness (`abp test`), `sqlite` per-bed memory,
  OTel tracing, and a one-command Azure deploy.

## What's in this folder

| File | What it is |
|------|------------|
| `growops.yml` | The blueprint — the whole agent, declaratively. |
| `farm_impl.py` | Python implementations for the function tools + the RAG retriever. |
| `companion/mock_sensors.py` + `Dockerfile` | A tiny mock sensor gateway (stands in for the ESP32 fleet) so `read_sensors` has a live endpoint. |
| `companion/start.sh` / `stop.sh` | Bring the Azure demo up / tear it down to ~$0. |
| `WALKTHROUGH.md` | This guide. |

---

## How it works (30-second tour)

```
ingest (read+parse sensors)
   → sense  [irrigation | climate | pest]   (parallel analysts)
   → synthesize  → status ∈ {nominal, attention, critical}
        │
        ├─ nominal   → report            (skip remediation, write a markdown report)
        └─ otherwise → plan (supervisor)  → actuators  → safety gates → report
                            │
                            └─ set_actuator is APPROVAL-GATED (human in the loop)

   low confidence anywhere → handoff to a human (Slack), END
```

The point of the demo is that the **runtime guarantees fire in production**: a
healthy bed returns a clean `nominal` result with no side effects, while a bed
that needs action cannot actuate without a human — the approval gate blocks it.

---

## Prerequisites

- Python 3.11+ with `agent-blueprint` installed (`pip install -e ".[dev]"` from
  the repo root, or `pip install agent-blueprint`).
- For the **offline** path: nothing else.
- For the **Azure** path: the [Azure CLI](https://aka.ms/installazurecli) (`az`),
  an Azure subscription, and `az login`.

---

## Path A — run it offline (no cloud)

The deterministic harness runs the whole graph with a mock LLM and stubbed tools.
No API keys, no Azure.

```bash
abp validate examples/growops/growops.yml      # parse + cross-reference checks
abp lint     examples/growops/growops.yml      # 9 static checks
abp test     examples/growops/growops.yml --install   # runs both scenarios end to end
```

`--install` pip-installs the generated project's runtime deps (langgraph,
langchain-openai, …) the first time. You should see:

```
nominal_cycle             PASS   route, artifacts
low_confidence_escalation PASS   route
2 passed, 0 failed
```

You can also generate the runnable project to inspect it:

```bash
abp generate examples/growops/growops.yml -o /tmp/growops && ls /tmp/growops
```

---

## Path B — deploy to Azure

`abp deploy` builds the image and ships the app, but it **assumes the cloud
substrate already exists** (resource group, registry, Container Apps environment,
the model). So the flow is: provision once, then `start.sh` / `stop.sh` for each
demo.

### B0. One-time setup (do this once)

Pick your own globally-unique names for the registry and the Azure OpenAI account,
then **edit them in three places** so they match: `growops.yml` (`deploy.azure`)
and `companion/start.sh` + `companion/stop.sh` (the config vars at the top).

```bash
az login

# Resource group + Container Apps environment (kept between demos — free at idle)
az group create -n growops-rg -l westeurope
az containerapp env create -n growops-env -g growops-rg -l westeurope

# Azure OpenAI account + a gpt-4o deployment (use a region with gpt-4o quota)
az cognitiveservices account create -n <your-aoai-name> -g growops-rg \
  -l swedencentral --kind OpenAI --sku S0 --custom-domain <your-aoai-name> --yes
az cognitiveservices account deployment create -n <your-aoai-name> -g growops-rg \
  --deployment-name gpt-4o --model-name gpt-4o --model-version 2024-11-20 \
  --model-format OpenAI --sku-name Standard --sku-capacity 10
```

> First time on a subscription you may need to enable two providers — `az` will
> prompt, or run them yourself:
> `az provider register -n Microsoft.App` and
> `az provider register -n Microsoft.CognitiveServices`.

The **registry** and the **container apps** are *not* created here — `start.sh`
creates them (and `stop.sh` deletes them), because they are the only resources
that cost money while idle.

### B1. Bring it up

```bash
./examples/growops/companion/start.sh
```

This recreates the registry, builds + deploys the mock sensor gateway, then runs
`abp deploy` for the agent — wiring the model endpoint, secrets, and the sensor
URL. It prints the app URL at the end (and the deploy block in `growops.yml`
tells `abp deploy` which resource group / registry / environment to use).

### B2. Hit it

```bash
APP=https://<app-fqdn>           # printed by start.sh

curl $APP/health                 # → {"status":"ok","agent":"growops"}

# A healthy bed → clean nominal result, no side effects:
curl -X POST $APP/invoke -H 'Content-Type: application/json' -d '{
  "input": {"bed_id":"bed-B2","soil_moisture":55,"air_temp_c":22,"humidity":60,"co2_ppm":800},
  "thread_id": "bed-B2"
}'
# → {"response":{"status":"nominal","risk_level":"low","confidence":0.95,
#                "recommended_actions":[],"approved_plan":null}}
```

A bed that needs remediation routes into the supervisor and tries to actuate —
which the **approval gate blocks** in headless mode:

```
{"error":"Approval required for tool 'set_actuator'. Set ABP_TOOL_APPROVAL_MODE=allow
 or explicitly approve the tool via ABP_APPROVED_TOOLS ..."}
```

That is the demo's point: the agent never actuates hardware without a human. To
let it through (e.g. an autonomous mode), set `ABP_TOOL_APPROVAL_MODE=allow` or
`ABP_APPROVED_TOOLS=set_actuator` on the Container App.

### B3. Take it down

```bash
./examples/growops/companion/stop.sh
```

Deletes the container apps + the registry (the only resources that bill at idle)
and keeps the Azure OpenAI account + Container Apps environment (both free at
idle). Idle cost after this: **~$0**. Bring it back any time with `start.sh`
(~5 min).

---

## Cost

| Resource | Idle cost |
|----------|-----------|
| Azure OpenAI (S0) | $0 — pure pay-per-token |
| Container Apps environment | $0 (Consumption) |
| Registry (ACR Basic) | ~$5/mo **while it exists** — `stop.sh` deletes it |
| Agent + sensor apps | $0 once `stop.sh` has run |

So the whole setup is effectively start/stop: `start.sh` to demo, `stop.sh` to
return to ~$0. For a full wipe (true $0, but a slower restart), delete the
resource group: `az group delete -n growops-rg --yes`.

---

## Make it your own

- **Names**: change `acr_name` / `app_name` / `resource_group` / `container_app_env`
  in `growops.yml`'s `deploy.azure`, and the matching vars at the top of
  `companion/start.sh` + `stop.sh`. ACR and Azure OpenAI names are globally unique.
- **Real hardware**: replace the mock sensor gateway with your ESP32 fleet's
  endpoint (set `SENSOR_GATEWAY_URL`), and point `ACTUATOR_GATEWAY_URL` at a real
  actuator gateway. The actuator stays human-gated unless you opt into approval.
- **Scheduling**: drive it autonomously with a Container Apps cron Job that does
  `curl … /invoke` per bed on a schedule.
