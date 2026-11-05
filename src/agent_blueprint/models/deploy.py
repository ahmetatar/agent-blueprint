"""Deploy configuration models."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

#: Algorithms allowed with a shared secret (symmetric) and with a JWKS (asymmetric).
#: Keeping the two sets disjoint prevents algorithm-confusion attacks, where a
#: token signed with HS256 using the public key would pass an RS256 verifier.
HMAC_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)


class DeployAuthConfig(BaseModel):
    """Who may call the deployed `/invoke` endpoint, and as which user.

    The user id is read from a verified JWT and becomes the prefix of every
    conversation's checkpointer thread, so one user can never load another
    user's conversation.
    """

    mode: Literal["jwt", "none"] = "jwt"
    jwks_url: str | None = None          # asymmetric keys from an identity provider
    secret_env: str | None = None        # env var holding a shared HMAC secret
    algorithms: list[str] | None = None  # default: RS256 with jwks_url, HS256 with secret_env
    issuer: str | None = None
    audience: str | None = None
    user_claim: str = "sub"

    @model_validator(mode="after")
    def _check_consistency(self) -> "DeployAuthConfig":
        jwt_fields = {
            "jwks_url": self.jwks_url,
            "secret_env": self.secret_env,
            "algorithms": self.algorithms,
            "issuer": self.issuer,
            "audience": self.audience,
        }
        if self.mode == "none":
            set_fields = sorted(name for name, value in jwt_fields.items() if value)
            if set_fields:
                raise ValueError(
                    "deploy.auth.mode is 'none' but JWT settings are set: "
                    f"{', '.join(set_fields)}. Remove them or use mode: jwt."
                )
            return self
        if self.jwks_url and self.secret_env:
            raise ValueError("deploy.auth: set either jwks_url or secret_env, not both")
        if self.jwks_url and not self.audience:
            raise ValueError(
                "deploy.auth.audience is required with jwks_url: an identity provider "
                "issues tokens for many services, and without an audience check a "
                "token meant for another service would be accepted"
            )
        if self.algorithms is not None:
            if not self.algorithms:
                raise ValueError("deploy.auth.algorithms must not be empty")
            allowed = HMAC_ALGORITHMS if self.secret_env else ASYMMETRIC_ALGORITHMS
            kind = "secret_env (HMAC)" if self.secret_env else "jwks_url (asymmetric)"
            bad = sorted(set(self.algorithms) - allowed)
            if bad:
                raise ValueError(
                    f"deploy.auth.algorithms {bad} cannot be used with {kind}; "
                    f"allowed: {sorted(allowed)}"
                )
        if not self.user_claim:
            raise ValueError("deploy.auth.user_claim must not be empty")
        return self

    @property
    def is_configured(self) -> bool:
        """True when the server can verify tokens (or auth is explicitly off)."""
        return self.mode == "none" or bool(self.jwks_url or self.secret_env)

    @property
    def resolved_algorithms(self) -> list[str]:
        if self.algorithms:
            return list(self.algorithms)
        return ["HS256"] if self.secret_env else ["RS256"]


class DeployRateLimitConfig(BaseModel):
    """Per-user request limit on `/invoke`, enforced inside each server replica."""

    requests_per_minute: int = Field(default=60, ge=0)  # 0 disables the limit


class AzureDeployConfig(BaseModel):
    subscription_env: str = "AZURE_SUBSCRIPTION_ID"
    resource_group: str
    location: str = "eastus"
    acr_name: str                        # Azure Container Registry name
    container_app_env: str               # Container Apps Environment name
    app_name: str | None = None          # defaults to blueprint name
    min_replicas: int = 0
    max_replicas: int = 3


class AWSDeployConfig(BaseModel):
    region: str = "us-east-1"
    ecr_repo: str                        # ECR repository name
    service_name: str | None = None      # App Runner service name; defaults to blueprint name
    access_role_arn_env: str = "AWS_APPRUNNER_ROLE_ARN"


class GCPDeployConfig(BaseModel):
    project_env: str = "GCP_PROJECT_ID"
    region: str = "us-central1"
    service_name: str | None = None      # Cloud Run service name; defaults to blueprint name
    artifact_repo: str = "cloud-run-source-deploy"
    allow_unauthenticated: bool = False


class DockerDeployConfig(BaseModel):
    host_port: int = 8080
    container_name: str | None = None    # defaults to blueprint name
    network: str | None = None           # e.g. "host" on Linux
    platform: str | None = None          # e.g. "linux/amd64"


# Podman shares the same config shape as Docker
PodmanDeployConfig = DockerDeployConfig


class DeployConfig(BaseModel):
    platform: str | None = None          # default platform: azure | aws | gcp | docker | podman
    azure: AzureDeployConfig | None = None
    aws: AWSDeployConfig | None = None
    gcp: GCPDeployConfig | None = None
    docker: DockerDeployConfig | None = None
    podman: PodmanDeployConfig | None = None
    auth: DeployAuthConfig = Field(default_factory=DeployAuthConfig)
    rate_limit: DeployRateLimitConfig = Field(default_factory=DeployRateLimitConfig)
