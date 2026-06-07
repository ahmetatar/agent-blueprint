"""Targeted ruamel mutations behind ``POST /api/blueprint/ops`` (phase E2b).

Canvas edits are applied as surgical mutations to the loaded ``CommentedMap``
— never "re-serialize the Pydantic model", which would destroy comments, key
order, and quoting. Each op touches only the keys it names; everything else
round-trips byte-identically (see the dump settings in ``utils.yaml_loader``).

The ops layer is deliberately schema-naive: it edits raw YAML structure and
leaves semantic validation to ``BlueprintSpec`` on the mutated document (the
server rejects the whole batch and writes nothing if validation fails). The
only structural knowledge baked in is the edge-target sugar it must navigate:
scalar ``to: X`` and the ``- default: X`` shorthand.

Ops address graphs via a ``graph`` scope: ``"graph"`` (the main graph) or
``"subgraphs.<name>"`` — matching the view-model's ``graph_ref``.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from ruamel.yaml.comments import CommentedMap, CommentedSeq

_SEGMENT = re.compile(r"^([^\[\]]+)((?:\[\d+\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")

MAIN_GRAPH = "graph"
_SUBGRAPH_PREFIX = "subgraphs."


class OpError(Exception):
    """A structured edit that cannot be applied to the current document."""


class AddNodeOp(BaseModel):
    op: Literal["add_node"]
    graph: str = MAIN_GRAPH
    node_id: str
    node: dict[str, Any]


class RemoveNodeOp(BaseModel):
    """Remove a node and every edge entry that references it."""

    op: Literal["remove_node"]
    graph: str = MAIN_GRAPH
    node_id: str


class AddEdgeOp(BaseModel):
    op: Literal["add_edge"]
    graph: str = MAIN_GRAPH
    from_node: str
    target: str
    condition: str | None = None


class RemoveEdgeOp(BaseModel):
    """Remove one edge target (matched by target + condition)."""

    op: Literal["remove_edge"]
    graph: str = MAIN_GRAPH
    from_node: str
    target: str
    condition: str | None = None


class RetargetEdgeOp(BaseModel):
    """Point an existing edge target at a different node, in place.

    Matching mirrors ``remove_edge`` (from + target + condition). The entry
    keeps its position in the ``to`` list — evaluation order is routing
    semantics when conditions overlap — and keeps its authored form
    (``target:`` mapping or ``default:`` shorthand) and any comments.
    """

    op: Literal["retarget_edge"]
    graph: str = MAIN_GRAPH
    from_node: str
    target: str
    condition: str | None = None
    new_target: str


class SetFieldOp(BaseModel):
    """Set a value at a dotted path (``graph.edges[0].to[1].condition``).

    Missing intermediate mappings are created; list indices must exist.
    """

    op: Literal["set_field"]
    path: str
    value: Any = None


class UnsetFieldOp(BaseModel):
    """Remove the key at a dotted path (revert a field to its default).

    Idempotent: a missing key (or missing parent) is a no-op, so clearing a
    field that was never authored does not fail the batch.
    """

    op: Literal["unset_field"]
    path: str


EditOp = Annotated[
    AddNodeOp
    | RemoveNodeOp
    | AddEdgeOp
    | RemoveEdgeOp
    | RetargetEdgeOp
    | SetFieldOp
    | UnsetFieldOp,
    Field(discriminator="op"),
]


def apply_ops(document: CommentedMap, ops: list[EditOp]) -> None:
    """Apply ops in order; raises OpError on the first one that cannot apply."""
    for op in ops:
        if isinstance(op, AddNodeOp):
            _add_node(document, op)
        elif isinstance(op, RemoveNodeOp):
            _remove_node(document, op)
        elif isinstance(op, AddEdgeOp):
            _add_edge(document, op)
        elif isinstance(op, RemoveEdgeOp):
            _remove_edge(document, op)
        elif isinstance(op, RetargetEdgeOp):
            _retarget_edge(document, op)
        elif isinstance(op, SetFieldOp):
            _set_field(document, op)
        else:
            _unset_field(document, op)


# -- graph scope --------------------------------------------------------------


def _graph_container(document: CommentedMap, graph: str) -> Any:
    if graph == MAIN_GRAPH:
        container = document.get("graph")
        if not isinstance(container, dict):
            raise OpError("blueprint has no 'graph' section")
        return container
    if graph.startswith(_SUBGRAPH_PREFIX):
        name = graph[len(_SUBGRAPH_PREFIX) :]
        subgraphs = document.get("subgraphs")
        container = subgraphs.get(name) if isinstance(subgraphs, dict) else None
        if not isinstance(container, dict):
            raise OpError(f"unknown subgraph '{name}'")
        return container
    raise OpError(f"invalid graph scope '{graph}' (expected 'graph' or 'subgraphs.<name>')")


def _nodes_of(container: Any, graph: str) -> Any:
    nodes = container.get("nodes")
    if not isinstance(nodes, dict):
        raise OpError(f"'{graph}' has no 'nodes' mapping")
    return nodes


# -- node ops -----------------------------------------------------------------


def _add_node(document: CommentedMap, op: AddNodeOp) -> None:
    nodes = _nodes_of(_graph_container(document, op.graph), op.graph)
    if op.node_id in nodes:
        raise OpError(f"node '{op.node_id}' already exists in '{op.graph}'")
    nodes[op.node_id] = op.node


def _remove_node(document: CommentedMap, op: RemoveNodeOp) -> None:
    container = _graph_container(document, op.graph)
    nodes = _nodes_of(container, op.graph)
    if op.node_id not in nodes:
        raise OpError(f"node '{op.node_id}' does not exist in '{op.graph}'")
    del nodes[op.node_id]

    edges = container.get("edges")
    if not isinstance(edges, list):
        return
    for i in reversed(range(len(edges))):
        edge = edges[i]
        if not isinstance(edge, dict):
            continue
        if edge.get("from") == op.node_id:
            del edges[i]
            continue
        to = edge.get("to")
        if isinstance(to, str) and to == op.node_id:
            del edges[i]
        elif isinstance(to, list):
            for j in reversed(range(len(to))):
                if _target_of(to[j]) == op.node_id:
                    del to[j]
            if not to:
                del edges[i]


# -- edge ops -----------------------------------------------------------------


def _target_of(item: Any) -> str | None:
    if isinstance(item, dict):
        if "target" in item:
            target = item["target"]
            return target if isinstance(target, str) else None
        default = item.get("default")  # `- default: X` shorthand
        return default if isinstance(default, str) else None
    return None


def _condition_of(item: Any) -> str | None:
    if isinstance(item, dict) and "target" in item:
        condition = item.get("condition")
        return condition if isinstance(condition, str) else None
    return None  # the `- default: X` shorthand is unconditional


def _new_target(target: str, condition: str | None) -> dict[str, Any]:
    if condition is None:
        return {"target": target}
    return {"condition": condition, "target": target}


def _add_edge(document: CommentedMap, op: AddEdgeOp) -> None:
    container = _graph_container(document, op.graph)
    edges = container.get("edges")
    if edges is None:
        edges = CommentedSeq()
        container["edges"] = edges
    if not isinstance(edges, list):
        raise OpError(f"'{op.graph}' edges must be a list")

    edge = next(
        (e for e in edges if isinstance(e, dict) and e.get("from") == op.from_node), None
    )
    if edge is None:
        if op.condition is None:
            edges.append({"from": op.from_node, "to": op.target})
        else:
            edges.append({"from": op.from_node, "to": [_new_target(op.target, op.condition)]})
        return

    to = edge.get("to")
    if isinstance(to, str):
        # Scalar `to: X` is sugar for a single default target — normalize to
        # the list form before extending it.
        seq = CommentedSeq()
        seq.append(CommentedMap([("default", to)]))
        edge["to"] = seq
        to = seq
    if not isinstance(to, list):
        raise OpError(f"edge from '{op.from_node}' has a malformed 'to'")
    for item in to:
        if _target_of(item) == op.target and _condition_of(item) == op.condition:
            raise OpError(f"edge {op.from_node} -> {op.target} already exists")
    to.append(_new_target(op.target, op.condition))


def _remove_edge(document: CommentedMap, op: RemoveEdgeOp) -> None:
    container = _graph_container(document, op.graph)
    edges = container.get("edges")
    if not isinstance(edges, list):
        raise OpError(f"no edges defined in '{op.graph}'")
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict) or edge.get("from") != op.from_node:
            continue
        to = edge.get("to")
        if isinstance(to, str):
            if to == op.target and op.condition is None:
                del edges[i]
                return
        elif isinstance(to, list):
            for j, item in enumerate(to):
                if _target_of(item) == op.target and _condition_of(item) == op.condition:
                    del to[j]
                    if not to:
                        del edges[i]
                    return
    rendered = f" (condition: {op.condition})" if op.condition else ""
    raise OpError(f"edge {op.from_node} -> {op.target}{rendered} not found")


def _retarget_edge(document: CommentedMap, op: RetargetEdgeOp) -> None:
    container = _graph_container(document, op.graph)
    edges = container.get("edges")
    if not isinstance(edges, list):
        raise OpError(f"no edges defined in '{op.graph}'")
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") != op.from_node:
            continue
        to = edge.get("to")
        if isinstance(to, str):
            if to == op.target and op.condition is None:
                edge["to"] = op.new_target
                return
        elif isinstance(to, list):
            for item in to:
                if _target_of(item) != op.target or _condition_of(item) != op.condition:
                    continue
                for other in to:
                    if other is item:
                        continue
                    if (
                        _target_of(other) == op.new_target
                        and _condition_of(other) == op.condition
                    ):
                        raise OpError(
                            f"edge {op.from_node} -> {op.new_target} already exists"
                        )
                if isinstance(item, dict) and "target" in item:
                    item["target"] = op.new_target
                else:  # `- default: X` shorthand
                    item["default"] = op.new_target
                return
    rendered = f" (condition: {op.condition})" if op.condition else ""
    raise OpError(f"edge {op.from_node} -> {op.target}{rendered} not found")


# -- generic field op ----------------------------------------------------------


def _step_into(current: Any, key: str, indexes: list[str], path: str) -> Any:
    if not isinstance(current, dict):
        raise OpError(f"cannot access '{key}' in '{path}': parent is not a mapping")
    if key not in current:
        if indexes:
            raise OpError(f"'{key}' in '{path}' does not exist")
        current[key] = CommentedMap()
    current = current[key]
    for index_str in indexes:
        index = int(index_str)
        if not isinstance(current, list) or index >= len(current):
            raise OpError(f"index [{index}] in '{path}' is out of range")
        current = current[index]
    return current


def _set_field(document: CommentedMap, op: SetFieldOp) -> None:
    segments = op.path.split(".")
    parsed: list[tuple[str, list[str]]] = []
    for raw in segments:
        match = _SEGMENT.match(raw)
        if match is None:
            raise OpError(f"invalid path segment '{raw}' in '{op.path}'")
        parsed.append((match.group(1), _INDEX.findall(match.group(2))))

    current: Any = document
    for key, indexes in parsed[:-1]:
        current = _step_into(current, key, indexes, op.path)

    last_key, last_indexes = parsed[-1]
    if not last_indexes:
        if not isinstance(current, dict):
            raise OpError(f"cannot set '{last_key}' in '{op.path}': parent is not a mapping")
        current[last_key] = op.value
        return
    # Final segment indexes into a list: walk to the list, assign the element.
    target_list = _step_into(current, last_key, last_indexes[:-1], op.path)
    index = int(last_indexes[-1])
    if not isinstance(target_list, list) or index >= len(target_list):
        raise OpError(f"index [{index}] in '{op.path}' is out of range")
    target_list[index] = op.value


def _unset_field(document: CommentedMap, op: UnsetFieldOp) -> None:
    segments = op.path.split(".")
    parsed: list[tuple[str, list[str]]] = []
    for raw in segments:
        match = _SEGMENT.match(raw)
        if match is None:
            raise OpError(f"invalid path segment '{raw}' in '{op.path}'")
        parsed.append((match.group(1), _INDEX.findall(match.group(2))))
    last_key, last_indexes = parsed[-1]
    if last_indexes:
        raise OpError("unset_field cannot target a list element (use remove_edge/remove_node)")

    current: Any = document
    for key, indexes in parsed[:-1]:
        if not isinstance(current, dict) or key not in current:
            return  # nothing to unset
        current = current[key]
        for index_str in indexes:
            index = int(index_str)
            if not isinstance(current, list) or index >= len(current):
                return
            current = current[index]
    if isinstance(current, dict) and last_key in current:
        del current[last_key]
