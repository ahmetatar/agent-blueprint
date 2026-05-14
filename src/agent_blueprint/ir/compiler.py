"""BlueprintSpec → AgentGraph IR compiler."""

from dataclasses import dataclass, field
from typing import Any

from agent_blueprint.exceptions import BlueprintCompilationError
from agent_blueprint.ir.expression import CompiledExpression, parse_expression
from agent_blueprint.models.agents import AgentDef, RagMode
from agent_blueprint.models.artifacts import ArtifactDef
from agent_blueprint.models.blueprint import BlueprintSpec, BlueprintSettings, IOSchema
from agent_blueprint.models.contracts import ContractsDef, NodeContractDef
from agent_blueprint.models.harness import HarnessDef
from agent_blueprint.models.graph import EdgeDef, NodeDef, NodeType
from agent_blueprint.models.memory import MemoryConfig
from agent_blueprint.models.policies import PoliciesDef
from agent_blueprint.models.providers import ModelProviderDef
from agent_blueprint.models.retrievers import RetrieverDef
from agent_blueprint.models.state import StateDef
from agent_blueprint.models.tools import ToolDef

_UNSUPPORTED_NODE_TYPES: set[str] = set()


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
    contract: NodeContractDef | None
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
    input_schema: IOSchema | None = None
    output_schema: IOSchema | None = None
    contracts: ContractsDef | None = None
    artifacts: dict[str, ArtifactDef] = field(default_factory=dict)
    artifact_owners: dict[str, list[str]] = field(default_factory=dict)
    policies: PoliciesDef | None = None
    harness: HarnessDef | None = None
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


@dataclass
class ExpandedGraph:
    state: StateDef
    nodes: dict[str, NodeDef]
    edges: list[EdgeDef]
    entry_point: str


def compile_blueprint(spec: BlueprintSpec) -> AgentGraph:
    """Compile a validated BlueprintSpec into the framework-agnostic AgentGraph IR."""
    expanded = _expand_subgraphs(spec)
    nodes = _compile_nodes(spec, expanded.nodes)
    edges = _compile_edges(expanded.edges)
    warnings = _collect_warnings(nodes)
    artifact_owners = _compile_artifact_owners(spec.artifacts)

    return AgentGraph(
        name=spec.blueprint.name,
        version=spec.blueprint.version,
        description=spec.blueprint.description,
        settings=spec.settings,
        state=expanded.state,
        nodes=nodes,
        edges=edges,
        entry_point=expanded.entry_point,
        memory=spec.memory,
        all_tools=spec.tools,
        retrievers=spec.retrievers,
        input_schema=spec.input,
        output_schema=spec.output,
        contracts=spec.contracts,
        artifacts=spec.artifacts,
        artifact_owners=artifact_owners,
        policies=spec.policies,
        harness=spec.harness,
        warnings=warnings,
    )


def _namespace_id(subgraph_node_id: str, inner_node_id: str) -> str:
    return f"{subgraph_node_id}__{inner_node_id}"


def _namespace_state_key(subgraph_node_id: str, inner_field: str) -> str:
    return f"{subgraph_node_id}__{inner_field}"


