"""Graph / workflow definition models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class NodeType(str, Enum):
    agent = "agent"
    function = "function"
    handoff = "handoff"
    parallel = "parallel"
    subgraph = "subgraph"


class ParallelFailurePolicy(str, Enum):
    fail_fast = "fail_fast"


class HandoffChannel(str, Enum):
    slack = "slack"
    email = "email"
    webhook = "webhook"
    console = "console"


class EdgeTarget(BaseModel):
    condition: str | None = None
    target: str = ""
    default: bool = False

    @model_validator(mode="before")
    @classmethod
    def handle_default_shorthand(cls, values: Any) -> Any:
        """Support YAML shorthand: `- default: END` → {target: END, default: true}"""
        if isinstance(values, dict) and "target" not in values and "default" in values:
            target = values.pop("default")
            if isinstance(target, str):
                return {"target": target, "default": True, "condition": None}
        return values


class EdgeDef(BaseModel):
    from_node: str = Field(alias="from")
    to: list[EdgeTarget] | str

    model_config = {"populate_by_name": True}

    def get_targets(self) -> list[EdgeTarget]:
        if isinstance(self.to, str):
            return [EdgeTarget(target=self.to, default=True)]
        return self.to


class NodeDef(BaseModel):
    agent: str | None = None
    type: NodeType = NodeType.agent
    branches: list[str] = Field(default_factory=list)
    join: str | None = None
    failure_policy: ParallelFailurePolicy = ParallelFailurePolicy.fail_fast
    description: str | None = None
    action: str | None = None
    channel: HandoffChannel | None = None
    message_template: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_type_fields(self) -> "NodeDef":
        if self.type == NodeType.agent and not self.agent:
            raise ValueError("agent nodes require an 'agent' reference")
        if self.type == NodeType.parallel:
            if self.agent:
                raise ValueError("parallel nodes cannot declare an 'agent' reference")
            if not self.branches:
                raise ValueError("parallel nodes require at least one branch")
            if not self.join:
                raise ValueError("parallel nodes require a 'join' target")
            if self.join in self.branches:
                raise ValueError("parallel node 'join' target cannot also be a branch")
        if self.type == NodeType.handoff and not self.channel:
            self.channel = HandoffChannel.console
        return self


class GraphDef(BaseModel):
    entry_point: str
    nodes: dict[str, NodeDef]
    edges: list[EdgeDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "GraphDef":
        if self.entry_point not in self.nodes:
            raise ValueError(
                f"entry_point '{self.entry_point}' is not defined in nodes"
            )
        # Validate edge source nodes exist
        for edge in self.edges:
            if edge.from_node not in self.nodes and edge.from_node != "START":
                raise ValueError(
                    f"Edge 'from' node '{edge.from_node}' is not defined in nodes"
                )
            for target in edge.get_targets():
                if target.target not in self.nodes and target.target != "END":
                    raise ValueError(
                        f"Edge target '{target.target}' is not defined in nodes"
                    )
        for node_id, node in self.nodes.items():
            if node.type != NodeType.parallel:
                continue
            for branch in node.branches:
                if branch not in self.nodes:
                    raise ValueError(
                        f"Parallel node '{node_id}' branch '{branch}' is not defined in nodes"
                    )
            if node.join not in self.nodes:
                raise ValueError(
                    f"Parallel node '{node_id}' join target '{node.join}' is not defined in nodes"
                )
            if any(edge.from_node == node_id for edge in self.edges):
                raise ValueError(
                    f"Parallel node '{node_id}' uses branches/join and cannot also declare explicit outgoing edges"
                )
            branch_edges = sorted(
                edge.from_node for edge in self.edges if edge.from_node in node.branches
            )
            if branch_edges:
                rendered = ", ".join(branch_edges)
                raise ValueError(
                    f"Parallel node '{node_id}' branch node(s) cannot declare explicit outgoing edges: {rendered}"
                )
        return self
