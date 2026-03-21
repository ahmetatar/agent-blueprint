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
| `--platform` | from blueprint | `azure` \| `aws` \| `gcp` |
| `--image-tag` | `latest` | Docker image tag |
| `--dry-run` | `false` | Print all commands without executing |
| `--env KEY=VAL` | — | Extra env vars to inject as secrets (repeatable) |

## Deploy Flow

1. Validates and compiles the blueprint
2. Generates LangGraph code to a temp dir
3. Adds `Dockerfile`, `server.py` (FastAPI `/invoke` + `/health`), `requirements_deploy.txt`
4. Checks platform CLI prerequisites and authentication
5. Collects secrets from environment (`api_key_env`, tool auth env vars)
6. Builds Docker image → pushes to cloud registry → creates/updates cloud service
7. Prints the deployed endpoint URL

## HTTP API

```bash
# Single invocation
curl -X POST https://<endpoint>/invoke \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello", "thread_id": "default"}'

# Health check
curl https://<endpoint>/health
```

## Platform-Specific Resources

| Platform | Registry | Service |
|---|---|---|
| Azure | Azure Container Registry (ACR) | Container Apps |
| AWS | Elastic Container Registry (ECR) | App Runner |
| GCP | Artifact Registry | Cloud Run |

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
```

## Secret Injection

Secrets are collected automatically from the blueprint (`model_providers[*].api_key_env`, `tools[*].auth.*_env`) and read from your local environment at deploy time. Missing secrets produce a warning but do not block deployment.
