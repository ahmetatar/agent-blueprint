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
    tool_validation_body: list[str] | None = None,
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
        "def validate_tool_arguments(tool_name, args):",
        *(
            [f"    {line}" for line in tool_validation_body]
            if tool_validation_body
            else ["    return None"]
        ),
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
        def __init__(self, content="", tool_calls=None, usage_metadata=None, cost_usd=None, response_metadata=None):
            self.content = content
            self.tool_calls = tool_calls or []
            self.usage_metadata = usage_metadata
            self.cost_usd = cost_usd
            self.response_metadata = response_metadata or {}

    class ChatOpenAI:
        SCRIPT = list(llm_script)

        def __init__(self, *args, **kwargs):
            self._script = [dict(item) for item in self.SCRIPT]

        def bind_tools(self, tools):
            self._tools = tools
            return self

        def invoke(self, working):
            item = self._script.pop(0)
            if "raises" in item:
                raise item["raises"]
            return FakeResponse(
                content=item.get("content", ""),
                tool_calls=item.get("tool_calls", []),
                usage_metadata=item.get("usage"),
                cost_usd=item.get("cost_usd"),
                response_metadata=item.get("response_metadata", {}),
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

    def test_parallel_node_generates_fanout_and_join_edges(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "parallel-test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "findings": {"type": "array", "reducer": "append"},
                    "scores": {"type": "object", "reducer": "merge"},
                    "summary": {"type": "string", "reducer": "replace"},
                }
            },
            "graph": {
                "entry_point": "fanout",
                "nodes": {
                    "fanout": {
                        "type": "parallel",
                        "branches": ["research", "pricing"],
                        "join": "merge",
                    },
                    "research": {"type": "function"},
                    "pricing": {"type": "function"},
                    "merge": {"type": "function"},
                },
                "edges": [{"from": "merge", "to": "END"}],
            },
        })
        files = self.gen.generate(compile_blueprint(spec))

        ast.parse(files["graph.py"])
        ast.parse(files["nodes.py"])
        ast.parse(files["state.py"])
        graph_py = files["graph.py"]
        nodes_py = files["nodes.py"]
        state_py = files["state.py"]

        assert "def _fan_out_fanout(state: AgentState) -> list[str]:" in graph_py
        assert "return ['research', 'pricing']" in graph_py
        assert 'builder.add_edge("research", "merge")' in graph_py
        assert 'builder.add_edge("pricing", "merge")' in graph_py
        assert "def node_fanout(state: AgentState) -> dict:" in nodes_py
        assert '"parallel_started"' in nodes_py
        assert '"failure_policy": "fail_fast"' in nodes_py
        assert "findings: Annotated[Any, _abp_append_reducer]" in state_py
        assert "scores: Annotated[Any, _abp_merge_reducer]" in state_py
        assert "summary: Annotated[str | None, _abp_replace_reducer]" in state_py

    def test_subgraph_node_generates_namespaced_adapters(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "subgraph-test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "prd": {"type": "object", "reducer": "replace", "default": None, "nullable": True},
                }
            },
            "agents": {"writer_agent": {"model": "gpt-4o"}},
            "graph": {
                "entry_point": "prd_pipeline",
                "nodes": {
                    "prd_pipeline": {
                        "type": "subgraph",
                        "ref": "prd_generation_v1",
                        "input_map": {"messages": "messages"},
                        "output_map": {"prd": "prd"},
                    }
                },
                "edges": [{"from": "prd_pipeline", "to": "END"}],
            },
            "subgraphs": {
                "prd_generation_v1": {
                    "entry_point": "writer",
                    "nodes": {"writer": {"agent": "writer_agent"}},
                    "edges": [{"from": "writer", "to": "END"}],
                }
            },
        })
        files = self.gen.generate(compile_blueprint(spec))

        ast.parse(files["graph.py"])
        ast.parse(files["nodes.py"])
        ast.parse(files["state.py"])
        graph_py = files["graph.py"]
        nodes_py = files["nodes.py"]
        state_py = files["state.py"]

        assert 'builder.add_edge(START, "prd_pipeline__entry")' in graph_py
        assert 'builder.add_edge("prd_pipeline__entry", "prd_pipeline__writer")' in graph_py
        assert 'builder.add_edge("prd_pipeline__writer", "prd_pipeline__exit")' in graph_py
        assert '"subgraph_entered"' in nodes_py
        assert '"subgraph_exited"' in nodes_py
        assert '"prd_pipeline__messages": state.get("messages")' in nodes_py
        assert '"prd": state.get("prd_pipeline__prd")' in nodes_py
        assert 'working: list = list(state.get("prd_pipeline__messages", []))' in nodes_py
        assert 'updates["prd_pipeline__messages"] = new_messages' in nodes_py
        assert "prd_pipeline__messages: Annotated[list, add_messages]" in state_py
        assert "prd_pipeline__prd: Annotated[Any, _abp_replace_reducer]" in state_py

    def test_retry_policy_is_rendered_for_nodes(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "retry-render-test"},
            "agents": {"researcher": {"model": "gpt-4o"}},
            "graph": {
                "entry_point": "researcher",
                "nodes": {
                    "researcher": {
                        "agent": "researcher",
                        "retry": {
                            "max_attempts": 3,
                            "backoff_seconds": 0.25,
                            "on": ["exception"],
                        },
                    }
                },
                "edges": [],
            },
        })
        files = self.gen.generate(compile_blueprint(spec))

        ast.parse(files["nodes.py"])
        nodes_py = files["nodes.py"]
        assert "RETRY_POLICY_BY_NODE = {" in nodes_py
        assert '"researcher": {' in nodes_py
        assert '"max_attempts": 3' in nodes_py
        assert '"backoff_seconds": 0.25' in nodes_py
        assert '"on": [\'exception\']' in nodes_py
        assert 'response = _invoke_llm_with_retry("researcher", llm, working)' in nodes_py

    def test_node_retries_llm_exception_then_succeeds(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "retry-success-test"},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {
                        "assistant": {
                            "agent": "assistant",
                            "retry": {"max_attempts": 2, "backoff_seconds": 0},
                        }
                    },
                    "edges": [],
                },
            },
            llm_script=[
                {"raises": RuntimeError("temporary model failure")},
                {"content": "recovered"},
            ],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="retry-success-test",
            blueprint_version="1.0",
            mode="mock",
        )

        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert result["messages"][-1].content == "recovered"
        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == [
            "node_started",
            "retry_scheduled",
            "node_finished",
        ]
        retry_event = manifest["trace"][1]
        assert retry_event["metadata"]["attempt"] == 1
        assert retry_event["metadata"]["next_attempt"] == 2
        assert retry_event["metadata"]["max_attempts"] == 2
        assert retry_event["metadata"]["condition"] == "exception"

    def test_node_retry_exhaustion_fails_deterministically(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "retry-exhausted-test"},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {
                        "assistant": {
                            "agent": "assistant",
                            "retry": {"max_attempts": 2, "backoff_seconds": 0},
                        }
                    },
                    "edges": [],
                },
            },
            llm_script=[
                {"raises": RuntimeError("first failure")},
                {"raises": RuntimeError("second failure")},
            ],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="retry-exhausted-test",
            blueprint_version="1.0",
            mode="mock",
        )

        with pytest.raises(RuntimeError, match="second failure"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == [
            "node_started",
            "retry_scheduled",
            "retry_exhausted",
        ]
        exhausted_event = manifest["trace"][-1]
        assert exhausted_event["metadata"]["attempt"] == 2
        assert exhausted_event["metadata"]["max_attempts"] == 2
        assert exhausted_event["metadata"]["condition"] == "exception"

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

    def test_generated_main_enforces_latency_budget(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "latency-budget-test"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "policies": {"budgets": {"max_latency_seconds": 0.5}},
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
            "    def invoke(self, state, config=None):\n"
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
        spec_obj = importlib.util.spec_from_file_location(
            "generated_main_latency_budget_test",
            main_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)
        monkeypatch.setattr(module, "monotonic", iter([10.0, 10.75]).__next__)

        with pytest.raises(RuntimeError, match="run exceeded max_latency_seconds=0.5"):
            module.run("hello")

        trace_mod = sys.modules["_abp_trace"]
        manifest = trace_mod.get_last_trace_manifest()
        assert manifest is not None
        assert manifest["trace"][-2]["event"] == "policy_violation"
        assert manifest["trace"][-2]["metadata"]["policy_kind"] == "max_latency_seconds"
        assert manifest["trace"][-2]["metadata"]["elapsed_seconds"] == 0.75
        assert manifest["trace"][-1]["event"] == "run_finished"
        assert manifest["trace"][-1]["metadata"]["status"] == "failed"

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

    def test_node_output_contract_can_store_object_under_single_produced_field(
        self,
        tmp_path,
        monkeypatch,
    ):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "node-output-contract-object-field"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "prd": {"type": "object", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {
                        "assistant": {
                            "output_contract": "prd_contract",
                            "produces": ["prd"],
                        }
                    },
                    "outputs": {
                        "prd_contract": {
                            "type": "object",
                            "required": ["title", "problem"],
                            "properties": {
                                "title": {"type": "string"},
                                "problem": {"type": "string"},
                            },
                            "additionalProperties": False,
                        }
                    },
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": '{"title":"Invoice Assistant","problem":"Manual disputes"}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="node-output-contract-object-field",
            blueprint_version="1.0",
            mode="live",
        )

        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert result["prd"] == {
            "title": "Invoice Assistant",
            "problem": "Manual disputes",
        }
        assert "title" not in result
        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "node_finished"]

    def test_node_writes_declared_artifact_for_valid_contract(self, tmp_path, monkeypatch):
        artifact_root = tmp_path / "run-artifacts"
        monkeypatch.setenv("ABP_ARTIFACT_DIR", str(artifact_root))
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "artifact-write-valid"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "prd": {"type": "object", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {
                        "assistant": {
                            "output_contract": "prd_contract",
                            "produces": ["prd"],
                        }
                    },
                    "outputs": {
                        "prd_contract": {
                            "type": "object",
                            "required": ["title", "problem"],
                            "properties": {
                                "title": {"type": "string"},
                                "problem": {"type": "string"},
                            },
                            "additionalProperties": False,
                        }
                    },
                },
                "artifacts": {
                    "prd_doc": {
                        "format": "markdown",
                        "producer": "assistant",
                        "contract": "prd_contract",
                        "path": "artifacts/prd.md",
                        "metadata": {"kind": "prd"},
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": '{"title":"Invoice Assistant","problem":"Manual disputes"}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="artifact-write-valid",
            blueprint_version="1.0",
            mode="live",
        )

        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        artifact_path = artifact_root / "artifacts" / "prd.md"
        assert result["prd"]["title"] == "Invoice Assistant"
        assert artifact_path.read_text(encoding="utf-8") == (
            "## Title\n"
            "Invoice Assistant\n\n"
            "## Problem\n"
            "Manual disputes\n"
        )
        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == [
            "node_started",
            "artifact_written",
            "node_finished",
        ]
        artifact_event = manifest["trace"][1]
        assert artifact_event["metadata"]["artifact"] == "prd_doc"
        assert artifact_event["metadata"]["format"] == "markdown"
        assert artifact_event["metadata"]["contract"] == "prd_contract"
        assert artifact_event["metadata"]["metadata"] == {"kind": "prd"}
        assert artifact_event["metadata"]["validated"] is True
        assert artifact_event["metadata"]["path"] == str(artifact_path)

    def test_node_writes_json_yaml_and_text_artifacts_with_contract_validation(self, tmp_path, monkeypatch):
        artifact_root = tmp_path / "run-artifacts"
        monkeypatch.setenv("ABP_ARTIFACT_DIR", str(artifact_root))
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "artifact-format-validation"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "prd": {"type": "object", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "contracts": {
                    "nodes": {
                        "assistant": {
                            "output_contract": "prd_contract",
                            "produces": ["prd"],
                        }
                    },
                    "outputs": {
                        "prd_contract": {
                            "type": "object",
                            "required": ["title", "problem"],
                            "properties": {
                                "title": {"type": "string"},
                                "problem": {"type": "string"},
                            },
                            "additionalProperties": False,
                        }
                    },
                },
                "artifacts": {
                    "prd_json": {
                        "format": "json",
                        "producer": "assistant",
                        "contract": "prd_contract",
                        "path": "artifacts/prd.json",
                        "metadata": {"kind": "prd", "format_role": "machine"},
                    },
                    "prd_yaml": {
                        "format": "yaml",
                        "producer": "assistant",
                        "contract": "prd_contract",
                        "path": "artifacts/prd.yml",
                    },
                    "prd_text": {
                        "format": "text",
                        "producer": "assistant",
                        "contract": "prd_contract",
                        "path": "artifacts/prd.txt",
                    },
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": '{"title":"Invoice Assistant","problem":"Manual disputes"}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="artifact-format-validation",
            blueprint_version="1.0",
            mode="live",
        )

        module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert json.loads((artifact_root / "artifacts" / "prd.json").read_text(encoding="utf-8")) == {
            "title": "Invoice Assistant",
            "problem": "Manual disputes",
        }
        assert (artifact_root / "artifacts" / "prd.yml").read_text(encoding="utf-8") == (
            'title: "Invoice Assistant"\n'
            'problem: "Manual disputes"\n'
        )
        assert (artifact_root / "artifacts" / "prd.txt").read_text(encoding="utf-8") == (
            "{'title': 'Invoice Assistant', 'problem': 'Manual disputes'}\n"
        )
        manifest = trace_mod.current_recorder().manifest
        artifact_events = [
            event for event in manifest["trace"] if event["event"] == "artifact_written"
        ]
        assert [event["metadata"]["artifact"] for event in artifact_events] == [
            "prd_json",
            "prd_yaml",
            "prd_text",
        ]
        assert all(event["metadata"]["validated"] is True for event in artifact_events)
        assert artifact_events[0]["metadata"]["metadata"] == {
            "kind": "prd",
            "format_role": "machine",
        }

    def test_node_artifact_contract_mismatch_fails_before_write(self, tmp_path, monkeypatch):
        artifact_root = tmp_path / "run-artifacts"
        monkeypatch.setenv("ABP_ARTIFACT_DIR", str(artifact_root))
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "artifact-contract-mismatch"},
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
                            "output_contract": "route_contract",
                            "produces": ["route"],
                        }
                    },
                    "outputs": {
                        "route_contract": {
                            "type": "object",
                            "required": ["route"],
                            "properties": {"route": {"type": "string"}},
                        },
                        "prd_contract": {
                            "type": "object",
                            "required": ["title"],
                            "properties": {"title": {"type": "string"}},
                        },
                    },
                },
                "artifacts": {
                    "prd_doc": {
                        "format": "markdown",
                        "producer": "assistant",
                        "contract": "prd_contract",
                        "path": "artifacts/prd.md",
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            },
            llm_script=[{"content": '{"route":"billing"}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="artifact-contract-mismatch",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="Artifact contract error"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert not (artifact_root / "artifacts" / "prd.md").exists()
        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        assert manifest["trace"][-1]["metadata"]["stage"] == "artifact"
        assert manifest["trace"][-1]["metadata"]["artifact"] == "prd_doc"

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
        assert '"dangerous_tool",' in tools_py
        assert "per_tool_requires_approval=True" in tools_py

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

    def test_policy_config_is_emitted_into_generated_runtime(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "policy-runtime-test"},
            "model_providers": {
                "openai_priced": {
                    "provider": "openai",
                    "pricing": {
                        "input_per_1k_tokens_usd": 0.005,
                        "output_per_1k_tokens_usd": 0.015,
                    },
                }
            },
            "tools": {
                "lookup_invoice": {
                    "type": "function",
                    "parameters": {"invoice_id": {"type": "string", "required": True}},
                }
            },
            "agents": {
                "assistant": {
                    "model": "gpt-4o",
                    "model_provider": "openai_priced",
                    "tools": ["lookup_invoice"],
                }
            },
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "policies": {
                "tool_usage": {
                    "max_calls_per_node": 2,
                    "max_calls_per_run": 3,
                    "require_explicit_arguments": True,
                    "on_unknown_tool": "fail",
                },
                "budgets": {
                    "max_tokens_per_run": 3000,
                    "max_latency_seconds": 1.5,
                    "max_cost_usd": 0.25,
                },
            },
        })
        files = self.gen.generate(compile_blueprint(spec))
        assert 'TOOL_POLICY = {' in files["tools.py"]
        assert '"max_calls_per_node": 2' in files["tools.py"]
        assert '"max_calls_per_run": 3' in files["tools.py"]
        assert '"require_explicit_arguments": True' in files["tools.py"]
        assert "UNKNOWN_TOOL_POLICY = 'fail'" in files["tools.py"]
        assert 'BUDGET_POLICY = {' in files["main.py"]
        assert '"max_latency_seconds": 1.5' in files["main.py"]
        assert '"max_tokens_per_run": 3000' in files["_abp_trace.py"]
        assert '"max_cost_usd": 0.25' in files["_abp_trace.py"]
        assert "'input_per_1k_tokens_usd': 0.005" in files["_abp_trace.py"]

    def test_nodes_enforce_max_tokens_per_run(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "token-budget-test"},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {"assistant": {"agent": "assistant"}},
                    "edges": [],
                },
                "policies": {"budgets": {"max_tokens_per_run": 5}},
            },
            llm_script=[
                {"content": "first", "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}},
                {"content": "second", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
            ],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="token-budget-test",
            blueprint_version="1.0",
            mode="mock",
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})
        assert result["messages"][-1].content == "first"

        with pytest.raises(RuntimeError, match="run exceeded max_tokens_per_run=5"):
            module.node_assistant({"messages": [module.HumanMessage("again")]})

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][-1]["event"] == "policy_violation"
        assert manifest["trace"][-1]["metadata"]["policy_kind"] == "max_tokens_per_run"

    def test_nodes_fail_when_token_budget_lacks_usage_metadata(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "token-budget-metadata-test"},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {"assistant": {"agent": "assistant"}},
                    "edges": [],
                },
                "policies": {"budgets": {"max_tokens_per_run": 5}},
            },
            llm_script=[{"content": "first"}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="token-budget-metadata-test",
            blueprint_version="1.0",
            mode="mock",
        )

        with pytest.raises(RuntimeError, match="token usage metadata is required"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][-1]["event"] == "policy_violation"
        assert manifest["trace"][-1]["metadata"]["policy_kind"] == "max_tokens_per_run"

    def test_nodes_enforce_max_cost_usd_with_provider_pricing(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "cost-budget-test"},
                "model_providers": {
                    "openai_priced": {
                        "provider": "openai",
                        "pricing": {
                            "input_per_1k_tokens_usd": 1.0,
                            "output_per_1k_tokens_usd": 1.0,
                        },
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o", "model_provider": "openai_priced"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {"assistant": {"agent": "assistant"}},
                    "edges": [],
                },
                "policies": {"budgets": {"max_cost_usd": 0.003}},
            },
            llm_script=[
                {"content": "first", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
                {"content": "second", "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}},
            ],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="cost-budget-test",
            blueprint_version="1.0",
            mode="mock",
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})
        assert result["messages"][-1].content == "first"

        with pytest.raises(RuntimeError, match="run exceeded max_cost_usd=0.003"):
            module.node_assistant({"messages": [module.HumanMessage("again")]})

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][-1]["event"] == "policy_violation"
        assert manifest["trace"][-1]["metadata"]["policy_kind"] == "max_cost_usd"

    def test_nodes_enforce_max_cost_usd_with_explicit_fixture_cost(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "cost-budget-explicit-test"},
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {"assistant": {"agent": "assistant"}},
                    "edges": [],
                },
                "policies": {"budgets": {"max_cost_usd": 0.002}},
            },
            llm_script=[
                {"content": "first", "cost_usd": 0.0015},
                {"content": "second", "cost_usd": 0.0010},
            ],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="cost-budget-explicit-test",
            blueprint_version="1.0",
            mode="mock",
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})
        assert result["messages"][-1].content == "first"

        with pytest.raises(RuntimeError, match="run exceeded max_cost_usd=0.002"):
            module.node_assistant({"messages": [module.HumanMessage("again")]})

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][-1]["event"] == "policy_violation"
        assert manifest["trace"][-1]["metadata"]["policy_kind"] == "max_cost_usd"

    def test_nodes_apply_low_confidence_escalation(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "low-confidence-test"},
                "state": {
                    "fields": {
                        "messages": {"type": "list[message]", "reducer": "append"},
                        "route": {"type": "string", "nullable": True, "default": None},
                        "confidence": {"type": "number", "nullable": True, "default": None},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o"}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {
                        "assistant": {"agent": "assistant"},
                        "handoff_review": {"type": "handoff", "channel": "console"},
                    },
                    "edges": [{"from": "assistant", "to": "END"}],
                },
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
                        }
                    },
                },
                "policies": {
                    "escalation": {"on_low_confidence": "handoff_review", "confidence_threshold": 0.75}
                },
            },
            llm_script=[{"content": '{"route":"billing","confidence":0.42}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="low-confidence-test",
            blueprint_version="1.0",
            mode="mock",
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert result["route"] == "billing"
        assert result["confidence"] == 0.42
        assert result["__abp_escalation_target"] == "handoff_review"
        assert result["__abp_escalation_source"] == "assistant"

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][-1]["event"] == "node_finished"
        assert manifest["trace"][-1]["route"] == "handoff_review"
        assert manifest["trace"][-1]["metadata"]["escalated"] is True

    def test_graph_routes_low_confidence_escalation_to_handoff_target(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "graph-escalation-test"},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "graph": {
                "entry_point": "assistant",
                "nodes": {
                    "assistant": {"agent": "assistant"},
                    "handoff_review": {"type": "handoff", "channel": "console"},
                },
                "edges": [
                    {"from": "assistant", "to": "END"},
                    {"from": "handoff_review", "to": "END"},
                ],
            },
            "policies": {
                "escalation": {"on_low_confidence": "handoff_review", "confidence_threshold": 0.75}
            },
        })
        files = self.gen.generate(compile_blueprint(spec))
        graph_path = tmp_path / "generated_graph.py"
        graph_path.write_text(files["graph.py"], encoding="utf-8")
        (tmp_path / "state.py").write_text("AgentState = dict\n", encoding="utf-8")
        (tmp_path / "nodes.py").write_text(
            "def node_assistant(state):\n    return state\n\n"
            "def node_handoff_review(state):\n    return state\n",
            encoding="utf-8",
        )

        fake_langgraph_graph = types.ModuleType("langgraph.graph")

        class FakeStateGraph:
            instances = []

            def __init__(self, state_type):
                self.state_type = state_type
                self.conditional_edges = []
                self.edges = []
                FakeStateGraph.instances.append(self)

            def add_node(self, name, fn):
                return None

            def add_edge(self, source, target):
                self.edges.append((source, target))

            def add_conditional_edges(self, source, route_fn, mapping):
                self.conditional_edges.append((source, route_fn, mapping))

            def compile(self, checkpointer=None):
                return {"checkpointer": checkpointer, "conditional_edges": self.conditional_edges}

        fake_langgraph_graph.StateGraph = FakeStateGraph
        fake_langgraph_graph.START = "START"
        fake_langgraph_graph.END = "END"

        fake_memory = types.ModuleType("langgraph.checkpoint.memory")

        class MemorySaver:
            pass

        fake_memory.MemorySaver = MemorySaver

        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.setitem(sys.modules, "langgraph.graph", fake_langgraph_graph)
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint.memory", fake_memory)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_graph_escalation_test",
            graph_path,
        )
        assert spec_obj is not None
        assert spec_obj.loader is not None
        module = importlib.util.module_from_spec(spec_obj)
        sys.modules[spec_obj.name] = module
        spec_obj.loader.exec_module(module)

        builder = FakeStateGraph.instances[0]
        assistant_routes = [item for item in builder.conditional_edges if item[0] == "assistant"]
        assert len(assistant_routes) == 1
        _, route_fn, mapping = assistant_routes[0]
        assert mapping["handoff_review"] == "handoff_review"
        assert route_fn({"__abp_escalation_target": "handoff_review", "__abp_escalation_source": "assistant"}) == "handoff_review"
        assert route_fn({}) == "END"

    def test_generated_tools_enforce_max_calls_per_run(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "tool-policy-test"},
            "tools": {
                "lookup_invoice": {
                    "type": "function",
                    "description": "Lookup invoice",
                    "parameters": {"invoice_id": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup_invoice"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "policies": {"tool_usage": {"max_calls_per_run": 1}},
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
            json.dumps({"tool_outputs": {"lookup_invoice": [{"result": {"status": "paid"}}]}}),
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.setitem(sys.modules, "langchain_core", fake_langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_tools_policy_test",
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
            blueprint="tool-policy-test",
            blueprint_version="1.0",
            mode="stubbed",
        )
        trace_mod.set_current_node("assistant")

        first = module.lookup_invoice.invoke({"invoice_id": "inv-123"})
        assert first == {"status": "paid"}

        with pytest.raises(RuntimeError, match="exceeded max_calls_per_run=1"):
            module.lookup_invoice.invoke({"invoice_id": "inv-456"})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["tool_called", "policy_violation"]
        assert manifest["trace"][1]["tool"] == "lookup_invoice"
        assert manifest["trace"][1]["metadata"]["policy_kind"] == "max_calls_per_run"

    def test_generated_tools_enforce_max_calls_per_node(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "tool-policy-node-test"},
            "tools": {
                "lookup_invoice": {
                    "type": "function",
                    "description": "Lookup invoice",
                    "parameters": {"invoice_id": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup_invoice"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "policies": {"tool_usage": {"max_calls_per_node": 1}},
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
            json.dumps({"tool_outputs": {"lookup_invoice": [{"result": {"status": "paid"}}]}}),
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
        monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
        monkeypatch.setitem(sys.modules, "langchain_core", fake_langchain_core)
        monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools_mod)

        spec_obj = importlib.util.spec_from_file_location(
            "generated_tools_policy_node_test",
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
            blueprint="tool-policy-node-test",
            blueprint_version="1.0",
            mode="stubbed",
        )
        trace_mod.set_current_node("assistant")

        first = module.lookup_invoice.invoke({"invoice_id": "inv-123"})
        assert first == {"status": "paid"}

        with pytest.raises(RuntimeError, match="exceeded max_calls_per_node=1"):
            module.lookup_invoice.invoke({"invoice_id": "inv-456"})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["tool_called", "policy_violation"]
        assert manifest["trace"][1]["tool"] == "lookup_invoice"
        assert manifest["trace"][1]["metadata"]["policy_kind"] == "max_calls_per_node"

    def test_generated_tools_validate_explicit_arguments(self, tmp_path, monkeypatch):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "tool-policy-args-test"},
            "tools": {
                "lookup_invoice": {
                    "type": "function",
                    "description": "Lookup invoice",
                    "parameters": {"invoice_id": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup_invoice"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "policies": {"tool_usage": {"require_explicit_arguments": True}},
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
            "generated_tools_policy_args_test",
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
            blueprint="tool-policy-args-test",
            blueprint_version="1.0",
            mode="stubbed",
        )
        trace_mod.set_current_node("assistant")

        with pytest.raises(RuntimeError, match="missing required argument\\(s\\): invoice_id"):
            module.validate_tool_arguments("lookup_invoice", {})

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][0]["event"] == "policy_violation"
        assert manifest["trace"][0]["metadata"]["policy_kind"] == "explicit_arguments"

        trace_mod.current_recorder().manifest["trace"].clear()

        with pytest.raises(RuntimeError, match="unknown argument\\(s\\): extra"):
            module.validate_tool_arguments("lookup_invoice", {"invoice_id": "inv-123", "extra": True})

        manifest = trace_mod.current_recorder().manifest
        assert manifest["trace"][0]["event"] == "policy_violation"
        assert manifest["trace"][0]["metadata"]["policy_kind"] == "explicit_arguments"

    def test_unknown_tool_policy_can_fail_node_execution(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data={
                "blueprint": {"name": "unknown-tool-policy-test"},
                "tools": {
                    "lookup_invoice": {
                        "type": "function",
                        "parameters": {"invoice_id": {"type": "string", "required": True}},
                    }
                },
                "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup_invoice"]}},
                "graph": {
                    "entry_point": "assistant",
                    "nodes": {"assistant": {"agent": "assistant"}},
                    "edges": [],
                },
                "policies": {"tool_usage": {"on_unknown_tool": "fail"}},
            },
            llm_script=[
                {
                    "tool_calls": [
                        {"id": "call-1", "name": "missing_tool", "args": {"query": "refund"}},
                    ]
                }
            ],
            tool_names=["lookup_invoice"],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="unknown-tool-policy-test",
            blueprint_version="1.0",
            mode="mock",
        )

        with pytest.raises(RuntimeError, match="Tool policy violation: Unknown tool: missing_tool"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "policy_violation"]
        assert manifest["trace"][1]["tool"] == "missing_tool"
        assert manifest["trace"][1]["metadata"]["policy_kind"] == "unknown_tool"

    def test_nodes_validate_tool_arguments_before_invocation(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "node-policy-plumbing-test"},
            "tools": {
                "lookup_invoice": {
                    "type": "function",
                    "parameters": {"invoice_id": {"type": "string", "required": True}},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup_invoice"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        files = self.gen.generate(compile_blueprint(spec))
        assert "validate_tool_arguments(tc[\"name\"], tc.get(\"args\", {}))" in files["nodes.py"]

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


def _load_generated_main_module(
    tmp_path,
    monkeypatch,
    *,
    spec_data: dict,
    graph_module_body: str,
    module_name: str,
):
    gen = LangGraphGenerator()
    spec = BlueprintSpec.model_validate(spec_data)
    files = gen.generate(compile_blueprint(spec))

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

    (tmp_path / "graph.py").write_text(graph_module_body, encoding="utf-8")
    main_path = tmp_path / "generated_main.py"
    main_path.write_text(files["main.py"], encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
    monkeypatch.delitem(sys.modules, "graph", raising=False)
    monkeypatch.delitem(sys.modules, "langchain_core.messages", raising=False)
    spec_obj = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec_obj is not None
    assert spec_obj.loader is not None
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)
    return module


_REQUIRED_FIELDS_GRAPH_TEMPLATE = (
    "class DummyMessage:\n"
    "    def __init__(self, content):\n"
    "        self.content = content\n\n"
    "class DummyGraph:\n"
    "    def invoke(self, state, config=None):\n"
    "        return {RESULT}\n\n"
    "graph = DummyGraph()\n"
)


class TestStateRequiredFieldsEnforcement:
    gen = LangGraphGenerator()

    @staticmethod
    def _spec_data() -> dict:
        return {
            "blueprint": {"name": "required-fields-test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "summary": {"type": "string", "default": None, "nullable": True},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o"}},
            "contracts": {"state": {"required_fields": ["summary"]}},
            "graph": {
                "entry_point": "assistant",
                "nodes": {"assistant": {"agent": "assistant"}},
                "edges": [],
            },
        }

    def test_required_state_fields_rendered_in_main(self):
        spec = BlueprintSpec.model_validate(self._spec_data())
        files = self.gen.generate(compile_blueprint(spec))
        assert "STATE_REQUIRED_FIELDS = ['summary']" in files["main.py"]
        assert "_validate_required_state_fields" in files["main.py"]
        ast.parse(files["main.py"])

    def test_required_field_missing_emits_contract_failed_state_stage(self, tmp_path, monkeypatch):
        graph_body = _REQUIRED_FIELDS_GRAPH_TEMPLATE.replace(
            "{RESULT}", "{'messages': [DummyMessage('ok')]}"
        )
        module = _load_generated_main_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(),
            graph_module_body=graph_body,
            module_name="generated_main_required_missing_test",
        )

        with pytest.raises(ValueError, match="required field\\(s\\) 'summary'"):
            module.run("hello")

        manifest = sys.modules["_abp_trace"].get_last_trace_manifest()
        assert manifest is not None
        assert [event["event"] for event in manifest["trace"]] == ["contract_failed", "run_finished"]
        assert manifest["trace"][0]["metadata"]["stage"] == "state_required_fields"
        assert manifest["trace"][-1]["metadata"]["status"] == "failed"

    def test_required_field_none_value_fails(self, tmp_path, monkeypatch):
        graph_body = _REQUIRED_FIELDS_GRAPH_TEMPLATE.replace(
            "{RESULT}", "{'messages': [DummyMessage('ok')], 'summary': None}"
        )
        module = _load_generated_main_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(),
            graph_module_body=graph_body,
            module_name="generated_main_required_none_test",
        )

        with pytest.raises(ValueError, match="required field\\(s\\) 'summary'"):
            module.run("hello")

        manifest = sys.modules["_abp_trace"].get_last_trace_manifest()
        assert manifest is not None
        assert manifest["trace"][0]["event"] == "contract_failed"
        assert manifest["trace"][0]["metadata"]["stage"] == "state_required_fields"

    def test_required_fields_pass_when_present(self, tmp_path, monkeypatch):
        graph_body = _REQUIRED_FIELDS_GRAPH_TEMPLATE.replace(
            "{RESULT}", "{'messages': [DummyMessage('ok')], 'summary': 'done'}"
        )
        module = _load_generated_main_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(),
            graph_module_body=graph_body,
            module_name="generated_main_required_present_test",
        )

        response = module.run("hello")

        assert response == "ok"
        manifest = sys.modules["_abp_trace"].get_last_trace_manifest()
        assert manifest is not None
        events = [event["event"] for event in manifest["trace"]]
        assert "contract_failed" not in events
        assert manifest["trace"][-1]["metadata"]["status"] == "success"


