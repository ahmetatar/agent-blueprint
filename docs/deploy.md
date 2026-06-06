# Deployment

Deploy to a cloud platform with `abp deploy`. Requires Docker and the relevant CLI (`az` / `aws` / `gcloud`) to be installed and authenticated.

## Usage

```bash
abp deploy my-agent.yml --platform azure
abp deploy my-agent.yml --platform gcp --image-tag v1.2
abp deploy my-agent.yml --platform aws --dry-run
abp deploy my-agent.yml --env EXTRA_KEY=value
```

| Flag | Default | Description |
|---|---|---|
| `--platform` | from blueprint | `azure` \| `aws` \| `gcp` \| `docker` \| `podman` |
| `--image-tag` | `latest` | Docker image tag |
| `--dry-run` | `false` | Print all commands without executing |
| `--env KEY=VAL` | — | Extra env vars to inject as secrets (repeatable) |

`docker` and `podman` run the same container image locally instead of pushing to a cloud — useful for a production-like smoke test. They need no `deploy.*` config section (defaults apply).

## Deploy Flow

1. Validates and compiles the blueprint
2. Generates LangGraph code to a temp dir
3. Adds `Dockerfile`, `server.py` (FastAPI `/invoke` + `/health`), `requirements_deploy.txt`
4. Checks platform CLI prerequisites and authentication
5. Collects secrets from environment (`api_key_env`, tool auth env vars)
6. Builds Docker image → pushes to cloud registry → creates/updates cloud service
7. Prints the deployed endpoint URL

## How the Agent Is Exposed

You do **not** implement a trigger yourself. The packaging step wraps the
generated agent in a small FastAPI server (`server.py`) that exposes two
endpoints and listens on port **8080** (override with the `PORT` env var):

| Endpoint | Purpose |
|---|---|
| `POST /invoke` | Run the agent once. Internally calls the generated `main.run(input, thread_id=...)` |
| `GET /health` | Liveness probe — used by the platforms' health checks |

### Request / response contract

```bash
# Single invocation
curl -X POST https://<endpoint>/invoke \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello", "thread_id": "default"}'
# → {"response": "..."}        (200)
# → {"error": "..."}           (500 on any agent failure)

# Health check
curl https://<endpoint>/health
# → {"status": "ok", "agent": "<blueprint name>"}
```

`thread_id` selects the LangGraph checkpointer thread: requests sharing a
`thread_id` continue the same conversation; a new id starts a fresh one.

### What you should know before production

- **Conversation persistence**: with `memory.backend: in_memory` the
  checkpointer lives inside the process — conversation history does not
  survive restarts, cold starts, or multiple replicas. For real conversation
  continuity behind a scaling service, declare a shared backend
  (`postgres` or `redis`) in the blueprint's [`memory`](memory.md) section.
- **Authentication is the platform's job**: the `/invoke` endpoint itself is
  unauthenticated. On GCP, `allow_unauthenticated: false` (the default)
  keeps IAM in front of it; on Azure Container Apps and AWS App Runner,
  restrict ingress/attach auth at the platform level.
- **Non-HTTP triggers** (cron, queues, pub/sub) are not generated. Either
  point the platform's scheduler/consumer at `POST /invoke`, or import the
  generated `main.run()` directly in your own worker — the generated project
  is a plain Python package, so both work.

## Platform-Specific Resources

| Platform | Registry | Service | Exposed as |
|---|---|---|---|
| Azure | Azure Container Registry (ACR) | Container Apps | ingress FQDN (`https://<app>.<env>.azurecontainerapps.io`), `min/max_replicas` from blueprint |
| AWS | Elastic Container Registry (ECR) | App Runner | service URL (`https://<id>.<region>.awsapprunner.com`) |
| GCP | Artifact Registry | Cloud Run | Cloud Run URL; IAM-protected unless `allow_unauthenticated: true` |
| Docker / Podman | — (local image) | local container | `http://localhost:<host_port>` (default 8080) |

## Blueprint Configuration

```yaml
deploy:
  platform: azure             # default platform for abp deploy (overridable with --platform)

  azure:
    subscription_env: AZURE_SUBSCRIPTION_ID
    resource_group: "my-rg"
    location: "westeurope"
    acr_name: "myregistry"
    container_app_env: "my-env"
    min_replicas: 0
    max_replicas: 3

  aws:
    region: "eu-west-1"
    ecr_repo: "my-agent"
    service_name: "my-agent-service"   # optional, defaults to blueprint name

  gcp:
    project_env: GCP_PROJECT_ID
    region: "europe-west1"
    artifact_repo: "cloud-run-source-deploy"
    allow_unauthenticated: false

  docker:                       # podman uses the same shape under a `podman:` key
    host_port: 8080
    container_name: "my-agent"  # optional, defaults to blueprint name
    network: null               # e.g. "host" on Linux
    platform: null              # e.g. "linux/amd64" for cross-builds
```

## Secret Injection

Secrets are collected automatically from the blueprint (`model_providers[*].api_key_env`, `tools[*].auth.*_env`) and read from your local environment at deploy time. Missing secrets produce a warning but do not block deployment.
