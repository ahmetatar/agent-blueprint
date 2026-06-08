"""Graph view-model for the editor canvas (phase E1).

Built from ``BlueprintSpec`` rather than the compiled IR on purpose: the
compiler flattens subgraphs into namespaced nodes, but the canvas wants them
as collapsible *groups*. The spec keeps that structure, and `_resolve_llm`
(the compiler's own resolution helper) is reused so agent nodes display the
exact provider/model the generated project would use — the frontend never
re-implements YAML semantics.

Synthetic edges make implicit routing visible:

- ``START -> entry_point`` (unless the blueprint declares an edge from START)
- supervisor ``delegation`` / ``return`` edges to/from each worker, plus the
  ``on_finish`` route (END when unset)
- parallel fan-out edges to each branch and fan-in edges to the join node
- subgraph-internal ``END`` targets route to a per-group exit terminal
"""

from typing import Any

from agent_blueprint.ir.compiler import _resolve_llm
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.graph import EdgeTarget, GraphDef, NodeDef, NodeType

START_ID = "__start__"
END_ID = "__end__"

# Mirrors the compiler's subgraph expansion cap; spec-level ref cycles are a
# compile error, but the view-model must not hang on them either.
_MAX_DEPTH = 10


def build_view_model(spec: BlueprintSpec) -> dict[str, Any]:
    """Nodes + edges for the canvas, pre-digested server-side."""
    builder = _Builder(spec)
    builder.emit_graph(spec.graph, parent=None, prefix="", chain=(), graph_ref="graph")
    return {
        "entry_point": spec.graph.entry_point,
        "nodes": builder.nodes,
        "edges": builder.edges,
        # For the add-node dialog and config forms: agent nodes must
        # reference a defined agent; tool pickers list the defined tools.
        "agents": list(spec.agents),
        "tools": list(spec.tools),
        # Declared state fields — the edge-condition editor offers these as
        # `state.<field>` autocomplete chips.
        "state_fields": list(spec.state.fields),
    }


