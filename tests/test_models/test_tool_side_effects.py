"""Tool side-effect metadata: validation and approval implication."""

import pytest
from pydantic import ValidationError

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.tools import SideEffect, ToolDef


def _tool(**extra) -> ToolDef:
    return ToolDef.model_validate({"type": "function", "description": "t", **extra})


def _generate_tools_py(tool: dict) -> str:
    spec = BlueprintSpec.model_validate({
        "blueprint": {"name": "se-test"},
        "tools": {"mutate": {"type": "function", "description": "mutates", **tool}},
        "agents": {"assistant": {"model": "gpt-4o", "tools": ["mutate"]}},
        "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
    })
    return LangGraphGenerator().generate(compile_blueprint(spec))["tools.py"]


class TestValidation:
    def test_metadata_is_optional(self):
        tool = _tool()
        assert tool.side_effect is None and tool.idempotent is None
        assert tool.effective_requires_approval is False

    @pytest.mark.parametrize("value", ["none", "read", "write", "irreversible"])
    def test_all_side_effect_values_accepted(self, value):
        assert _tool(side_effect=value).side_effect == SideEffect(value)

    def test_unknown_side_effect_rejected(self):
        with pytest.raises(ValidationError):
            _tool(side_effect="destructive")

    @pytest.mark.parametrize("value", ["write", "irreversible"])
    def test_retrieval_tool_cannot_write(self, value):
        with pytest.raises(ValidationError, match="read-only"):
            ToolDef.model_validate({"type": "retrieval", "retriever": "kb", "side_effect": value})

    def test_waiver_requires_irreversible(self):
        with pytest.raises(ValidationError, match="only valid for side_effect: irreversible"):
            _tool(side_effect="write", approval_waived=True)

    def test_waiver_conflicts_with_requires_approval(self):
        with pytest.raises(ValidationError, match="conflicts"):
            _tool(side_effect="irreversible", approval_waived=True, requires_approval=True)


class TestEffectiveApproval:
    def test_irreversible_implies_approval(self):
        assert _tool(side_effect="irreversible").effective_requires_approval is True

    def test_waiver_removes_implied_approval(self):
        assert _tool(side_effect="irreversible", approval_waived=True).effective_requires_approval is False

    @pytest.mark.parametrize("value", ["none", "read", "write"])
    def test_other_side_effects_do_not_imply_approval(self, value):
        assert _tool(side_effect=value).effective_requires_approval is False

    def test_explicit_flag_still_wins(self):
        assert _tool(side_effect="read", requires_approval=True).effective_requires_approval is True


class TestGeneration:
    def test_irreversible_tool_is_approval_gated(self):
        assert "per_tool_requires_approval=True" in _generate_tools_py({"side_effect": "irreversible"})

    def test_waived_irreversible_tool_is_not_gated(self):
        tools_py = _generate_tools_py({"side_effect": "irreversible", "approval_waived": True})
        assert "per_tool_requires_approval=False" in tools_py
        assert "per_tool_requires_approval=True" not in tools_py

    def test_write_tool_is_not_gated(self):
        assert "per_tool_requires_approval=True" not in _generate_tools_py({"side_effect": "write"})
