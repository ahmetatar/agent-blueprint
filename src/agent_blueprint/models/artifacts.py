"""Artifact schema models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ArtifactFormat(str, Enum):
    markdown = "markdown"
    json = "json"
    yaml = "yaml"
    text = "text"


class ArtifactDef(BaseModel):
    format: ArtifactFormat
    producer: str
    path: str
    contract: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactsDef(BaseModel):
    root: dict[str, ArtifactDef]