class _Builder:
    def __init__(self, spec: BlueprintSpec) -> None:
        self.spec = spec
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self._emitted_node_ids: set[str] = set()

    # -- nodes ------------------------------------------------------------

    def emit_graph(
        self,
        graph: GraphDef,
        parent: str | None,
        prefix: str,
        chain: tuple[str, ...],
        graph_ref: str,
    ) -> None:
        top_level = parent is None
        end_id = END_ID if top_level else f"{prefix}{END_ID}"

        for node_id, node_def in graph.nodes.items():
            vid = f"{prefix}{node_id}"
            if node_def.type == NodeType.subgraph and node_def.ref is not None:
                self._emit_subgraph_group(vid, node_id, node_def, parent, chain, graph_ref)
            else:
                self._add_node(
                    self._node_payload(vid, node_id, node_def, parent, graph_ref),
                    entry=(node_id == graph.entry_point and not top_level),
                )

        if top_level:
            self._add_node(
                {"id": START_ID, "type": "start", "label": "START", "parent": None}
            )
        for edge in graph.edges:
            source = START_ID if edge.from_node == "START" else f"{prefix}{edge.from_node}"
            targets = edge.get_targets()
            for target in targets:
                self._emit_edge(
                    source,
                    target,
                    prefix,
                    end_id,
                    conditional_group=len(targets) > 1,
                    ref={
                        "graph": graph_ref,
                        "from": edge.from_node,
                        "target": target.target,
                        "condition": target.condition,
                    },
                )

        if top_level and not any(edge.from_node == "START" for edge in graph.edges):
            self._append_edge(START_ID, graph.entry_point, kind="entry")

        for node_id, node_def in graph.nodes.items():
            vid = f"{prefix}{node_id}"
            if node_def.type == NodeType.supervisor:
                self._emit_supervisor_edges(vid, node_def, prefix, end_id)
            elif node_def.type == NodeType.parallel:
                self._emit_parallel_edges(vid, node_def, prefix)

    def _emit_subgraph_group(
        self,
        vid: str,
        node_id: str,
        node_def: NodeDef,
        parent: str | None,
        chain: tuple[str, ...],
        graph_ref: str,
    ) -> None:
        ref = node_def.ref or ""
        group: dict[str, Any] = {
            "id": vid,
            "type": "subgraph",
            "label": node_id,
            "ref": ref,
            "parent": parent,
            "description": node_def.description,
            "graph_ref": graph_ref,
        }
        # Ref cycles / runaway nesting are compile errors; render the node
        # without expanding instead of recursing forever.
        if ref in chain or len(chain) >= _MAX_DEPTH:
            group["expanded"] = False
            self._add_node(group)
            return
        subgraph = self.spec.subgraphs[ref]
        group["expanded"] = True
        self._add_node(group)
        self.emit_graph(
            subgraph,
            parent=vid,
            prefix=f"{vid}:",
            chain=(*chain, ref),
            graph_ref=f"subgraphs.{ref}",
        )

    def _node_payload(
        self, vid: str, node_id: str, node_def: NodeDef, parent: str | None, graph_ref: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": vid,
            "type": node_def.type.value,
            "label": node_id,
            "parent": parent,
            "description": node_def.description,
            # Ops scope: which YAML graph this node lives in.
            "graph_ref": graph_ref,
            # The node's id in the *flattened* runtime graph — trace events
            # carry this. Canvas vids join the nesting chain with ":"; the
            # compiler's _namespace_id joins the same chain with "__".
            "runtime_id": vid.replace(":", "__"),
            # Effective field values (defaults filled in) for the config form.
            "config": node_def.model_dump(mode="json"),
        }
        if node_def.agent is not None:
            agent = self.spec.agents.get(node_def.agent)
            payload["agent"] = node_def.agent
            if agent is not None:
                provider, model, _ = _resolve_llm(agent, self.spec)
                payload["provider"] = provider
                payload["model"] = model
                payload["tools"] = list(agent.tools)
                payload["agent_config"] = agent.model_dump(mode="json")
        if node_def.type == NodeType.function:
            payload["action"] = node_def.action
        if node_def.type == NodeType.handoff and node_def.channel is not None:
            payload["channel"] = node_def.channel.value
        if node_def.type == NodeType.supervisor:
            payload["workers"] = list(node_def.workers)
            payload["max_iterations"] = node_def.max_iterations
        if node_def.retry.max_attempts > 1:
            payload["retry"] = node_def.retry.max_attempts
        return payload

    def _add_node(self, payload: dict[str, Any], entry: bool = False) -> None:
        if entry:
            payload["entry"] = True
        self.nodes.append(payload)
        self._emitted_node_ids.add(payload["id"])

    def _ensure_terminal(self, end_id: str, parent: str | None) -> None:
        if end_id not in self._emitted_node_ids:
            self._add_node({"id": end_id, "type": "end", "label": "END", "parent": parent})

    # -- edges ------------------------------------------------------------

    def _emit_edge(
        self,
        source: str,
        target: EdgeTarget,
        prefix: str,
        end_id: str,
        conditional_group: bool,
        ref: dict[str, Any],
    ) -> None:
        resolved = self._resolve_target(target.target, prefix, end_id)
        if target.condition is not None:
            self._append_edge(source, resolved, kind="conditional", label=target.condition, ref=ref)
        elif target.default and conditional_group:
            self._append_edge(source, resolved, kind="default", label="default", ref=ref)
        else:
            self._append_edge(source, resolved, kind="normal", ref=ref)

    def _emit_supervisor_edges(
        self, vid: str, node_def: NodeDef, prefix: str, end_id: str
    ) -> None:
        for worker in node_def.workers:
            self._append_edge(vid, f"{prefix}{worker}", kind="delegation")
            self._append_edge(f"{prefix}{worker}", vid, kind="return")
        finish = self._resolve_target(node_def.on_finish or "END", prefix, end_id)
        self._append_edge(vid, finish, kind="normal", label="on finish")

    def _emit_parallel_edges(self, vid: str, node_def: NodeDef, prefix: str) -> None:
        join = f"{prefix}{node_def.join}"
        for branch in node_def.branches:
            self._append_edge(vid, f"{prefix}{branch}", kind="parallel")
            self._append_edge(f"{prefix}{branch}", join, kind="parallel")

    def _resolve_target(self, target: str, prefix: str, end_id: str) -> str:
        if target == "END":
            # Terminals are emitted lazily so unreferenced ones don't clutter
            # the canvas. Subgraph END means "exit the group", so the terminal
            # lives inside the group (prefix is always "<group-id>:").
            self._ensure_terminal(end_id, parent=prefix[:-1] if prefix else None)
            return end_id
        return f"{prefix}{target}"

    def _append_edge(
        self,
        source: str,
        target: str,
        kind: str,
        label: str | None = None,
        ref: dict[str, Any] | None = None,
    ) -> None:
        self.edges.append(
            {
                "id": f"e{len(self.edges)}:{source}->{target}",
                "source": source,
                "target": target,
                "kind": kind,
                "label": label,
                # Present only for edges that exist in the YAML (`graph.edges`);
                # synthetic edges (entry/supervisor/parallel) are not editable.
                "ref": ref,
            }
        )
