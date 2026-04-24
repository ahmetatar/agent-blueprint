"""Retriever resource models."""

from typing import Any

from pydantic import BaseModel, Field


class RetrieverDef(BaseModel):
    """A generic retriever implementation owned by user code.

    ABP does not know vector-store providers. The generated runtime calls the
    dotted `impl` with keyword arguments: query, top_k, and config.
    """

    impl: str
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
