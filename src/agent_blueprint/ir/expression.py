"""Condition expression parser and code compiler.

Parses simple Python-like condition strings (e.g. "state.route == 'billing'")
and compiles them to Python code suitable for each target framework.
"""

import ast
from dataclasses import dataclass
from typing import Any

from agent_blueprint.exceptions import ExpressionError

# YAML/JS literals that are valid Python identifiers but map to Python builtins
_YAML_TO_PYTHON = {"null": "None", "true": "True", "false": "False"}

# Allowed AST node types for safety (no function calls, assignments, etc.)
_ALLOWED_NODES = {
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.In,
    ast.NotIn,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Attribute,
    ast.Name,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Load,
}

_ALLOWED_NAMES = {"state", "null", "true", "false", "True", "False", "None"}


@dataclass
class CompiledExpression:
    """A parsed and validated condition expression."""
    source: str
    ast_node: ast.Expression

    def to_python(self, state_var: str = "state") -> str:
        """Render the expression as Python code with a given state variable name."""
        return _render_node(self.ast_node.body, state_var)

    def to_dict_access(self, state_var: str = "state") -> str:
        """Render the expression using dict access (state['key'] instead of state.key)."""
        return _render_node_dict(self.ast_node.body, state_var)


@dataclass(frozen=True)
class FieldConstraint:
    """Finite-value constraints that can be statically compared for route overlap."""

    included: frozenset[Any] | None = None
    excluded: frozenset[Any] = frozenset()

    def intersect(self, other: "FieldConstraint") -> "FieldConstraint | None":
        if self.included is None:
            included = other.included
        elif other.included is None:
            included = self.included
        else:
            included = self.included & other.included

        excluded = self.excluded | other.excluded
        if included is not None:
            included = frozenset(value for value in included if value not in excluded)
            if not included:
                return None
        return FieldConstraint(included=included, excluded=frozenset(excluded))

    def overlaps(self, other: "FieldConstraint") -> bool:
        return self.intersect(other) is not None


@dataclass
class ConditionDisjunct:
    """One AND-clause in a normalized OR-of-ANDs condition."""

    constraints: dict[str, FieldConstraint]

    def overlaps(self, other: "ConditionDisjunct") -> bool:
        for field in set(self.constraints) & set(other.constraints):
            if not self.constraints[field].overlaps(other.constraints[field]):
                return False
        return True


@dataclass
class ConditionAnalysis:
    """Static condition analysis shared by linting and target generators."""

    source: str
    referenced_fields: set[str]
    disjuncts: list[ConditionDisjunct]
    fully_analyzable: bool
    reason: str | None = None

    def overlaps(self, other: "ConditionAnalysis") -> bool:
        if not self.fully_analyzable or not other.fully_analyzable:
            return False
        return any(left.overlaps(right) for left in self.disjuncts for right in other.disjuncts)