def _expand_subgraphs(spec: BlueprintSpec) -> ExpandedGraph:
    nodes: dict[str, NodeDef] = {}
    edges: list[EdgeDef] = []
    state = spec.state.model_copy(deep=True)
    subgraph_entry_nodes: dict[str, str] = {}
    subgraph_exit_nodes: dict[str, str] = {}

    for node_id, node_def in spec.graph.nodes.items():
        if node_def.type != NodeType.subgraph:
            nodes[node_id] = node_def
            continue

        if node_def.ref is None or node_def.ref not in spec.subgraphs:
            raise BlueprintCompilationError(
                f"Node '{node_id}' references undefined subgraph '{node_def.ref}'"
            )

        subgraph = spec.subgraphs[node_def.ref]
        entry_id = _namespace_id(node_id, "entry")
        exit_id = _namespace_id(node_id, "exit")
        subgraph_entry_nodes[node_id] = entry_id
        subgraph_exit_nodes[node_id] = exit_id

        nodes[entry_id] = NodeDef(
            type=NodeType.function,
            description=f"Subgraph '{node_id}' input adapter",
            metadata={
                "abp_subgraph_adapter": "entry",
                "subgraph_node": node_id,
                "ref": node_def.ref,
                "input_map": node_def.input_map,
            },
        )
        nodes[exit_id] = NodeDef(
            type=NodeType.function,
            description=f"Subgraph '{node_id}' output adapter",
            metadata={
                "abp_subgraph_adapter": "exit",
                "subgraph_node": node_id,
                "ref": node_def.ref,
                "output_map": node_def.output_map,
            },
        )

        for outer_field, inner_field in node_def.input_map.items():
            if outer_field in spec.state.fields:
                state.fields.setdefault(
                    _namespace_state_key(node_id, inner_field),
                    spec.state.fields[outer_field].model_copy(deep=True),
                )
        for inner_field, outer_field in node_def.output_map.items():
            if outer_field in spec.state.fields:
                state.fields.setdefault(
                    _namespace_state_key(node_id, inner_field),
                    spec.state.fields[outer_field].model_copy(deep=True),
                )

        state_key_map = {
            inner_field: _namespace_state_key(node_id, inner_field)
            for inner_field in set(node_def.input_map.values()) | set(node_def.output_map.keys())
        }

        for inner_node_id, inner_node in subgraph.nodes.items():
            expanded_node = inner_node.model_copy(deep=True)
            expanded_node.metadata = {
                **expanded_node.metadata,
                "abp_subgraph_node": node_id,
                "abp_subgraph_ref": node_def.ref,
                "abp_state_key_map": state_key_map,
            }
            if expanded_node.type == NodeType.parallel:
                expanded_node.branches = [
                    _namespace_id(node_id, branch) for branch in expanded_node.branches
                ]
                if expanded_node.join:
                    expanded_node.join = _namespace_id(node_id, expanded_node.join)
            nodes[_namespace_id(node_id, inner_node_id)] = expanded_node

        edges.append(EdgeDef.model_validate({
            "from": entry_id,
            "to": _namespace_id(node_id, subgraph.entry_point),
        }))
        for inner_edge in subgraph.edges:
            edge_source = _namespace_id(node_id, inner_edge.from_node)
            remapped_targets = []
            for target in inner_edge.get_targets():
                remapped = target.model_copy(deep=True)
                remapped.target = (
                    exit_id if remapped.target == "END" else _namespace_id(node_id, remapped.target)
                )
                remapped_targets.append(remapped)
            edges.append(EdgeDef.model_validate({"from": edge_source, "to": remapped_targets}))

    for edge in spec.graph.edges:
        from_node = subgraph_exit_nodes.get(edge.from_node, edge.from_node)
        remapped_targets = []
        for target in edge.get_targets():
            remapped = target.model_copy(deep=True)
            remapped.target = subgraph_entry_nodes.get(remapped.target, remapped.target)
            remapped_targets.append(remapped)
        edges.append(EdgeDef.model_validate({"from": from_node, "to": remapped_targets}))

    entry_point = subgraph_entry_nodes.get(spec.graph.entry_point, spec.graph.entry_point)
    return ExpandedGraph(
        state=state,
        nodes=nodes,
        edges=edges,
        entry_point=entry_point,
    )


def _compile_artifact_owners(artifacts: dict[str, ArtifactDef]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for artifact_name, artifact in artifacts.items():
        owners.setdefault(artifact.producer, []).append(artifact_name)
    return owners


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


def _compile_nodes(spec: BlueprintSpec, node_defs: dict[str, NodeDef]) -> list[IRNode]:
    nodes: list[IRNode] = []

    for node_id, node_def in node_defs.items():
        if node_def.type.value in _UNSUPPORTED_NODE_TYPES:
            raise BlueprintCompilationError(
                f"Node '{node_id}' uses unsupported node type '{node_def.type.value}'. "
                "This workflow semantic is declared in the schema but not implemented yet."
            )

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
            contract=spec.contracts.nodes.get(node_id) if spec.contracts else None,
            description=description,
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
            resolved_provider_def=resolved_provider_def,
        ))

    return nodes


def _compile_edges(edge_defs: list[EdgeDef]) -> list[IREdge]:
    edges: list[IREdge] = []

    for edge_def in edge_defs:
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
