"""Model provider configuration models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, model_validator


class ModelProvider(str, Enum):
    openai = "openai"
    anthropic = "anthropic"
    google = "google"
    ollama = "ollama"
    azure_openai = "azure_openai"
    bedrock = "bedrock"
    openai_compatible = "openai_compatible"  # any OpenAI-compatible endpoint


class ModelProviderDef(BaseModel):
    provider: ModelProvider
    # Credentials
    api_key_env: str | None = None
    # Custom endpoint (ollama, azure, openai_compatible)
    base_url: str | None = None
    # Azure-specific
    deployment: str | None = None
    api_version: str | None = None
    # Bedrock-specific
    region: str | None = None
    aws_profile_env: str | None = None
    # Extra arbitrary provider params
    extra: dict[str, Any] = {}

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "ModelProviderDef":
        if self.provider == ModelProvider.azure_openai:
            if not self.base_url:
                raise ValueError("azure_openai provider requires 'base_url' (endpoint URL)")
            if not self.deployment:
                raise ValueError("azure_openai provider requires 'deployment'")
        if self.provider == ModelProvider.ollama and not self.base_url:
            raise ValueError("ollama provider requires 'base_url' (e.g. http://localhost:11434)")
        if self.provider == ModelProvider.openai_compatible and not self.base_url:
            raise ValueError("openai_compatible provider requires 'base_url'")
        return self