def parse_expression(expr: str) -> CompiledExpression:
    """Parse and validate a condition expression string."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"Invalid expression syntax: {expr!r}") from e

    # Safety check: only allow a restricted set of AST node types
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES:
            raise ExpressionError(
                f"Unsafe expression: '{type(node).__name__}' is not allowed in: {expr!r}"
            )
        if isinstance(node, ast.Name) and node.id not in _ALLOWED_NAMES:
            raise ExpressionError(
                f"Unsupported expression name: '{node.id}' in {expr!r}; use state.<field> references"
            )
        if isinstance(node, ast.Attribute) and not _is_state_attribute(node):
            raise ExpressionError(
                f"Unsupported attribute reference in {expr!r}; use state.<field> references"
            )

    return CompiledExpression(source=expr, ast_node=tree)


def analyze_expression(expr: str | CompiledExpression) -> ConditionAnalysis:
    """Return normalized condition semantics for static linting and portability checks."""
    compiled = parse_expression(expr) if isinstance(expr, str) else expr
    referenced_fields = _referenced_state_fields(compiled.ast_node.body)
    disjuncts, fully_analyzable, reason = _analyze_node(compiled.ast_node.body)
    return ConditionAnalysis(
        source=compiled.source,
        referenced_fields=referenced_fields,
        disjuncts=[ConditionDisjunct(item) for item in disjuncts],
        fully_analyzable=fully_analyzable,
        reason=reason,
    )


def _is_state_attribute(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "state"


def _referenced_state_fields(node: ast.AST) -> set[str]:
    fields: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and _is_state_attribute(child):
            fields.add(child.attr)
    return fields


def _analyze_node(
    node: ast.expr,
) -> tuple[list[dict[str, FieldConstraint]], bool, str | None]:
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return _analyze_and(node.values)
        if isinstance(node.op, ast.Or):
            return _analyze_or(node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inverted = _invert_simple_compare(node.operand)
        if inverted is None:
            return [{}], False, "`not` is only statically analyzable for simple comparisons"
        return _constraint_from_compare(inverted)
    if isinstance(node, ast.Compare):
        return _constraint_from_compare(node)
    return [{}], False, f"{type(node).__name__} conditions are valid but not statically analyzable"


def _analyze_and(
    values: list[ast.expr],
) -> tuple[list[dict[str, FieldConstraint]], bool, str | None]:
    disjuncts: list[dict[str, FieldConstraint]] = [{}]
    fully_analyzable = True
    reason: str | None = None
    for value in values:
        next_disjuncts, value_analyzable, value_reason = _analyze_node(value)
        fully_analyzable = fully_analyzable and value_analyzable
        reason = reason or value_reason
        merged: list[dict[str, FieldConstraint]] = []
        for left in disjuncts:
            for right in next_disjuncts:
                combined = _merge_disjuncts(left, right)
                if combined is not None:
                    merged.append(combined)
        disjuncts = merged
    return disjuncts, fully_analyzable, reason


def _analyze_or(
    values: list[ast.expr],
) -> tuple[list[dict[str, FieldConstraint]], bool, str | None]:
    disjuncts: list[dict[str, FieldConstraint]] = []
    fully_analyzable = True
    reason: str | None = None
    for value in values:
        value_disjuncts, value_analyzable, value_reason = _analyze_node(value)
        disjuncts.extend(value_disjuncts)
        fully_analyzable = fully_analyzable and value_analyzable
        reason = reason or value_reason
    return disjuncts, fully_analyzable, reason


def _merge_disjuncts(
    left: dict[str, FieldConstraint],
    right: dict[str, FieldConstraint],
) -> dict[str, FieldConstraint] | None:
    merged = dict(left)
    for field, constraint in right.items():
        if field not in merged:
            merged[field] = constraint
            continue
        intersected = merged[field].intersect(constraint)
        if intersected is None:
            return None
        merged[field] = intersected
    return merged


def _constraint_from_compare(
    node: ast.Compare,
) -> tuple[list[dict[str, FieldConstraint]], bool, str | None]:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        return [{}], False, "chained comparisons are valid but not statically analyzable"
    if not (isinstance(node.left, ast.Attribute) and _is_state_attribute(node.left)):
        return [{}], False, "only state.<field> comparisons are statically analyzable"

    field_name = node.left.attr
    op = node.ops[0]
    comparator = node.comparators[0]
    values = _literal_values(comparator)
    if values is None:
        return [{}], False, "comparisons must use literal values for static analysis"

    if isinstance(op, (ast.Eq, ast.In)):
        return [{field_name: FieldConstraint(included=frozenset(values))}], True, None
    if isinstance(op, (ast.NotEq, ast.NotIn)):
        return [{field_name: FieldConstraint(excluded=frozenset(values))}], True, None
    return [{}], False, "range comparisons are portable but only partially analyzable"


def _invert_simple_compare(node: ast.expr) -> ast.Compare | None:
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    inverse_op: ast.cmpop
    op = node.ops[0]
    if isinstance(op, ast.Eq):
        inverse_op = ast.NotEq()
    elif isinstance(op, ast.NotEq):
        inverse_op = ast.Eq()
    elif isinstance(op, ast.In):
        inverse_op = ast.NotIn()
    elif isinstance(op, ast.NotIn):
        inverse_op = ast.In()
    else:
        return None
    return ast.Compare(left=node.left, ops=[inverse_op], comparators=node.comparators)


def _literal_values(node: ast.expr) -> set[Any] | None:
    if isinstance(node, ast.Constant):
        return {node.value}
    if isinstance(node, ast.Name) and node.id in _YAML_TO_PYTHON:
        return {{"None": None, "True": True, "False": False}[_YAML_TO_PYTHON[node.id]]}
    if isinstance(node, (ast.List, ast.Tuple)):
        values: set[Any] = set()
        for item in node.elts:
            item_values = _literal_values(item)
            if item_values is None or len(item_values) != 1:
                return None
            values.update(item_values)
        return values
    return None


def _render_node(node: ast.expr, state_var: str) -> str:
    """Recursively render an AST node as Python source."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Name):
        return _YAML_TO_PYTHON.get(node.id, node.id)
    elif isinstance(node, ast.Attribute):
        # state.foo → state_var["foo"] or state_var.foo
        base = _render_node(node.value, state_var)
        if isinstance(node.value, ast.Name) and node.value.id == "state":
            return f'{state_var}.{node.attr}'
        return f'{base}.{node.attr}'
    elif isinstance(node, ast.Compare):
        left = _render_node(node.left, state_var)
        parts = [left]
        for op, comparator in zip(node.ops, node.comparators):
            parts.append(_op_to_str(op))
            parts.append(_render_node(comparator, state_var))
        return " ".join(parts)
    elif isinstance(node, ast.BoolOp):
        op_str = " and " if isinstance(node.op, ast.And) else " or "
        return f"({op_str.join(_render_node(v, state_var) for v in node.values)})"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"not ({_render_node(node.operand, state_var)})"
    elif isinstance(node, (ast.List, ast.Tuple)):
        items = ", ".join(_render_node(e, state_var) for e in node.elts)
        return f"[{items}]"
    else:
        raise ExpressionError(f"Cannot render AST node: {type(node).__name__}")


