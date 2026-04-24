"""Tests for IR compiler."""

from pathlib import Path

from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_spec(name: str) -> BlueprintSpec:
    raw = load_blueprint_yaml(FIXTURES / name)
    return BlueprintSpec.model_validate(raw)


class TestCompileBasicChatbot:
    def test_compiles_successfully(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.name == "basic-chatbot"

    def test_nodes_compiled(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert len(ir.nodes) == 1
        assert ir.nodes[0].id == "assistant"

    def test_entry_point(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.entry_point == "assistant"

    def test_agent_attached_to_node(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        node = ir.nodes[0]
        assert node.agent is not None
        assert node.agent.model == "gpt-4o"


class TestCompileCustomerSupport:
    def test_conditional_edges_compiled(self):
        spec = load_spec("customer_support.yml")
        ir = compile_blueprint(spec)
        router_edges = ir.get_edges_from("router")
        assert len(router_edges) == 1
        assert router_edges[0].is_conditional

    def test_condition_expressions_parsed(self):
        spec = load_spec("customer_support.yml")
        ir = compile_blueprint(spec)
        edge = ir.get_edges_from("router")[0]
        cond_targets = [t for t in edge.targets if t.condition is not None]
        assert len(cond_targets) == 2
        # Verify condition compiles to valid Python
        code = cond_targets[0].condition.to_dict_access("state")
        result = eval(code, {}, {"state": {"department": "billing"}})
        assert isinstance(result, bool)

    def test_tool_defs_attached(self):
        spec = load_spec("customer_support.yml")
        ir = compile_blueprint(spec)
        router_node = ir.get_node("router")
        assert router_node is not None
        assert "classify_intent" in router_node.tool_defs


class TestCompilerWarnings:
    def test_no_warnings_for_anthropic_reasoning(self):
        spec = load_spec("reasoning_agent.yml")
        ir = compile_blueprint(spec)
        assert ir.warnings == []

    def test_warning_for_reasoning_with_empty_params(self):
        spec = load_spec("reasoning_openai.yml")
        ir = compile_blueprint(spec)
        assert len(ir.warnings) == 1
        assert "thinker" in ir.warnings[0]
        assert "params" in ir.warnings[0]

    def test_warning_for_reasoning_without_explicit_provider_adapter(self):
        spec = load_spec("reasoning_openai.yml")
        spec.agents["thinker"].reasoning.params = {"reasoning": {"effort": "high"}}
        spec.agents["thinker"].model = "gpt-4o"
        ir = compile_blueprint(spec)
        assert len(ir.warnings) == 1
        assert "model_provider or provider/model prefix" in ir.warnings[0]

    def test_legacy_llm_kwargs_remains_supported(self):
        spec = load_spec("reasoning_agent.yml")
        spec.agents["thinker"].reasoning.llm_kwargs = spec.agents["thinker"].reasoning.params
        spec.agents["thinker"].reasoning.params = {}
        ir = compile_blueprint(spec)
        assert ir.warnings == []
        assert spec.agents["thinker"].reasoning.effective_params()["thinking"]["type"] == "enabled"

    def test_no_warnings_when_reasoning_not_set(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.warnings == []
