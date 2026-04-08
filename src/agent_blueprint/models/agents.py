"""Agent definition models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agent_blueprint.models.memory import AgentMemoryConfig


class ReasoningConfig(BaseModel):
    enabled: bool = True
    llm_kwargs: dict[str, Any] = Field(default_factory=dict)


class HumanInTheLoopTrigger(str, Enum):
    before_tool_call = "before_tool_call"
    after_tool_call = "after_tool_call"
    before_response = "before_response"
    always = "always"


class HumanInTheLoopConfig(BaseModel):
    enabled: bool = True
    trigger: HumanInTheLoopTrigger = HumanInTheLoopTrigger.before_tool_call
    tools: list[str] = Field(default_factory=list)
    message: str | None = None


class OutputFieldDef(BaseModel):
    type: str
    enum: list[str] | None = None
    description: str | None = None
    nullable: bool = False
    default: Any = None


class AgentDef(BaseModel):
    name: str | None = None
    model: str = "openai/gpt-4o"
    model_provider: str | None = None  # references a key in BlueprintSpec.model_providers
    system_prompt: str | None = None
    tools: list[str] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    output_schema: dict[str, OutputFieldDef] = Field(default_factory=dict)
    memory: AgentMemoryConfig | None = None
    human_in_the_loop: HumanInTheLoopConfig | None = None
    reasoning: ReasoningConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
