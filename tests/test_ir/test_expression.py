"""Tests for condition expression parser."""

import pytest
from agent_blueprint.ir.expression import analyze_expression, parse_expression
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

    def test_non_state_name_rejected(self):
        with pytest.raises(ExpressionError, match="Unsupported expression name"):
            parse_expression("route == 'billing'")


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

    def test_nested_boolean_rendering_preserves_grouping(self):
        expr = parse_expression(
            "(state.route == 'billing' or state.route == 'sales') and state.priority == 'high'"
        )
        python_code = expr.to_dict_access("state")

        assert python_code.startswith("(")
        result = eval(python_code, {}, {"state": {"route": "sales", "priority": "low"}})
        assert result is False


class TestAnalyzeExpression:
    def test_compound_or_and_overlap(self):
        left = analyze_expression(
            "state.route == 'billing' or "
            "(state.route == 'support' and state.priority != 'low')"
        )
        right = analyze_expression("state.route in ['support', 'sales']")

        assert left.fully_analyzable is True
        assert left.referenced_fields == {"route", "priority"}
        assert left.overlaps(right) is True

    def test_compound_conditions_can_be_disjoint(self):
        left = analyze_expression("state.route == 'billing' and state.priority == 'high'")
        right = analyze_expression("state.route == 'billing' and state.priority == 'low'")

        assert left.fully_analyzable is True
        assert left.overlaps(right) is False

    def test_range_comparison_is_valid_but_partially_analyzable(self):
        analysis = analyze_expression("state.score >= 0.8")

        assert analysis.fully_analyzable is False
        assert analysis.referenced_fields == {"score"}
        assert "partially analyzable" in (analysis.reason or "")
