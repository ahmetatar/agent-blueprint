"""Static lint checks for agent blueprints."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.ir.expression import analyze_expression
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.graph import GraphDef
from agent_blueprint.utils.yaml_loader import dump_blueprint_document, load_blueprint_document

_STATE_REF_RE = re.compile(r"\bstate\.([A-Za-z_][A-Za-z0-9_]*)")


class LintSeverity(str, Enum):
    error = "error"
    warning = "warning"


@dataclass(frozen=True)
class LintFinding:
    severity: LintSeverity
    code: str
    location: str
    message: str
    autofixable: bool = False


def lint_blueprint(spec: BlueprintSpec, ir: AgentGraph) -> list[LintFinding]:
    findings: list[LintFinding] = []
    findings.extend(_lint_unreachable_nodes(spec))
    findings.extend(_lint_missing_default_routes(spec))
    findings.extend(_lint_condition_analyzability(spec))
    findings.extend(_lint_condition_overlap_ambiguity(spec))
    findings.extend(_lint_dead_state_fields(spec))
    findings.extend(_lint_contract_usage(spec))
    findings.extend(_lint_mutation_patterns(spec))
    findings.extend(_lint_parallel_branch_conflicts(spec))
    findings.extend(_lint_unbounded_loops(spec))
    return findings


def _lint_unbounded_loops(spec: BlueprintSpec) -> list[LintFinding]:
    """Flag cycles with no route out: once entered, the run can never reach
    END and will always exhaust settings.max_graph_steps.

    Loops with a (conditional) exit are a legitimate agent pattern
    (reflection, revision) and are not flagged.
    """
    findings = _unbounded_loops_for_graph(spec.graph, location_prefix="graph")
    for name, subgraph in sorted(spec.subgraphs.items()):
        findings.extend(
            _unbounded_loops_for_graph(subgraph, location_prefix=f"subgraphs.{name}")
        )
    return findings


def _unbounded_loops_for_graph(graph: GraphDef, *, location_prefix: str) -> list[LintFinding]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in graph.nodes}
    exits_to_end: set[str] = set()

    for node_id, node in graph.nodes.items():
        if node.type.value == "parallel":
            adjacency[node_id].update(b for b in node.branches if b in adjacency)
            if node.join:
                for branch in node.branches:
                    adjacency.setdefault(branch, set()).add(node.join)
        if node.type.value == "supervisor":
            adjacency[node_id].update(w for w in node.workers if w in adjacency)
            for worker in node.workers:
                adjacency.setdefault(worker, set()).add(node_id)
            if node.on_finish and node.on_finish != "END":
                if node.on_finish in adjacency:
                    adjacency[node_id].add(node.on_finish)
            else:
                exits_to_end.add(node_id)  # supervisor finishes to END
    for edge in graph.edges:
        if edge.from_node not in adjacency:
            continue
        for target in edge.get_targets():
            if target.target == "END":
                exits_to_end.add(edge.from_node)
            elif target.target in adjacency:
                adjacency[edge.from_node].add(target.target)

    findings: list[LintFinding] = []
    for component in _strongly_connected_components(adjacency):
        is_cycle = len(component) > 1 or any(n in adjacency[n] for n in component)
        if not is_cycle:
            continue
        has_exit = any(n in exits_to_end for n in component) or any(
            target not in component
            for n in component
            for target in adjacency[n]
        )
        if has_exit:
            continue
        ordered = sorted(component)
        rendered = " -> ".join(ordered)
        findings.append(LintFinding(
            severity=LintSeverity.error,
            code="unbounded-loop",
            location=f"{location_prefix}.nodes.{ordered[0]}",
            message=(
                f"Node(s) {rendered} form a loop with no route to END or to any node "
                "outside the loop; once entered, the run can never terminate and will "
                "always exhaust settings.max_graph_steps. Add a conditional exit edge."
            ),
            autofixable=False,
        ))
    return findings


def _strongly_connected_components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    """Kosaraju's algorithm (iterative) — small graphs, clarity over speed."""
    order: list[str] = []
    visited: set[str] = set()
    for start in adjacency:
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(sorted(adjacency[start])))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                stack.pop()
            elif child not in visited:
                visited.add(child)
                stack.append((child, iter(sorted(adjacency[child]))))

    reverse: dict[str, set[str]] = {node: set() for node in adjacency}
    for node, targets in adjacency.items():
        for target in targets:
            reverse[target].add(node)

    components: list[set[str]] = []
    assigned: set[str] = set()
    for node in reversed(order):
        if node in assigned:
            continue
        component = {node}
        assigned.add(node)
        pending = [node]
        while pending:
            current = pending.pop()
            for parent in reverse[current]:
                if parent not in assigned:
                    assigned.add(parent)
                    component.add(parent)
                    pending.append(parent)
        components.append(component)
    return components


