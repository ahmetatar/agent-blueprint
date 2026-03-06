"""Tests for BlueprintSpec validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> dict:
    return load_blueprint_yaml(FIXTURES / name)


class TestValidBlueprints:
    def test_basic_chatbot_loads(self):
        raw = load("basic_chatbot.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.blueprint.name == "basic-chatbot"
        assert "assistant" in spec.agents
        assert spec.graph.entry_point == "assistant"

    def test_variable_interpolation(self):
        raw = load("basic_chatbot.yml")
        spec = BlueprintSpec.model_validate(raw)
        # ${settings.default_model} should resolve to "gpt-4o"
        assert spec.agents["assistant"].model == "gpt-4o"

    def test_customer_support_loads(self):
        raw = load("customer_support.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.blueprint.name == "customer-support-agent"
        assert len(spec.agents) == 3
        assert len(spec.tools) == 2
        assert len(spec.graph.nodes) == 3

    def test_customer_support_edges(self):
        raw = load("customer_support.yml")
        spec = BlueprintSpec.model_validate(raw)
        # router should have conditional edges
        router_edges = [e for e in spec.graph.edges if e.from_node == "router"]
        assert len(router_edges) == 1
        targets = router_edges[0].get_targets()
        assert len(targets) == 3


class TestInvalidBlueprints:
    def test_missing_agent_reference(self):
        raw = load("invalid_missing_agent.yml")
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate(raw)
        assert "nonexistent_agent" in str(exc_info.value)

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                # missing 'graph' field
            })

    def test_invalid_entry_point(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "graph": {
                    "entry_point": "nonexistent",
                    "nodes": {"real_node": {"agent": None, "type": "function"}},
                    "edges": [],
                }
            })
