"""BlueprintSpec → AgentGraph IR compiler."""

from dataclasses import dataclass, field
from typing import Any

from agent_blueprint.exceptions import BlueprintCompilationError
from agent_blueprint.ir.expression import CompiledExpression, parse_expression
from agent_blueprint.models.agents import AgentDef
from agent_blueprint.models.blueprint import BlueprintSpec, BlueprintSettings
from agent_blueprint.models.graph import NodeDef, NodeType
from agent_blueprint.models.memory import MemoryConfig
from agent_blueprint.models.state import StateDef
from agent_blueprint.models.tools import ToolDef


@dataclass
class IREdgeTarget:
    target: str
    condition: CompiledExpression | None
    is_default: bool


@dataclass
class IREdge:
    from_node: str
    targets: list[IREdgeTarget]

    @property
    def is_conditional(self) -> bool:
        return any(t.condition is not None for t in self.targets)


@dataclass
class IRNode:
    id: str
    node_def: NodeDef
    agent: AgentDef | None
    tool_defs: dict[str, ToolDef]
    description: str


@dataclass
class AgentGraph:
    """Framework-agnostic intermediate representation of an agent blueprint."""
    name: str
    version: str
    description: str | None
    settings: BlueprintSettings
    state: StateDef
    nodes: list[IRNode]
    edges: list[IREdge]
    entry_point: str
    memory: MemoryConfig
    all_tools: dict[str, ToolDef]
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_node(self, node_id: str) -> IRNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def get_edges_from(self, node_id: str) -> list[IREdge]:
        return [e for e in self.edges if e.from_node == node_id]


def compile_blueprint(spec: BlueprintSpec) -> AgentGraph:
    """Compile a validated BlueprintSpec into the framework-agnostic AgentGraph IR."""
    nodes = _compile_nodes(spec)
    edges = _compile_edges(spec)

    return AgentGraph(
        name=spec.blueprint.name,
        version=spec.blueprint.version,
        description=spec.blueprint.description,
        settings=spec.settings,
        state=spec.state,
        nodes=nodes,
        edges=edges,
        entry_point=spec.graph.entry_point,
        memory=spec.memory,
        all_tools=spec.tools,
    )


def _compile_nodes(spec: BlueprintSpec) -> list[IRNode]:
    nodes: list[IRNode] = []

    for node_id, node_def in spec.graph.nodes.items():
        agent: AgentDef | None = None
        tool_defs: dict[str, ToolDef] = {}

        if node_def.agent:
            agent = spec.agents.get(node_def.agent)
            if agent is None:
                raise BlueprintCompilationError(
                    f"Node '{node_id}' references undefined agent '{node_def.agent}'"
                )
            for tool_name in agent.tools:
                if tool_name in spec.tools:
                    tool_defs[tool_name] = spec.tools[tool_name]

        description = (
            node_def.description
            or (agent.name if agent and agent.name else None)
            or node_id
        )

        nodes.append(IRNode(
            id=node_id,
            node_def=node_def,
            agent=agent,
            tool_defs=tool_defs,
            description=description,
        ))

    return nodes


def _compile_edges(spec: BlueprintSpec) -> list[IREdge]:
    edges: list[IREdge] = []

    for edge_def in spec.graph.edges:
        targets: list[IREdgeTarget] = []

        for edge_target in edge_def.get_targets():
            compiled_condition: CompiledExpression | None = None

            if edge_target.condition:
                try:
                    compiled_condition = parse_expression(edge_target.condition)
                except Exception as e:
                    raise BlueprintCompilationError(
                        f"Edge from '{edge_def.from_node}' has invalid condition "
                        f"'{edge_target.condition}': {e}"
                    ) from e

            targets.append(IREdgeTarget(
                target=edge_target.target,
                condition=compiled_condition,
                is_default=edge_target.default or edge_target.condition is None,
            ))

        if not targets:
            raise BlueprintCompilationError(
                f"Edge from '{edge_def.from_node}' has no targets"
            )

        edges.append(IREdge(from_node=edge_def.from_node, targets=targets))

    return edges