def _lint_parallel_branch_conflicts(spec: BlueprintSpec) -> list[LintFinding]:
    """Branches of a parallel node that produce the same state field need an
    append/merge reducer — with `replace` the concurrent updates collide and
    LangGraph raises at runtime."""
    findings: list[LintFinding] = []
    if not spec.contracts:
        return findings

    for node_id, node in sorted(spec.graph.nodes.items()):
        if node.type.value != "parallel":
            continue
        produced_by: dict[str, list[str]] = {}
        for branch in node.branches:
            contract = spec.contracts.nodes.get(branch)
            if contract is None:
                continue
            for field_name in contract.produces:
                produced_by.setdefault(field_name, []).append(branch)
        for field_name, branches in sorted(produced_by.items()):
            if len(branches) < 2:
                continue
            field_def = spec.state.fields.get(field_name)
            reducer = field_def.reducer.value if field_def else "replace"
            if reducer == "replace":
                rendered = ", ".join(f"'{b}'" for b in sorted(branches))
                findings.append(LintFinding(
                    severity=LintSeverity.error,
                    code="parallel-branch-conflict",
                    location=f"graph.nodes.{node_id}",
                    message=(
                        f"Parallel node '{node_id}' branches {rendered} all produce state "
                        f"field '{field_name}', which uses the 'replace' reducer — concurrent "
                        "updates will collide at runtime. Use an 'append' or 'merge' reducer, "
                        "or write to distinct fields."
                    ),
                    autofixable=False,
                ))
    return findings


def _lint_unreachable_nodes(spec: BlueprintSpec) -> list[LintFinding]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in spec.graph.nodes}
    for node_id, node in spec.graph.nodes.items():
        if node.type.value == "parallel":
            adjacency.setdefault(node_id, set()).update(node.branches)
            if node.join:
                for branch in node.branches:
                    adjacency.setdefault(branch, set()).add(node.join)
        if node.type.value == "supervisor":
            adjacency.setdefault(node_id, set()).update(node.workers)
            for worker in node.workers:
                adjacency.setdefault(worker, set()).add(node_id)
            if node.on_finish and node.on_finish != "END":
                adjacency[node_id].add(node.on_finish)
    for edge in spec.graph.edges:
        for target in edge.get_targets():
            if target.target in spec.graph.nodes:
                adjacency.setdefault(edge.from_node, set()).add(target.target)

    # Low-confidence escalation is a dynamic reroute: the generator injects the
    # escalation target into the router of every node that has an outgoing edge
    # (see templates/langgraph/graph.py.j2), so it is reachable from each such
    # node even without an explicit edge. Mirror that here so a node reachable
    # only via `policies.escalation.on_low_confidence` is not falsely flagged.
    escalation_target = (
        spec.policies.escalation.on_low_confidence if spec.policies else None
    )
    if escalation_target and escalation_target in spec.graph.nodes:
        for edge in spec.graph.edges:
            adjacency.setdefault(edge.from_node, set()).add(escalation_target)

    visited: set[str] = set()
    stack = [spec.graph.entry_point]
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        stack.extend(sorted(adjacency.get(node_id, set()) - visited))

    findings: list[LintFinding] = []
    for node_id in sorted(spec.graph.nodes):
        if node_id not in visited:
            findings.append(LintFinding(
                severity=LintSeverity.error,
                code="unreachable-node",
                location=f"graph.nodes.{node_id}",
                message=f"Node '{node_id}' is unreachable from entry_point '{spec.graph.entry_point}'",
                autofixable=False,
            ))
    return findings


