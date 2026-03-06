"""Memory / persistence configuration models."""

from enum import Enum

from pydantic import BaseModel


class MemoryBackend(str, Enum):
    sqlite = "sqlite"
    postgres = "postgres"
    redis = "redis"
    in_memory = "in_memory"


class CheckpointStrategy(str, Enum):
    node = "node"
    edge = "edge"
    manual = "manual"


class AgentMemoryType(str, Enum):
    conversation_buffer = "conversation_buffer"
    summary = "summary"
    vector = "vector"


class AgentMemoryConfig(BaseModel):
    type: AgentMemoryType = AgentMemoryType.conversation_buffer
    max_tokens: int | None = None
    max_messages: int | None = None


class MemoryConfig(BaseModel):
    type: str = "checkpoint"
    backend: MemoryBackend = MemoryBackend.in_memory
    connection_string_env: str | None = None
    checkpoint_every: CheckpointStrategy = CheckpointStrategy.node
