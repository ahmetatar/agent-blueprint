"""Static lint checks for agent blueprints."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.models.blueprint import BlueprintSpec
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
    findings.extend(_lint_condition_overlap_ambiguity(spec))
    findings.extend(_lint_dead_state_fields(spec))
    findings.extend(_lint_contract_usage(spec))
    findings.extend(_lint_mutation_patterns(spec))
    return findings


def _lint_unreachable_nodes(spec: BlueprintSpec) -> list[LintFinding]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in spec.graph.nodes}
    for edge in spec.graph.edges:
        for target in edge.get_targets():
            if target.target in spec.graph.nodes:
                adjacency.setdefault(edge.from_node, set()).add(target.target)

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
            (target_index, target)
            for target_index, target in enumerate(edge.get_targets())
            if target.condition
        ]
        for left_index, (target_index, target) in enumerate(conditional_targets):
            left_constraint = _extract_simple_state_constraint(target.condition or "")
            if left_constraint is None:
                continue
            for other_target_index, other_target in conditional_targets[left_index + 1:]:
                right_constraint = _extract_simple_state_constraint(other_target.condition or "")
                if right_constraint is None:
                    continue
                if _constraints_overlap(left_constraint, right_constraint):
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


def _extract_simple_state_constraint(expr: str) -> tuple[str, set[object]] | None:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    body = tree.body
    if not isinstance(body, ast.Compare):
        return None
    if len(body.ops) != 1 or len(body.comparators) != 1:
        return None
    if not (
        isinstance(body.left, ast.Attribute)
        and isinstance(body.left.value, ast.Name)
        and body.left.value.id == "state"
    ):
        return None

    field_name = body.left.attr
    op = body.ops[0]
    comparator = body.comparators[0]

    if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
        return field_name, {comparator.value}

    if isinstance(op, ast.In) and isinstance(comparator, (ast.List, ast.Tuple)):
        values: set[object] = set()
        for item in comparator.elts:
            if not isinstance(item, ast.Constant):
                return None
            values.add(item.value)
        return field_name, values

    return None


def _constraints_overlap(
    left: tuple[str, set[object]],
    right: tuple[str, set[object]],
) -> bool:
    left_field, left_values = left
    right_field, right_values = right
    if left_field != right_field:
        return False
    return bool(left_values & right_values)


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
