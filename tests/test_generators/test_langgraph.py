"""Tests for the LangGraph code generator."""

import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types

import pytest
from pydantic import ValidationError

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.agents import ReasoningConfig
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_ir(name: str):
    raw = load_blueprint_yaml(FIXTURES / name)
    spec = BlueprintSpec.model_validate(raw)
    return compile_blueprint(spec)


def _write_trace_helper(tmp_path, files: dict[str, str]) -> None:
    (tmp_path / "_abp_trace.py").write_text(files["_abp_trace.py"], encoding="utf-8")


def _write_harness_helper(tmp_path, files: dict[str, str]) -> None:
    (tmp_path / "_abp_harness.py").write_text(files["_abp_harness.py"], encoding="utf-8")


def _load_generated_nodes_module(
    tmp_path,
    monkeypatch,
    *,
    spec_data: dict,
    llm_script: list[dict],
    tool_names: list[str] | None = None,
):
    gen = LangGraphGenerator()
    spec = BlueprintSpec.model_validate(spec_data)
    files = gen.generate(compile_blueprint(spec))

    _write_trace_helper(tmp_path, files)
    _write_harness_helper(tmp_path, files)
    (tmp_path / "generated_nodes.py").write_text(files["nodes.py"], encoding="utf-8")
    (tmp_path / "state.py").write_text("AgentState = dict\n", encoding="utf-8")

    tool_entries = tool_names or []
    tools_py = [
        "TOOL_CALLS = []",
        "",
        "class FakeTool:",
        "    def __init__(self, name):",
        "        self.name = name",
        "    def invoke(self, args):",
        "        TOOL_CALLS.append((self.name, args))",
        "        return f\"tool:{self.name}\"",
        "",
        "TOOLS = {",
        f"    'assistant': [{', '.join(f'FakeTool({name!r})' for name in tool_entries)}],",
        "}",
        "TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS.get('assistant', [])}",
        "",
    ]
    (tmp_path / "tools.py").write_text("\n".join(tools_py), encoding="utf-8")

    fake_messages = types.ModuleType("langchain_core.messages")

    class HumanMessage:
        type = "human"

        def __init__(self, content):
            self.content = content

    class SystemMessage:
        type = "system"

        def __init__(self, content):
            self.content = content

    class ToolMessage:
        type = "tool"

        def __init__(self, content, tool_call_id):
            self.content = content
            self.tool_call_id = tool_call_id

    fake_messages.HumanMessage = HumanMessage
    fake_messages.SystemMessage = SystemMessage
    fake_messages.ToolMessage = ToolMessage

    fake_openai = types.ModuleType("langchain_openai")

    class FakeResponse:
        def __init__(self, content="", tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []

    class ChatOpenAI:
        SCRIPT = list(llm_script)

        def __init__(self, *args, **kwargs):
            self._script = [dict(item) for item in self.SCRIPT]

        def bind_tools(self, tools):
            self._tools = tools
            return self

        def invoke(self, working):
            item = self._script.pop(0)
            return FakeResponse(
                content=item.get("content", ""),
                tool_calls=item.get("tool_calls", []),
            )

    fake_openai.ChatOpenAI = ChatOpenAI

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(sys.modules, "langchain_core.messages", fake_messages)
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_openai)
    monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
    monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
    monkeypatch.delitem(sys.modules, "state", raising=False)
    monkeypatch.delitem(sys.modules, "tools", raising=False)

    spec_obj = importlib.util.spec_from_file_location(
        "generated_nodes_test_module",
        tmp_path / "generated_nodes.py",
    )
    assert spec_obj is not None
    assert spec_obj.loader is not None
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)
    return module


