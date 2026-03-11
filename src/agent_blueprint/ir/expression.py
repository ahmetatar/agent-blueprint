"""Condition expression parser and code compiler.

Parses simple Python-like condition strings (e.g. "state.route == 'billing'")
and compiles them to Python code suitable for each target framework.
"""

import ast
from dataclasses import dataclass

from agent_blueprint.exceptions import ExpressionError

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

    return CompiledExpression(source=expr, ast_node=tree)


def _render_node(node: ast.expr, state_var: str) -> str:
    """Recursively render an AST node as Python source."""
    if isinstance(node, ast.Constant):
        return repr(node.value)
    elif isinstance(node, ast.Name):
        return node.id
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
        return op_str.join(_render_node(v, state_var) for v in node.values)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"not {_render_node(node.operand, state_var)}"
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
        return node.id
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
        return op_str.join(_render_node_dict(v, state_var) for v in node.values)
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return f"not {_render_node_dict(node.operand, state_var)}"
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
