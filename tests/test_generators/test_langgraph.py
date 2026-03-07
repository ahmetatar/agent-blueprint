"""Tests for the LangGraph code generator."""

import ast
from pathlib import Path

import pytest

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_ir(name: str):
    raw = load_blueprint_yaml(FIXTURES / name)
    spec = BlueprintSpec.model_validate(raw)
    return compile_blueprint(spec)


class TestLangGraphGenerator:
    def setup_method(self):
        self.gen = LangGraphGenerator()

    def test_generates_expected_files(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        assert "main.py" in files
        assert "graph.py" in files
        assert "nodes.py" in files
        assert "state.py" in files
        assert "tools.py" in files
        assert "requirements.txt" in files
        assert ".env.example" in files

    def test_state_py_is_valid_python(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        # Should parse without syntax errors
        ast.parse(files["state.py"])

    def test_nodes_py_is_valid_python(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        ast.parse(files["nodes.py"])

    def test_graph_py_is_valid_python(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        ast.parse(files["graph.py"])

    def test_main_py_is_valid_python(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        ast.parse(files["main.py"])

    def test_conditional_graph_is_valid_python(self):
        ir = load_ir("customer_support.yml")
        files = self.gen.generate(ir)
        ast.parse(files["graph.py"])

    def test_env_example_includes_openai_key(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        assert "OPENAI_API_KEY" in files[".env.example"]

    def test_env_example_includes_api_tool_secrets(self):
        ir = load_ir("customer_support.yml")
        files = self.gen.generate(ir)
        assert "BILLING_API_KEY" in files[".env.example"]

    def test_blueprint_name_in_main(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        assert "basic-chatbot" in files["main.py"]

    def test_impl_tool_generates_import(self):
        ir = load_ir("impl_tools.yml")
        files = self.gen.generate(ir)
        tools_py = files["tools.py"]
        assert "from myapp.classifiers import classify_intent as _classify_intent_impl" in tools_py
        assert "from myapp.tools.search import web_search as _web_search_impl" in tools_py

    def test_impl_tool_generates_wire_call(self):
        ir = load_ir("impl_tools.yml")
        files = self.gen.generate(ir)
        tools_py = files["tools.py"]
        assert 'classify_intent = tool(_classify_intent_impl' in tools_py
        assert 'web_search = tool(_web_search_impl' in tools_py

    def test_no_impl_tool_generates_stub(self):
        ir = load_ir("impl_tools.yml")
        files = self.gen.generate(ir)
        tools_py = files["tools.py"]
        assert "raise NotImplementedError" in tools_py
        assert "def send_email(" in tools_py

    def test_impl_tools_py_is_valid_python(self):
        ir = load_ir("impl_tools.yml")
        files = self.gen.generate(ir)
        ast.parse(files["tools.py"])

    def test_impl_field_only_valid_for_function_type(self):
        from pydantic import ValidationError
        from agent_blueprint.models.blueprint import BlueprintSpec
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "tools": {
                    "bad": {
                        "type": "api",
                        "url": "https://example.com",
                        "impl": "myapp.tools.bad",  # impl on non-function tool
                    }
                },
                "graph": {"entry_point": "n", "nodes": {"n": {}}, "edges": []},
            })
