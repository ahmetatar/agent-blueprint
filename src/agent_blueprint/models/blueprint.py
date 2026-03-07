"""Root BlueprintSpec model - entry point for all YAML validation."""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from agent_blueprint.models.agents import AgentDef
from agent_blueprint.models.graph import GraphDef
from agent_blueprint.models.mcp import McpServerDef
from agent_blueprint.models.memory import MemoryConfig
from agent_blueprint.models.state import StateDef
from agent_blueprint.models.tools import ToolDef, ToolType


class BlueprintMeta(BaseModel):
    name: str
    version: str = "1.0"
    description: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)


class BlueprintSettings(BaseModel):
    default_model: str = "gpt-4o"
    default_temperature: float = 0.7
    max_retries: int = 3
    timeout_seconds: int = 300


class IOFieldDef(BaseModel):
    type: str
    required: bool = True
    description: str | None = None
    nullable: bool = False
    default: Any = None
    enum: list[str] | None = None


class IOSchema(BaseModel):
    schema_def: dict[str, IOFieldDef] = Field(alias="schema", default_factory=dict)

    model_config = {"populate_by_name": True}


class BlueprintSpec(BaseModel):
    blueprint: BlueprintMeta
    settings: BlueprintSettings = Field(default_factory=BlueprintSettings)
    state: StateDef = Field(default_factory=StateDef)
    mcp_servers: dict[str, McpServerDef] = Field(default_factory=dict)
    agents: dict[str, AgentDef] = Field(default_factory=dict)
    tools: dict[str, ToolDef] = Field(default_factory=dict)
    graph: GraphDef
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    input: IOSchema | None = None
    output: IOSchema | None = None

    @model_validator(mode="after")
    def validate_references(self) -> "BlueprintSpec":
        # Validate agent tool references
        for agent_name, agent in self.agents.items():
            for tool_ref in agent.tools:
                if tool_ref not in self.tools:
                    raise ValueError(
                        f"Agent '{agent_name}' references undefined tool '{tool_ref}'"
                    )
            if agent.human_in_the_loop:
                for tool_ref in agent.human_in_the_loop.tools:
                    if tool_ref not in self.tools:
                        raise ValueError(
                            f"Agent '{agent_name}' human_in_the_loop references undefined tool '{tool_ref}'"
                        )

        # Validate mcp tool server references
        for tool_name, tool in self.tools.items():
            if tool.type == ToolType.mcp and tool.server not in self.mcp_servers:
                raise ValueError(
                    f"Tool '{tool_name}' references undefined MCP server '{tool.server}'"
                )

        # Validate node agent references
        for node_name, node in self.graph.nodes.items():
            if node.agent and node.agent not in self.agents:
                raise ValueError(
                    f"Node '{node_name}' references undefined agent '{node.agent}'"
                )
        return self
