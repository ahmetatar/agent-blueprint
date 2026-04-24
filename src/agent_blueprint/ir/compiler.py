"""BlueprintSpec → AgentGraph IR compiler."""

from dataclasses import dataclass, field
from typing import Any

from agent_blueprint.exceptions import BlueprintCompilationError
from agent_blueprint.ir.expression import CompiledExpression, parse_expression
from agent_blueprint.models.agents import AgentDef, RagMode
from agent_blueprint.models.blueprint import BlueprintSpec, BlueprintSettings
from agent_blueprint.models.graph import NodeDef
from agent_blueprint.models.memory import MemoryConfig
from agent_blueprint.models.providers import ModelProviderDef
from agent_blueprint.models.retrievers import RetrieverDef
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
    resolved_provider: str = "openai"       # e.g. "openai", "anthropic", "ollama"
    resolved_model: str = "gpt-4o"          # model name without provider prefix
    resolved_provider_def: ModelProviderDef | None = None


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
    retrievers: dict[str, RetrieverDef]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_node(self, node_id: str) -> IRNode | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def get_edges_from(self, node_id: str) -> list[IREdge]:
        return [e for e in self.edges if e.from_node == node_id]

    @property
    def used_providers(self) -> set[str]:
        """Return the set of resolved providers used across all agent nodes."""
        return {node.resolved_provider for node in self.nodes if node.agent}


def compile_blueprint(spec: BlueprintSpec) -> AgentGraph:
    """Compile a validated BlueprintSpec into the framework-agnostic AgentGraph IR."""
    nodes = _compile_nodes(spec)
    edges = _compile_edges(spec)
    warnings = _collect_warnings(nodes)

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
        retrievers=spec.retrievers,
        warnings=warnings,
    )


def _collect_warnings(nodes: list[IRNode]) -> list[str]:
    warnings: list[str] = []
    for node in nodes:
        if node.agent and node.agent.reasoning and node.agent.reasoning.enabled:
            if not node.agent.reasoning.params:
                warnings.append(
                    f"Node '{node.id}': reasoning.enabled is set but params is empty "
                    f"— no reasoning parameters will be passed to the LLM."
                )
            if not node.resolved_provider_def and "/" not in node.agent.model:
                warnings.append(
                    f"Node '{node.id}': reasoning.enabled is set but no model_provider or "
                    f"provider/model prefix was found — the OpenAI adapter will be used by default."
                )
    return warnings


def _resolve_llm(agent: AgentDef, spec: BlueprintSpec) -> tuple[str, str, ModelProviderDef | None]:
    """Resolve (provider, model_name, provider_def) for an agent at compile time."""
    # Always strip "provider/" prefix — model_name is the bare model identifier
    raw_model = agent.model
    model_name = raw_model.split("/", 1)[1] if "/" in raw_model else raw_model

    provider_def: ModelProviderDef | None = None

    # Look up explicit model_provider, then fall back to settings default
    provider_key = agent.model_provider or spec.settings.default_model_provider
    if provider_key:
        provider_def = spec.model_providers.get(provider_key)

    if provider_def:
        return provider_def.provider.value, model_name, provider_def

    # No model_providers configured — parse "provider/model" syntax
    if "/" in raw_model:
        provider = raw_model.split("/", 1)[0]
    else:
        provider = "openai"
    return provider, model_name, None


def _compile_nodes(spec: BlueprintSpec) -> list[IRNode]:
    nodes: list[IRNode] = []

    for node_id, node_def in spec.graph.nodes.items():
        agent: AgentDef | None = None
        tool_defs: dict[str, ToolDef] = {}
        resolved_provider = "openai"
        resolved_model = "gpt-4o"
        resolved_provider_def: ModelProviderDef | None = None

        if node_def.agent:
            agent = spec.agents.get(node_def.agent)
            if agent is None:
                raise BlueprintCompilationError(
                    f"Node '{node_id}' references undefined agent '{node_def.agent}'"
                )
            for tool_name in agent.tools:
                if tool_name in spec.tools:
                    tool_defs[tool_name] = spec.tools[tool_name]
            if (
                agent.rag
                and agent.rag.mode in (RagMode.tool_only, RagMode.hybrid)
                and agent.rag.retrieval_tool in spec.tools
            ):
                tool_defs[agent.rag.retrieval_tool] = spec.tools[agent.rag.retrieval_tool]
            resolved_provider, resolved_model, resolved_provider_def = _resolve_llm(agent, spec)

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
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
            resolved_provider_def=resolved_provider_def,
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