class TestLangGraphGenerator:
    def setup_method(self):
        self.gen = LangGraphGenerator()

    def test_generates_expected_files(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        assert "main.py" in files
        assert "_abp_trace.py" in files
        assert "_abp_harness.py" in files
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

    def test_trace_helper_is_valid_python(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        ast.parse(files["_abp_trace.py"])

    def test_harness_helper_is_valid_python(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        ast.parse(files["_abp_harness.py"])

    def test_main_py_maps_graph_step_limit_error(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        main_py = files["main.py"]
        assert "from langgraph.errors import GraphRecursionError" in main_py
        assert "ABP runtime step limit exceeded" in main_py
        assert "settings.max_graph_steps=25" in main_py

    def test_main_py_generates_input_contract_validation(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "input-contract-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "input": {
                "schema": {
                    "user_input": {"type": "string", "required": True},
                    "priority": {"type": "integer", "required": False, "default": 1},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))
        main_py = files["main.py"]
        assert "INPUT_SCHEMA = {" in main_py
        assert "_validate_input_payload" in main_py
        assert "missing required field" in main_py
        assert "unknown input field(s)" in main_py

    def test_generated_main_raises_abp_error_on_step_limit(self, tmp_path, monkeypatch):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "from langgraph.errors import GraphRecursionError\n\n"
            "class DummyGraph:\n"
            "    def invoke(self, state, config=None):\n"
            "        raise GraphRecursionError('limit hit')\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        (tmp_path / "generated_main.py").write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        spec = importlib.util.spec_from_file_location(
            "generated_main_step_limit_test",
            tmp_path / "generated_main.py",
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)

        with pytest.raises(RuntimeError, match="ABP runtime step limit exceeded"):
            module.run("hello")

    def test_generated_main_rejects_invalid_input_before_graph_execution(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "input-contract-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "input": {
                "schema": {
                    "user_input": {"type": "string", "required": True},
                    "department": {"type": "string", "required": True, "enum": ["billing", "support"]},
                    "note": {"type": "string", "required": False, "nullable": True},
                    "priority": {"type": "integer", "required": False, "default": 1},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "class DummyGraph:\n"
            "    def __init__(self):\n"
            "        self.call_count = 0\n"
            "    def invoke(self, state, config=None):\n"
            "        self.call_count += 1\n"
            "        return {'messages': []}\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        main_path = tmp_path / "generated_main.py"
        main_path.write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.delitem(sys.modules, "graph", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_input_invalid_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        with pytest.raises(ValueError, match="missing required field 'department'"):
            module.run({"user_input": "hello"})
        assert module.graph.call_count == 0

        with pytest.raises(ValueError, match="must be one of"):
            module.run({"user_input": "hello", "department": "sales"})
        assert module.graph.call_count == 0

    def test_generated_main_accepts_nullable_enum_and_defaulted_input(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "input-contract-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "input": {
                "schema": {
                    "user_input": {"type": "string", "required": True},
                    "department": {"type": "string", "required": True, "enum": ["billing", "support"]},
                    "note": {"type": "string", "required": False, "nullable": True},
                    "priority": {"type": "integer", "required": False, "default": 1},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "class DummyMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n\n"
            "class DummyGraph:\n"
            "    def __init__(self):\n"
            "        self.last_state = None\n"
            "    def invoke(self, state, config=None):\n"
            "        self.last_state = state\n"
            "        return {'messages': [DummyMessage('ok')]}\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        main_path = tmp_path / "generated_main.py"
        main_path.write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.delitem(sys.modules, "graph", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_input_valid_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        response = module.run({
            "user_input": "hello",
            "department": "billing",
            "note": None,
        })

        assert response == "ok"
        assert module.graph.last_state["department"] == "billing"
        assert module.graph.last_state["note"] is None
        assert module.graph.last_state["priority"] == 1
        assert module.graph.last_state["messages"][0].content == "hello"

    def test_main_py_generates_output_contract_validation(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "output-contract-test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "answer": {"type": "string"},
                    "confidence": {"type": "number"},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o"}},
            "output": {
                "schema": {
                    "answer": {"type": "string", "required": True},
                    "confidence": {"type": "number", "required": True},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))
        main_py = files["main.py"]
        assert "OUTPUT_SCHEMA = {" in main_py
        assert "_validate_output_payload" in main_py
        assert "Output contract error" in main_py

    def test_generated_main_rejects_invalid_output_before_returning(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "output-contract-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "output": {
                "schema": {
                    "answer": {"type": "string", "required": True},
                    "confidence": {"type": "number", "required": True},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "class DummyMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n\n"
            "class DummyGraph:\n"
            "    def invoke(self, state, config=None):\n"
            "        return {'messages': [DummyMessage('ok')], 'answer': 'done'}\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        main_path = tmp_path / "generated_main.py"
        main_path.write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.delitem(sys.modules, "graph", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_output_invalid_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        with pytest.raises(ValueError, match="missing required field 'confidence'"):
            module.run("hello")

    def test_generated_main_returns_valid_structured_output(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "output-contract-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "output": {
                "schema": {
                    "answer": {"type": "string", "required": True},
                    "confidence": {"type": "number", "required": True},
                    "category": {"type": "string", "required": False, "default": "general"},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "class DummyMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n\n"
            "class DummyGraph:\n"
            "    def invoke(self, state, config=None):\n"
            "        return {\n"
            "            'messages': [DummyMessage('ok')],\n"
            "            'answer': 'done',\n"
            "            'confidence': 0.82,\n"
            "        }\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        main_path = tmp_path / "generated_main.py"
        main_path.write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "graph", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_output_valid_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        response = module.run("hello")

        assert response == {
            "answer": "done",
            "confidence": 0.82,
            "category": "general",
        }

    def test_generated_main_emits_contract_failed_and_run_finished_events(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "output-contract-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "output": {
                "schema": {
                    "answer": {"type": "string", "required": True},
                    "confidence": {"type": "number", "required": True},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "class DummyMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n\n"
            "class DummyGraph:\n"
            "    def invoke(self, state, config=None):\n"
            "        return {'messages': [DummyMessage('ok')], 'answer': 'done'}\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        main_path = tmp_path / "generated_main.py"
        main_path.write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "graph", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_output_trace_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        with pytest.raises(ValueError, match="missing required field 'confidence'"):
            module.run("hello")

        trace_mod = sys.modules["_abp_trace"]
        manifest = trace_mod.get_last_trace_manifest()
        assert manifest is not None
        assert [event["event"] for event in manifest["trace"]] == ["contract_failed", "run_finished"]
        assert manifest["trace"][-1]["metadata"]["status"] == "failed"

    def test_generated_main_emits_run_finished_on_success(self, tmp_path, monkeypatch):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)

        _write_trace_helper(tmp_path, files)
        (tmp_path / "langgraph").mkdir()
        (tmp_path / "langgraph" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langgraph" / "errors.py").write_text(
            "class GraphRecursionError(Exception):\n    pass\n",
            encoding="utf-8",
        )

        (tmp_path / "langchain_core").mkdir()
        (tmp_path / "langchain_core" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "langchain_core" / "messages.py").write_text(
            "class HumanMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n\n"
            "class AIMessage:\n"
            "    def __init__(self, content):\n"
            "        self.content = content\n",
            encoding="utf-8",
        )

        (tmp_path / "graph.py").write_text(
            "from langchain_core.messages import AIMessage\n\n"
            "class DummyGraph:\n"
            "    def invoke(self, state, config=None):\n"
            "        return {'messages': [AIMessage('ok')]}\n\n"
            "graph = DummyGraph()\n",
            encoding="utf-8",
        )
        main_path = tmp_path / "generated_main.py"
        main_path.write_text(files["main.py"], encoding="utf-8")

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "graph", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core", raising=False)
        monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_success_trace_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        assert module.run("hello") == "ok"

        trace_mod = sys.modules["_abp_trace"]
        manifest = trace_mod.get_last_trace_manifest()
        assert manifest is not None
        assert manifest["trace"][-1]["event"] == "run_finished"
        assert manifest["trace"][-1]["metadata"]["status"] == "success"
        assert "output_state_hash" in manifest["trace"][-1]

    def test_nodes_py_generates_human_in_the_loop_helpers(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "hitl-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "tools": {"dangerous_tool": {"type": "function", "parameters": {"message": {"type": "string"}}}},
            "agents": {
                "assistant": {
                    "model": "gpt-4o",
                    "tools": ["dangerous_tool"],
                    "human_in_the_loop": {"enabled": True, "trigger": "before_tool_call"},
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))
        nodes_py = files["nodes.py"]
        assert "_require_human_review" in nodes_py
        assert "ABP_HITL_MODE" in nodes_py
        assert "human_review_requested" in nodes_py

    def test_nodes_py_generates_node_output_contract_validation(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "node-output-contract-test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "route": {"type": "string", "nullable": True, "default": None},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o"}},
            "contracts": {
                "nodes": {"assistant": {"output_contract": "route_payload", "produces": ["route"]}},
                "outputs": {
                    "route_payload": {
                        "type": "object",
                        "required": ["route"],
                        "properties": {"route": {"type": "string"}},
                        "additionalProperties": False,
                    }
                },
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))
        nodes_py = files["nodes.py"]
        assert "_validate_node_output_contract" in nodes_py
        assert "contract_kind=\"output_contract\"" in nodes_py

    def test_legacy_output_schema_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "legacy-output-schema-test"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "department": {"type": "string", "nullable": True, "default": None},
                    }
                },
                "agents": {
                    "assistant": {
                        "model": "gpt-4o",
                        "output_schema": {
                            "department": {"type": "string", "enum": ["billing", "technical"]},
                        },
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            })
        assert "output_schema is no longer supported" in str(exc_info.value)

    def test_human_in_the_loop_before_tool_call_blocks_tool_execution(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABP_HITL_MODE", raising=False)
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "hitl-before-tool"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "tools": {"dangerous_tool": {"type": "function", "parameters": {"message": {"type": "string"}}}},
                "agents": {
                    "assistant": {
                        "model": "gpt-4o",
                        "tools": ["dangerous_tool"],
                        "human_in_the_loop": {
                            "enabled": True,
                            "trigger": "before_tool_call",
                            "tools": ["dangerous_tool"],
                        },
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[
                {"tool_calls": [{"id": "tc1", "name": "dangerous_tool", "args": {"message": "hi"}}]},
            ],
            tool_names=["dangerous_tool"],
        )

        with pytest.raises(PermissionError, match="before_tool_call"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        tools_module = sys.modules["tools"]
        assert tools_module.TOOL_CALLS == []

    def test_human_in_the_loop_after_tool_call_blocks_after_execution(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABP_HITL_MODE", raising=False)
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "hitl-after-tool"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "tools": {"dangerous_tool": {"type": "function", "parameters": {"message": {"type": "string"}}}},
                "agents": {
                    "assistant": {
                        "model": "gpt-4o",
                        "tools": ["dangerous_tool"],
                        "human_in_the_loop": {
                            "enabled": True,
                            "trigger": "after_tool_call",
                            "tools": ["dangerous_tool"],
                        },
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[
                {"tool_calls": [{"id": "tc1", "name": "dangerous_tool", "args": {"message": "hi"}}]},
            ],
            tool_names=["dangerous_tool"],
        )

        with pytest.raises(PermissionError, match="after_tool_call"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        tools_module = sys.modules["tools"]
        assert tools_module.TOOL_CALLS == [("dangerous_tool", {"message": "hi"})]

    def test_human_in_the_loop_before_response_blocks_plain_response(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABP_HITL_MODE", raising=False)
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "hitl-before-response"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {
                    "assistant": {
                        "model": "gpt-4o",
                        "human_in_the_loop": {
                            "enabled": True,
                            "trigger": "before_response",
                        },
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": "plain response"}],
        )

        with pytest.raises(PermissionError, match="before_response"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

    def test_human_in_the_loop_always_can_be_explicitly_approved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABP_APPROVED_HITL", "assistant:before_response")
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "hitl-always"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {
                    "assistant": {
                        "model": "gpt-4o",
                        "human_in_the_loop": {
                            "enabled": True,
                            "trigger": "always",
                        },
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": "plain response"}],
        )

        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})
        assert result["messages"][-1].content == "plain response"

    def test_generated_nodes_emit_ordered_node_events(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-trace-test"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": "plain response"}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-trace-test",
            blueprint_version="1.0",
            mode="live",
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})
        manifest = trace_mod.current_recorder().manifest

        assert result["messages"][-1].content == "plain response"
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "node_finished"]
        assert manifest["trace"][0]["node"] == "assistant"
        assert "input_state_hash" in manifest["trace"][0]
        assert "output_state_hash" in manifest["trace"][1]

    def test_node_requires_contract_fails_before_execution(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-requires-test"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {"assistant": {"requires": ["messages"]}},
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-requires-test",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="requires state field"):
            module.node_assistant({})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        assert manifest["trace"][-1]["metadata"]["contract_kind"] == "requires"

    def test_node_forbids_mutation_contract_fails_at_runtime(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-forbids-mutation-test"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {"assistant": {"forbids_mutation": ["messages"]}},
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": "plain response"}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-forbids-mutation-test",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="forbidden field"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        assert manifest["trace"][-1]["metadata"]["contract_kind"] == "forbids_mutation"

    def test_state_immutable_fields_fail_on_mutation(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "state-immutable-test"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "state": {"immutable_fields": ["messages"]},
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": "plain response"}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="state-immutable-test",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="immutable field"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        assert manifest["trace"][-1]["metadata"]["contract_kind"] == "immutable_fields"

    def test_node_produces_contract_fails_when_output_missing(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-produces-test"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "route": {"type": "string", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {"assistant": {"produces": ["route"]}},
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": "plain response"}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-produces-test",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="did not produce required field"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        assert manifest["trace"][-1]["metadata"]["contract_kind"] == "produces"

    def test_node_output_contract_merges_validated_structured_output(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-output-contract-valid"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "route": {"type": "string", "nullable": True, "default": None},
                        "confidence": {"type": "number", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {
                        "assistant": {
                            "output_contract": "route_payload",
                            "produces": ["route", "confidence"],
                        }
                    },
                    "outputs": {
                        "route_payload": {
                            "type": "object",
                            "required": ["route", "confidence"],
                            "properties": {
                                "route": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "additionalProperties": False,
                        }
                    },
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": '{"route":"billing","confidence":0.91}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-output-contract-valid",
            blueprint_version="1.0",
            mode="live",
        )

        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert result["route"] == "billing"
        assert result["confidence"] == 0.91
        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "node_finished"]

    def test_node_output_contract_fails_on_invalid_shape(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-output-contract-invalid"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "route": {"type": "string", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {
                        "assistant": {
                            "output_contract": "route_payload",
                            "produces": ["route"],
                        }
                    },
                    "outputs": {
                        "route_payload": {
                            "type": "object",
                            "required": ["route"],
                            "properties": {"route": {"type": "string"}},
                            "additionalProperties": False,
                        }
                    },
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": '{"route":7}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-output-contract-invalid",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="Node output contract error"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        assert manifest["trace"][-1]["metadata"]["contract_kind"] == "output_contract"

    def test_generated_nodes_use_mock_llm_fixtures_when_enabled(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "mock-llm-test"},
                "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[],
        )
        monkeypatch.setenv("ABP_LLM_MODE", "mock")
        monkeypatch.setenv(
            "ABP_HARNESS_FIXTURES",
            json.dumps({"llm_outputs": {"assistant": [{"content": "fixture reply"}]}}),
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="mock-llm-test",
            blueprint_version="1.0",
            mode="mock",
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert result["messages"][-1].content == "fixture reply"

    def test_generated_tools_emit_tool_called_and_tool_failed_events(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "tool-trace-test"},
            "tools": {
                "dangerous_tool": {
                    "type": "function",
                    "description": "Dangerous operation",
                    "parameters": {"message": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["dangerous_tool"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        tools_path = tmp_path / "generated_tools.py"
        tools_path.write_text(files["tools.py"], encoding="utf-8")

        fake_langchain_core = types.ModuleType("langchain_core")
        fake_tools_mod = types.ModuleType("langchain_core.tools")

        class FakeTool:
            def __init__(self, func, name=None, description=None):
                self.func = func
                self.name = name or func.__name__
                self.description = description or ""

            def invoke(self, args):
                return self.func(**args)

        def tool(func):
            return FakeTool(func, name=func.__name__, description=func.__doc__)

        class StructuredTool:
            @classmethod
            def from_function(cls, func, name=None, description=None):
                return FakeTool(func, name=name, description=description)

        fake_tools_mod.tool = tool
        fake_tools_mod.StructuredTool = StructuredTool
        fake_langchain_core.tools = fake_tools_mod

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.setitem(sys.modules, "langchain_core", fake_langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_tools_trace_test",
            tools_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="tool-trace-test",
            blueprint_version="1.0",
            mode="live",
        )
        trace_mod.set_current_node("assistant")

        with pytest.raises(NotImplementedError, match="dangerous_tool is not implemented yet"):
            module.dangerous_tool.invoke({"message": "ship it"})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["tool_called", "tool_failed"]
        assert manifest["trace"][0]["tool"] == "dangerous_tool"
        assert manifest["trace"][0]["node"] == "assistant"
        assert "args_hash" in manifest["trace"][0]

    def test_generated_tools_use_stubbed_outputs_when_enabled(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "tool-stub-test"},
            "tools": {
                "lookup_invoice": {
                    "type": "function",
                    "description": "Lookup invoice",
                    "parameters": {"invoice_id": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup_invoice"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        tools_path = tmp_path / "generated_tools.py"
        tools_path.write_text(files["tools.py"], encoding="utf-8")

        fake_langchain_core = types.ModuleType("langchain_core")
        fake_tools_mod = types.ModuleType("langchain_core.tools")

        class FakeTool:
            def __init__(self, func, name=None, description=None):
                self.func = func
                self.name = name or func.__name__
                self.description = description or ""

            def invoke(self, args):
                return self.func(**args)

        def tool(func):
            return FakeTool(func, name=func.__name__, description=func.__doc__)

        class StructuredTool:
            @classmethod
            def from_function(cls, func, name=None, description=None):
                return FakeTool(func, name=name, description=description)

        fake_tools_mod.tool = tool
        fake_tools_mod.StructuredTool = StructuredTool
        fake_langchain_core.tools = fake_tools_mod

        monkeypatch.setenv("ABP_TOOL_MODE", "stub")
        monkeypatch.setenv(
            "ABP_HARNESS_FIXTURES",
            json.dumps({"tool_outputs": {"lookup_invoice": {"result": {"status": "paid"}}}}),
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.setitem(sys.modules, "langchain_core", fake_langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_tools_stub_test",
            tools_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        result = module.lookup_invoice.invoke({"invoice_id": "inv-123"})
        assert result == {"status": "paid"}

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
        assert 'classify_intent = StructuredTool.from_function(func=_approved_classify_intent' in tools_py
        assert 'web_search = StructuredTool.from_function(func=_approved_web_search' in tools_py

    def test_requires_approval_generates_approval_gate(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "approval-test"},
            "tools": {
                "dangerous_tool": {
                    "type": "function",
                    "description": "Dangerous operation",
                    "requires_approval": True,
                    "parameters": {"message": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["dangerous_tool"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))
        tools_py = files["tools.py"]
        assert "_require_approval" in tools_py
        assert '_emit_approval_event("approval_requested", tool_name, args)' in tools_py
        assert "ABP_APPROVED_TOOLS" in tools_py
        assert '_require_approval("dangerous_tool", _abp_args)' in tools_py

    def test_no_impl_tool_generates_stub(self):
        ir = load_ir("impl_tools.yml")
        files = self.gen.generate(ir)
        tools_py = files["tools.py"]
        assert "raise NotImplementedError" in tools_py
        assert "def send_email(" in tools_py

    def test_generated_tools_block_protected_tool_without_approval(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "approval-test"},
            "tools": {
                "dangerous_tool": {
                    "type": "function",
                    "description": "Dangerous operation",
                    "requires_approval": True,
                    "parameters": {"message": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["dangerous_tool"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        tools_path = tmp_path / "generated_tools.py"
        tools_path.write_text(files["tools.py"], encoding="utf-8")

        monkeypatch.delenv("ABP_TOOL_APPROVAL_MODE", raising=False)
        monkeypatch.delenv("ABP_APPROVED_TOOLS", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.syspath_prepend(str(tmp_path))

        fake_langchain_core = types.ModuleType("langchain_core")
        fake_tools_mod = types.ModuleType("langchain_core.tools")

        class FakeTool:
            def __init__(self, func, name=None, description=None):
                self.func = func
                self.name = name or func.__name__
                self.description = description or ""

            def invoke(self, args):
                return self.func(**args)

        def tool(func):
            return FakeTool(func, name=func.__name__, description=func.__doc__)

        class StructuredTool:
            @classmethod
            def from_function(cls, func, name=None, description=None):
                return FakeTool(func, name=name, description=description)

        fake_tools_mod.tool = tool
        fake_tools_mod.StructuredTool = StructuredTool
        fake_langchain_core.tools = fake_tools_mod
        monkeypatch.setitem(sys.modules, "langchain_core", fake_langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_tools_approval_test",
            tools_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with pytest.raises(PermissionError, match="Approval required for tool 'dangerous_tool'"):
                module.dangerous_tool.invoke({"message": "ship it"})

        assert '"event": "approval_requested"' in stderr.getvalue()
        assert '"tool": "dangerous_tool"' in stderr.getvalue()

    def test_generated_tools_allow_protected_tool_with_explicit_approval(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "approval-test"},
            "tools": {
                "dangerous_tool": {
                    "type": "function",
                    "description": "Dangerous operation",
                    "requires_approval": True,
                    "parameters": {"message": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["dangerous_tool"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))

        _write_trace_helper(tmp_path, files)
        _write_harness_helper(tmp_path, files)
        tools_path = tmp_path / "generated_tools.py"
        tools_path.write_text(files["tools.py"], encoding="utf-8")

        monkeypatch.setenv("ABP_APPROVED_TOOLS", "dangerous_tool")
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.syspath_prepend(str(tmp_path))

        fake_langchain_core = types.ModuleType("langchain_core")
        fake_tools_mod = types.ModuleType("langchain_core.tools")

        class FakeTool:
            def __init__(self, func, name=None, description=None):
                self.func = func
                self.name = name or func.__name__
                self.description = description or ""

            def invoke(self, args):
                return self.func(**args)

        def tool(func):
            return FakeTool(func, name=func.__name__, description=func.__doc__)

        class StructuredTool:
            @classmethod
            def from_function(cls, func, name=None, description=None):
                return FakeTool(func, name=name, description=description)

        fake_tools_mod.tool = tool
        fake_tools_mod.StructuredTool = StructuredTool
        fake_langchain_core.tools = fake_tools_mod
        monkeypatch.setitem(sys.modules, "langchain_core", fake_langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_tools_approval_allowed_test",
            tools_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        with pytest.raises(NotImplementedError, match="dangerous_tool is not implemented yet"):
            module.dangerous_tool.invoke({"message": "ship it"})

    def test_impl_tools_py_is_valid_python(self):
        ir = load_ir("impl_tools.yml")
        files = self.gen.generate(ir)
        ast.parse(files["tools.py"])

    def test_retrieval_tool_wires_generic_retriever_impl(self):
        ir = load_ir("rag_agent.yml")
        files = self.gen.generate(ir)
        tools_py = files["tools.py"]
        assert "from myapp.retrieval import search_support_docs as _retriever_support_docs_impl" in tools_py
        assert '"support_docs": {"impl": _retriever_support_docs_impl' in tools_py
        assert 'result = retriever["impl"](query=query, top_k=4, config=retriever["config"])' in tools_py

    def test_retrieval_tools_py_is_valid_python(self):
        ir = load_ir("rag_agent.yml")
        files = self.gen.generate(ir)
        ast.parse(files["tools.py"])

    def test_rag_context_only_generates_retrieval_injection(self):
        ir = load_ir("rag_agent.yml")
        files = self.gen.generate(ir)
        nodes_py = files["nodes.py"]
        assert 'TOOLS_BY_NAME["search_kb"].invoke({"query": rag_query})' in nodes_py
        assert "Relevant retrieved context" in nodes_py

    def test_rag_nodes_py_is_valid_python(self):
        ir = load_ir("rag_agent.yml")
        files = self.gen.generate(ir)
        ast.parse(files["nodes.py"])

    def test_memory_in_memory_uses_memorysaver(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        graph_py = files["graph.py"]
        assert "MemorySaver" in graph_py
        assert "MemorySaver()" in graph_py

    def test_memory_sqlite_uses_sqlitesaver(self):
        ir = load_ir("memory_sqlite.yml")
        files = self.gen.generate(ir)
        graph_py = files["graph.py"]
        assert "SqliteSaver" in graph_py
        assert "from_conn_string" in graph_py
        assert "SQLITE_DB_PATH" in graph_py

    def test_memory_redis_uses_redissaver(self):
        ir = load_ir("memory_redis.yml")
        files = self.gen.generate(ir)
        graph_py = files["graph.py"]
        assert "RedisSaver" in graph_py
        assert "from_conn_string" in graph_py
        assert "REDIS_URL" in graph_py

    def test_memory_postgres_uses_postgressaver(self):
        ir = load_ir("memory_postgres.yml")
        files = self.gen.generate(ir)
        graph_py = files["graph.py"]
        assert "PostgresSaver" in graph_py
        assert "from_conn_string" in graph_py
        assert "DATABASE_URL" in graph_py

    def test_memory_redis_graph_is_valid_python(self):
        ir = load_ir("memory_redis.yml")
        files = self.gen.generate(ir)
        ast.parse(files["graph.py"])

    def test_memory_sqlite_requirements_include_package(self):
        ir = load_ir("memory_sqlite.yml")
        files = self.gen.generate(ir)
        assert "langgraph-checkpoint-sqlite" in files["requirements.txt"]

    def test_memory_redis_requirements_include_package(self):
        ir = load_ir("memory_redis.yml")
        files = self.gen.generate(ir)
        assert "langgraph-checkpoint-redis" in files["requirements.txt"]
        assert "redis>=" in files["requirements.txt"]

    def test_memory_postgres_requirements_include_package(self):
        ir = load_ir("memory_postgres.yml")
        files = self.gen.generate(ir)
        assert "langgraph-checkpoint-postgres" in files["requirements.txt"]
        assert "psycopg" in files["requirements.txt"]

    def test_memory_in_memory_requirements_no_extra_package(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        assert "langgraph-checkpoint-sqlite" not in files["requirements.txt"]
        assert "langgraph-checkpoint-redis" not in files["requirements.txt"]
        assert "langgraph-checkpoint-postgres" not in files["requirements.txt"]

    def test_reasoning_generates_extended_thinking(self):
        ir = load_ir("reasoning_agent.yml")
        files = self.gen.generate(ir)
        nodes_py = files["nodes.py"]
        assert "thinking={'type': 'enabled', 'budget_tokens': 10000}" in nodes_py
        assert "temperature=1" in nodes_py

    def test_reasoning_params_are_passed_through_for_openai(self):
        ir = load_ir("basic_chatbot.yml")
        node = ir.get_node("assistant")
        assert node is not None
        assert node.agent is not None
        node.agent.reasoning = ReasoningConfig(
            enabled=True,
            params={"reasoning": {"effort": "high"}},
        )
        files = self.gen.generate(ir)
        expected = "ChatOpenAI(model='gpt-4o', temperature=0.7, reasoning={'effort': 'high'})"
        assert expected in files["nodes.py"]

    def test_reasoning_nodes_py_is_valid_python(self):
        ir = load_ir("reasoning_agent.yml")
        files = self.gen.generate(ir)
        ast.parse(files["nodes.py"])

    def test_no_reasoning_generates_normal_llm(self):
        ir = load_ir("basic_chatbot.yml")
        files = self.gen.generate(ir)
        assert "thinking=" not in files["nodes.py"]

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