def _lint_missing_default_routes(spec: BlueprintSpec) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for index, edge in enumerate(spec.graph.edges):
        targets = edge.get_targets()
        has_conditional = any(target.condition is not None for target in targets)
        has_default = any(target.default or target.condition is None for target in targets)
        if has_conditional and not has_default:
            findings.append(LintFinding(
                severity=LintSeverity.error,
                code="missing-default-route",
                location=f"graph.edges[{index}]",
                message=(
                    f"Conditional edge from '{edge.from_node}' has no default route; "
                    "add `default: END` or an unconditional target"
                ),
                autofixable=True,
            ))
    return findings


def _lint_dead_state_fields(spec: BlueprintSpec) -> list[LintFinding]:
    used_fields: set[str] = set()

    for edge in spec.graph.edges:
        for target in edge.get_targets():
            if target.condition:
                used_fields.update(_STATE_REF_RE.findall(target.condition))

    if spec.contracts:
        used_fields.update(spec.contracts.state.required_fields)
        used_fields.update(spec.contracts.state.immutable_fields)
        for invariant in spec.contracts.state.invariants:
            used_fields.update(_STATE_REF_RE.findall(invariant))
        for contract in spec.contracts.nodes.values():
            used_fields.update(contract.requires)
            used_fields.update(contract.produces)
            used_fields.update(contract.forbids_mutation)

    if spec.output is not None:
        used_fields.update(spec.output.schema_def.keys())

    if spec.input is not None:
        used_fields.update(spec.input.schema_def.keys())

    if any(node.agent for node in spec.graph.nodes.values()):
        used_fields.add("messages")

    findings: list[LintFinding] = []
    for field_name in sorted(spec.state.fields):
        if field_name not in used_fields:
            findings.append(LintFinding(
                severity=LintSeverity.warning,
                code="dead-state-field",
                location=f"state.fields.{field_name}",
                message=f"State field '{field_name}' is declared but not referenced by routes, contracts, or outputs",
                autofixable=False,
            ))
    return findings


def _lint_condition_overlap_ambiguity(spec: BlueprintSpec) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for edge_index, edge in enumerate(spec.graph.edges):
        conditional_targets = [
            (target_index, target, analyze_expression(target.condition or ""))
            for target_index, target in enumerate(edge.get_targets())
            if target.condition
        ]
        for left_index, (target_index, target, analysis) in enumerate(conditional_targets):
            if not analysis.fully_analyzable:
                continue
            for _other_target_index, other_target, other_analysis in conditional_targets[
                left_index + 1:
            ]:
                if not other_analysis.fully_analyzable:
                    continue
                if analysis.overlaps(other_analysis):
                    findings.append(LintFinding(
                        severity=LintSeverity.warning,
                        code="condition-overlap-ambiguity",
                        location=f"graph.edges[{edge_index}].to[{target_index}]",
                        message=(
                            f"Conditional targets '{target.target}' and '{other_target.target}' from "
                            f"'{edge.from_node}' can both match the same state values; route order will decide"
                        ),
                        autofixable=False,
                    ))
    return findings


def _lint_condition_analyzability(spec: BlueprintSpec) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for edge_index, edge in enumerate(spec.graph.edges):
        for target_index, target in enumerate(edge.get_targets()):
            if not target.condition:
                continue
            analysis = analyze_expression(target.condition)
            if analysis.fully_analyzable:
                continue
            findings.append(LintFinding(
                severity=LintSeverity.warning,
                code="condition-partially-analyzable",
                location=f"graph.edges[{edge_index}].to[{target_index}]",
                message=(
                    f"Condition for target '{target.target}' is valid and portable, "
                    f"but lint can only partially analyze it: {analysis.reason}"
                ),
                autofixable=False,
            ))
    return findings