class TestStateInvariantEnforcement:
    gen = LangGraphGenerator()

    @staticmethod
    def _spec_data(*, with_output_contract: bool) -> dict:
        spec: dict = {
            "blueprint": {"name": "invariant-test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "count": {"type": "integer", "nullable": True, "default": None},
                    "route": {"type": "string", "nullable": True, "default": None},
                    "items": {"type": "list", "reducer": "append", "default": []},
                }
            },
            "agents": {"assistant": {"model": "gpt-4o"}},
            "contracts": {
                "state": {"invariants": ["state.count >= 0"]},
            },
            "graph": {
                "entry_point": "assistant",
                "nodes": {"assistant": {"agent": "assistant"}},
                "edges": [],
            },
        }
        if with_output_contract:
            spec["contracts"]["nodes"] = {
                "assistant": {
                    "output_contract": "count_payload",
                    "produces": ["count", "route"],
                }
            }
            spec["contracts"]["outputs"] = {
                "count_payload": {
                    "type": "object",
                    "required": ["count", "route"],
                    "properties": {
                        "count": {"type": "integer"},
                        "route": {"type": "string"},
                    },
                    "additionalProperties": False,
                }
            }
        return spec

    def test_state_invariants_rendered_in_nodes(self):
        spec = BlueprintSpec.model_validate(self._spec_data(with_output_contract=False))
        files = self.gen.generate(compile_blueprint(spec))
        assert "STATE_INVARIANTS" in files["nodes.py"]
        assert "STATE_REDUCERS" in files["nodes.py"]
        assert "_check_state_invariants" in files["nodes.py"]
        assert "'state.count >= 0'" in files["nodes.py"]
        assert "lambda merged:" in files["nodes.py"]
        ast.parse(files["nodes.py"])

    def test_state_invariant_violation_emits_contract_failed(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(with_output_contract=True),
            llm_script=[{"content": '{"count": -1, "route": "x"}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="invariant-test",
            blueprint_version="1.0",
            mode="live",
        )

        with pytest.raises(ValueError, match="invariant violated"):
            module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "contract_failed"]
        failure = manifest["trace"][-1]
        assert failure["metadata"]["stage"] == "state_invariant"
        assert failure["metadata"]["contract_kind"] == "invariant"
        assert failure["metadata"]["expression"] == "state.count >= 0"

    def test_state_invariant_holds_no_event(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(with_output_contract=True),
            llm_script=[{"content": '{"count": 5, "route": "x"}'}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="invariant-test",
            blueprint_version="1.0",
            mode="live",
        )

        result = module.node_assistant({"messages": [module.HumanMessage("hello")]})

        assert result["count"] == 5
        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "node_finished"]

    def test_invariant_skips_when_field_not_yet_populated(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(with_output_contract=False),
            llm_script=[{"content": "plain response"}],
        )

        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint="invariant-test",
            blueprint_version="1.0",
            mode="live",
        )

        # count is None everywhere: "None >= 0" raises TypeError -> invariant skipped.
        module.node_assistant({"messages": [module.HumanMessage("hello")]})

        manifest = trace_mod.current_recorder().manifest
        assert [event["event"] for event in manifest["trace"]] == ["node_started", "node_finished"]

    def test_merge_state_for_invariants_is_reducer_aware(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(with_output_contract=False),
            llm_script=[{"content": "plain response"}],
        )

        merged = module._merge_state_for_invariants(
            {"items": ["a"], "count": 1, "route": "old"},
            {"items": ["b"], "count": 2, "route": None},
        )

        assert merged["items"] == ["a", "b"]  # append reducer concatenates
        assert merged["count"] == 2  # replace reducer overwrites
        assert merged["route"] == "old"  # replace reducer keeps left on None


