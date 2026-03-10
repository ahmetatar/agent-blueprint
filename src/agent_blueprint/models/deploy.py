"""Deploy configuration models."""

from pydantic import BaseModel, Field


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
