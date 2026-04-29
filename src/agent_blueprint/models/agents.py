"""Agent definition models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_blueprint.models.memory import AgentMemoryConfig


class ReasoningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


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


class RagMode(str, Enum):
    tool_only = "tool_only"
    context_only = "context_only"
    hybrid = "hybrid"


class RagQuerySource(str, Enum):
    last_user_message = "last_user_message"


class RagConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    retrieval_tool: str = Field(alias="tool")
    mode: RagMode = RagMode.context_only
    query_from: RagQuerySource = RagQuerySource.last_user_message
    max_context_chars: int | None = 8000
    context_prompt: str = (
        "Relevant retrieved context. Use it when it is helpful and ignore it when it is not relevant."
    )


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
    llm_params: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, OutputFieldDef] = Field(default_factory=dict)
    memory: AgentMemoryConfig | None = None
    human_in_the_loop: HumanInTheLoopConfig | None = None
    reasoning: ReasoningConfig | None = None
    rag: RagConfig | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_legacy_output_schema(self) -> "AgentDef":
        if self.output_schema:
            raise ValueError(
                "agents.*.output_schema is no longer supported. "
                "Use contracts.nodes.<node>.output_contract with contracts.outputs instead."
            )
        return self