def _load_generated_tools_module(tmp_path, monkeypatch, *, spec_data: dict, module_name: str):
    gen = LangGraphGenerator()
    spec = BlueprintSpec.model_validate(spec_data)
    files = gen.generate(compile_blueprint(spec))

    _write_trace_helper(tmp_path, files)
    _write_harness_helper(tmp_path, files)
    tools_path = tmp_path / "generated_tools.py"
    tools_path.write_text(files["tools.py"], encoding="utf-8")

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

    spec_obj = importlib.util.spec_from_file_location(module_name, tools_path)
    assert spec_obj is not None
    assert spec_obj.loader is not None
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)
    return module


class TestApprovalPolicyEnforcement:
    gen = LangGraphGenerator()

    @staticmethod
    def _spec_data(approvals: dict | None = None) -> dict:
        spec: dict = {
            "blueprint": {"name": "approval-policy-test"},
            "tools": {
                "safe_tool": {
                    "type": "function",
                    "description": "Safe operation",
                    "parameters": {"message": {"type": "string", "required": True}},
                },
                "danger_tool": {
                    "type": "function",
                    "description": "Dangerous operation",
                    "parameters": {"message": {"type": "string", "required": True}},
                },
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["safe_tool", "danger_tool"]}},
            "graph": {
                "entry_point": "assistant",
                "nodes": {"assistant": {"agent": "assistant"}},
                "edges": [],
            },
        }
        if approvals is not None:
            spec["policies"] = {"approvals": approvals}
        return spec

    @staticmethod
    def _clear_approval_env(monkeypatch):
        monkeypatch.delenv("ABP_TOOL_APPROVAL_MODE", raising=False)
        monkeypatch.delenv("ABP_APPROVED_TOOLS", raising=False)
        monkeypatch.delenv("ABP_APPROVE_TOOL_SAFE_TOOL", raising=False)
        monkeypatch.delenv("ABP_APPROVE_TOOL_DANGER_TOOL", raising=False)

    @staticmethod
    def _start_trace(blueprint: str):
        trace_mod = sys.modules["_abp_trace"]
        trace_mod.start_trace(
            run_id="run-1",
            blueprint=blueprint,
            blueprint_version="1.0",
            mode="live",
        )
        return trace_mod

    def test_approval_policy_rendered_in_tools(self):
        spec = BlueprintSpec.model_validate(
            self._spec_data({"mode": "all", "on_violation": "warn"})
        )
        files = self.gen.generate(compile_blueprint(spec))
        tools_py = files["tools.py"]
        assert "APPROVAL_POLICY = {" in tools_py
        assert "\"mode\": 'all'" in tools_py
        assert "\"on_violation\": 'warn'" in tools_py
        assert "_policy_requires_approval" in tools_py
        # The gate is now unconditional, even for tools without requires_approval.
        assert tools_py.count("per_tool_requires_approval=False") == 2
        ast.parse(tools_py)

    def test_approval_policy_all_blocks_unapproved_tool(self, tmp_path, monkeypatch):
        self._clear_approval_env(monkeypatch)
        module = _load_generated_tools_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data({"mode": "all"}),
            module_name="generated_tools_policy_all_block_test",
        )
        trace_mod = self._start_trace("approval-policy-test")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with pytest.raises(PermissionError, match="Approval required for tool 'safe_tool'"):
                module.safe_tool.invoke({"message": "hi"})

        events = [event["event"] for event in trace_mod.current_recorder().manifest["trace"]]
        assert "approval_requested" in events
        assert "approval_denied" in events
        assert events.count("approval_requested") == 1

    def test_approval_policy_warn_continues_and_emits_policy_violation(self, tmp_path, monkeypatch):
        self._clear_approval_env(monkeypatch)
        module = _load_generated_tools_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data({"mode": "all", "on_violation": "warn"}),
            module_name="generated_tools_policy_warn_test",
        )
        trace_mod = self._start_trace("approval-policy-test")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            # NotImplementedError (the stub tool body) proves execution continued
            # past the approval gate instead of raising PermissionError.
            with pytest.raises(NotImplementedError, match="safe_tool is not implemented yet"):
                module.safe_tool.invoke({"message": "hi"})

        trace = trace_mod.current_recorder().manifest["trace"]
        events = [event["event"] for event in trace]
        assert "approval_denied" in events
        violations = [event for event in trace if event["event"] == "policy_violation"]
        assert len(violations) == 1
        assert violations[0]["metadata"]["policy_kind"] == "approval"
        assert violations[0]["metadata"]["tool"] == "safe_tool"

    def test_approval_policy_selective_only_lists_named_tools(self, tmp_path, monkeypatch):
        self._clear_approval_env(monkeypatch)
        module = _load_generated_tools_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data({"mode": "selective", "tools": ["danger_tool"]}),
            module_name="generated_tools_policy_selective_test",
        )
        trace_mod = self._start_trace("approval-policy-test")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with pytest.raises(NotImplementedError):
                module.safe_tool.invoke({"message": "hi"})
            with pytest.raises(PermissionError, match="danger_tool"):
                module.danger_tool.invoke({"message": "hi"})

        trace = trace_mod.current_recorder().manifest["trace"]
        approval_events = [event for event in trace if event["event"].startswith("approval_")]
        assert all(event["tool"] == "danger_tool" for event in approval_events)
        assert any(event["event"] == "approval_requested" for event in approval_events)

    def test_approval_warn_with_env_approval_grants(self, tmp_path, monkeypatch):
        self._clear_approval_env(monkeypatch)
        monkeypatch.setenv("ABP_TOOL_APPROVAL_MODE", "allow")
        module = _load_generated_tools_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data({"mode": "all", "on_violation": "warn"}),
            module_name="generated_tools_policy_warn_granted_test",
        )
        trace_mod = self._start_trace("approval-policy-test")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with pytest.raises(NotImplementedError):
                module.safe_tool.invoke({"message": "hi"})

        events = [event["event"] for event in trace_mod.current_recorder().manifest["trace"]]
        assert "approval_granted" in events
        assert "approval_denied" not in events
        assert "policy_violation" not in events

    def test_no_policy_emits_no_approval_events_for_unprotected_tool(self, tmp_path, monkeypatch):
        self._clear_approval_env(monkeypatch)
        module = _load_generated_tools_module(
            tmp_path,
            monkeypatch,
            spec_data=self._spec_data(None),
            module_name="generated_tools_policy_absent_test",
        )
        trace_mod = self._start_trace("approval-policy-test")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with pytest.raises(NotImplementedError):
                module.safe_tool.invoke({"message": "hi"})

        events = [event["event"] for event in trace_mod.current_recorder().manifest["trace"]]
        assert not any(event.startswith("approval_") for event in events)