def _lint_contract_usage(spec: BlueprintSpec) -> list[LintFinding]:
    if spec.contracts is None:
        return []

    findings: list[LintFinding] = []
    consumed_output_contracts = {
        contract.output_contract
        for contract in spec.contracts.nodes.values()
        if contract.output_contract
    }
    if spec.harness:
        consumed_output_contracts.update(
            scenario.expected.output_contract
            for scenario in spec.harness.scenarios
            if scenario.expected.output_contract
        )

    for contract_name in sorted(spec.contracts.outputs):
        if contract_name not in consumed_output_contracts:
            findings.append(LintFinding(
                severity=LintSeverity.warning,
                code="unused-output-contract",
                location=f"contracts.outputs.{contract_name}",
                message=f"Output contract '{contract_name}' is declared but never referenced",
                autofixable=True,
            ))

    consumed_state_fields: set[str] = set()
    for edge in spec.graph.edges:
        for target in edge.get_targets():
            if target.condition:
                consumed_state_fields.update(_STATE_REF_RE.findall(target.condition))
    if spec.output is not None:
        consumed_state_fields.update(spec.output.schema_def.keys())
    for contract in spec.contracts.nodes.values():
        consumed_state_fields.update(contract.requires)

    for node_id, contract in spec.contracts.nodes.items():
        for field_name in contract.produces:
            if field_name not in consumed_state_fields:
                findings.append(LintFinding(
                severity=LintSeverity.warning,
                code="unused-produced-field",
                location=f"contracts.nodes.{node_id}.produces",
                message=f"Node '{node_id}' produces state field '{field_name}' but no route, output, or node requirement consumes it",
                autofixable=False,
            ))

    return findings


def _lint_mutation_patterns(spec: BlueprintSpec) -> list[LintFinding]:
    if spec.contracts is None:
        return []

    findings: list[LintFinding] = []
    immutable_fields = set(spec.contracts.state.immutable_fields)
    for node_id, contract in spec.contracts.nodes.items():
        overlap = sorted(set(contract.produces) & set(contract.forbids_mutation))
        for field_name in overlap:
            findings.append(LintFinding(
                severity=LintSeverity.error,
                code="conflicting-node-contract",
                location=f"contracts.nodes.{node_id}",
                message=(
                    f"Node '{node_id}' both produces and forbids mutation of state field '{field_name}'"
                ),
                autofixable=False,
            ))

        immutable_overlap = sorted(set(contract.produces) & immutable_fields)
        for field_name in immutable_overlap:
            findings.append(LintFinding(
                severity=LintSeverity.error,
                code="immutable-produced-field",
                location=f"contracts.nodes.{node_id}.produces",
                message=(
                    f"Node '{node_id}' produces immutable state field '{field_name}' declared in contracts.state.immutable_fields"
                ),
                autofixable=False,
            ))

    return findings


def apply_auto_fixes(blueprint: Path, findings: list[LintFinding]) -> list[str]:
    """Apply safe auto-fixes to a blueprint file and return descriptions."""
    fixable = [finding for finding in findings if finding.autofixable]
    if not fixable:
        return []

    document = load_blueprint_document(blueprint)
    applied: list[str] = []

    for finding in fixable:
        if finding.code == "missing-default-route":
            if _apply_missing_default_route_fix(document, finding):
                applied.append(f"{finding.code} at {finding.location}")
        elif finding.code == "unused-output-contract":
            if _apply_unused_output_contract_fix(document, finding):
                applied.append(f"{finding.code} at {finding.location}")

    if applied:
        dump_blueprint_document(blueprint, document)
    return applied


def _apply_missing_default_route_fix(document: CommentedMap, finding: LintFinding) -> bool:
    match = re.fullmatch(r"graph\.edges\[(\d+)\]", finding.location)
    if match is None:
        return False
    edge_index = int(match.group(1))
    graph = document.get("graph")
    if not isinstance(graph, CommentedMap):
        return False
    edges = graph.get("edges")
    if not isinstance(edges, list) or edge_index >= len(edges):
        return False
    edge = edges[edge_index]
    if not isinstance(edge, CommentedMap):
        return False
    targets = edge.get("to")
    if not isinstance(targets, list):
        return False

    for target in targets:
        if isinstance(target, CommentedMap) and ("default" in target or "condition" not in target):
            return False

    targets.append(CommentedMap({"default": "END"}))
    return True


def _apply_unused_output_contract_fix(document: CommentedMap, finding: LintFinding) -> bool:
    prefix = "contracts.outputs."
    if not finding.location.startswith(prefix):
        return False
    contract_name = finding.location[len(prefix):]

    contracts = document.get("contracts")
    if not isinstance(contracts, CommentedMap):
        return False
    outputs = contracts.get("outputs")
    if not isinstance(outputs, CommentedMap) or contract_name not in outputs:
        return False

    del outputs[contract_name]
    if not outputs:
        del contracts["outputs"]
    if not contracts:
        del document["contracts"]
    return True
