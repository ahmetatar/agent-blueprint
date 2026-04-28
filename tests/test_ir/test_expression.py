"""Tests for condition expression parser."""

import pytest
from agent_blueprint.ir.expression import parse_expression
from agent_blueprint.exceptions import ExpressionError


class TestParseExpression:
    def test_simple_equality(self):
        expr = parse_expression("state.department == 'billing'")
        assert expr.source == "state.department == 'billing'"

    def test_to_python(self):
        expr = parse_expression("state.department == 'billing'")
        code = expr.to_python("state")
        assert "department" in code
        assert "billing" in code

    def test_to_dict_access(self):
        expr = parse_expression("state.department == 'billing'")
        code = expr.to_dict_access("state")
        assert 'state.get("department")' in code
        assert "'billing'" in code

    def test_and_expression(self):
        expr = parse_expression("state.resolved == True and state.department == 'billing'")
        code = expr.to_python("state")
        assert "and" in code

    def test_in_expression(self):
        expr = parse_expression("state.role in ['admin', 'superuser']")
        code = expr.to_python("state")
        assert "in" in code

    def test_invalid_syntax(self):
        with pytest.raises(ExpressionError, match="Invalid expression syntax"):
            parse_expression("state.foo ==")

    def test_unsafe_expression_rejected(self):
        with pytest.raises(ExpressionError, match="Unsafe expression"):
            parse_expression("__import__('os').system('rm -rf /')")

    def test_function_call_rejected(self):
        with pytest.raises(ExpressionError, match="Unsafe expression"):
            parse_expression("len(state.messages) > 5")


class TestCompileExpression:
    def test_compile_for_langgraph(self):
        expr = parse_expression("state.route == 'billing'")
        python_code = expr.to_dict_access("state")
        # state.get("route") == 'billing' should evaluate correctly
        assert python_code == "state.get(\"route\") == 'billing'"

    def test_compile_negative(self):
        expr = parse_expression("state.route == 'billing'")
        python_code = expr.to_dict_access("state")
        # Verify dict-based eval works
        result = eval(python_code, {}, {"state": {"route": "billing"}})
        assert result is True
        result = eval(python_code, {}, {"state": {"route": "technical"}})
        assert result is False
