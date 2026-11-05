# Deployment

> **Cloud platforms are experimental.** `docker` and `podman` are core. The
> `azure`, `aws` and `gcp` deployers are frozen (bug fixes only) and are tested
> against recorded CLI calls, not live clouds. See [scope.md](scope.md).

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
| `POST /invoke` | Run the agent once. Authenticates the caller, then calls the generated `main.run(input, thread_id=...)` |
| `GET /health` | Liveness probe — used by the platforms' health checks. Never authenticated |

### Request / response contract

```bash
# Start a conversation: no conversation_id
curl -X POST https://<endpoint>/invoke \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello"}'
# → {"response": "...", "conversation_id": "3f1c…-uuid4"}   (200)

# Continue it: send the returned id back
curl -X POST https://<endpoint>/invoke \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d '{"input": "And the second question?", "conversation_id": "3f1c…-uuid4"}'

# Health check
curl https://<endpoint>/health
# → {"status": "ok", "agent": "<blueprint name>", "auth": "jwt"}
```

`input` is passed to `main.run()` unchanged: a string, or an object when the
blueprint declares an `input` schema.

| Status | When |
|---|---|
| 200 | `{"response": ..., "conversation_id": ...}` |
| 400 | Body is not a JSON object, `conversation_id` is not a UUIDv4, or the body contains `thread_id` |
| 401 | Missing, malformed, unsigned, expired or wrongly signed token; wrong `iss`/`aud`; no user claim. Carries `WWW-Authenticate: Bearer` |
| 429 | Rate limit exceeded. Carries `Retry-After` (seconds) |
| 503 | The JWKS endpoint cannot be reached |
| 500 | The agent run failed (including input/contract errors raised inside the run) |

## Authentication and Conversation Ownership

The server never trusts the client to say which conversation to load. It
derives the LangGraph checkpointer thread from the **verified** token:

```
thread_id = f"{user_id}:{conversation_id}"
```

- `user_id` comes from a claim of a verified JWT (`sub` by default), never
  from the request body.
- `conversation_id` is a random UUIDv4 issued by the server on the first
  request. Other values (`"default"`, order numbers, non-v4 UUIDs) are
  rejected with 400.
- A client that sends another user's `conversation_id` gets a **new, empty**
  thread under its own user id: it cannot read or append to the other
  conversation.
- There is no shared default thread: a request without `conversation_id`
  always starts a new conversation.
- `thread_id` in the request body is rejected with 400, so clients written
  against the old contract fail loudly instead of silently losing their
  conversation.

### Configuring JWT verification

`deploy.auth.mode` defaults to `jwt`. `abp deploy` refuses to build until the
server has a way to verify tokens. Choose one:

```yaml
deploy:
  auth:
    # Option A — identity provider (Auth0, Entra ID, Cognito, Keycloak, …)
    jwks_url: https://shopco.eu.auth0.com/.well-known/jwks.json
    audience: shopco-support        # required with jwks_url
    issuer: https://shopco.eu.auth0.com/   # recommended
    # algorithms: [RS256]           # default with jwks_url

    # Option B — shared HMAC secret (your own backend signs the tokens)
    # secret_env: SHOPCO_JWT_SECRET # env var name; the value is injected as a secret
    # algorithms: [HS256]           # default with secret_env

    user_claim: sub                 # claim that identifies the user
  rate_limit:
    requests_per_minute: 60         # per user, per replica; 0 disables
```

| Rule | Why |
|---|---|
| `exp` is always required | A leaked token stops working |
| `iss` / `aud` are required in the token when configured | A token issued for another service is not accepted |
| `audience` is required with `jwks_url` | An identity provider issues tokens for many services |
| HMAC algorithms only with `secret_env`, asymmetric only with `jwks_url`; `none` is never allowed | Prevents algorithm-confusion attacks |
| The secret must be at least 32 bytes; the server will not start without it | RFC 7518 §3.2; fail closed |

The secret value never appears in the blueprint: `secret_env` names an
environment variable, which `abp deploy` reads locally and injects like other
secrets.

### Running without authentication

```yaml
deploy:
  auth:
    mode: none
```

Use this only when something in front of the server already authenticates
callers and you accept that a `conversation_id` is then the only thing
protecting a conversation (a random UUIDv4 acts as a bearer capability). The
server logs a warning at startup, threads are keyed `anonymous:<conversation_id>`,
and rate limiting falls back to the client address. Behind a proxy that is
the proxy's address, so the limit becomes shared.

### What the server does not do

- **TLS** is terminated by the platform (Cloud Run, App Runner, Container
  Apps). For `docker`/`podman` on a real network, put a TLS proxy in front;
  tokens sent over plain HTTP can be replayed until they expire.
- **Rate limits are per replica** and reset on restart. With several replicas
  the effective limit is `requests_per_minute × replicas`; use the platform's
  or a gateway's limiter for a hard global limit.
- **Revocation**: a token stays valid until `exp`. Keep token lifetimes short.

### What you should know before production

- **Conversation persistence**: with `memory.backend: in_memory` the
  checkpointer lives inside the process — conversation history does not
  survive restarts, cold starts, or multiple replicas. For real conversation
  continuity behind a scaling service, declare a shared backend
  (`postgres` or `redis`) in the blueprint's [`memory`](memory.md) section.
- **Platform auth is a second layer, not a replacement**: IAM or ingress
  rules decide who may reach the service; `deploy.auth` decides which user a
  request belongs to and therefore which conversations it can load. On GCP,
  `allow_unauthenticated: false` (the default) keeps IAM in front of the
  service. Note that platform IAM may itself use the `Authorization` header
  for its own token; check your platform's documentation for how to pass a
  second, application-level token, or use `mode: none` if IAM is the only
  check you need and conversation ids never leave your backend.
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

Secrets are collected automatically from the blueprint (`model_providers[*].api_key_env`, `tools[*].auth.*_env`, `deploy.auth.secret_env`) and read from your local environment at deploy time. Missing secrets produce a warning but do not block deployment.
