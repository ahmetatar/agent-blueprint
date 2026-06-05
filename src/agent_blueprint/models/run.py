"""Run configuration models — declarative local-execution settings (`run:`)."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SandboxEngine(str, Enum):
    auto = "auto"
    docker = "docker"
    podman = "podman"


class SandboxNetwork(str, Enum):
    none = "none"
    bridge = "bridge"
    host = "host"


class SandboxConfig(BaseModel):
    """Container sandbox settings for `abp run --sandbox`."""

    enabled: bool = False                # plain `abp run` also sandboxes when true
    engine: SandboxEngine = SandboxEngine.auto
    image: str = "python:3.11-slim"      # base image for the generated Dockerfile
    network: SandboxNetwork = SandboxNetwork.bridge
    memory: str | None = None            # e.g. "512m"
    cpus: float | None = None            # e.g. 1.0
    env_passthrough: list[str] = Field(default_factory=list)

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("sandbox.memory must be a non-empty string (e.g. '512m')")
        return v

    @field_validator("cpus")
    @classmethod
    def validate_cpus(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("sandbox.cpus must be positive")
        return v


class RunConfig(BaseModel):
    """Top-level `run:` blueprint section."""

    sandbox: SandboxConfig | None = None