def _render_node_dict(node: ast.expr, state_var: str) -> str:
    """Like _render_node but uses dict access (state["key"]) for state attributes."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Name):
        return _YAML_TO_PYTHON.get(node.id, node.id)
    elif isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == "state":
            return f'{state_var}.get("{node.attr}")'
        base = _render_node_dict(node.value, state_var)
        return f'{base}.{node.attr}'
    elif isinstance(node, ast.Compare):
        left = _render_node_dict(node.left, state_var)
        parts = [left]
        for op, comparator in zip(node.ops, node.comparators):
            parts.append(_op_to_str(op))
            parts.append(_render_node_dict(comparator, state_var))
        return " ".join(parts)
    elif isinstance(node, ast.BoolOp):
        op_str = " and " if isinstance(node.op, ast.And) else " or "
        return f"({op_str.join(_render_node_dict(v, state_var) for v in node.values)})"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"not ({_render_node_dict(node.operand, state_var)})"
    elif isinstance(node, (ast.List, ast.Tuple)):
        items = ", ".join(_render_node_dict(e, state_var) for e in node.elts)
        return f"[{items}]"
    else:
        raise ExpressionError(f"Cannot render AST node: {type(node).__name__}")


def _op_to_str(op: ast.cmpop) -> str:
    mapping = {
        ast.Eq: "==",
        ast.NotEq: "!=",
        ast.In: "in",
        ast.NotIn: "not in",
        ast.Lt: "<",
        ast.LtE: "<=",
        ast.Gt: ">",
        ast.GtE: ">=",
    }
    return mapping.get(type(op), "==")
