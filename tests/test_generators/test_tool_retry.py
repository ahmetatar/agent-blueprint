"""Per-tool retry: validation, lint, generation and runtime behaviour."""

import importlib.util
import sys
import types

import pytest
from pydantic import ValidationError

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.tools import ToolDef


def _spec(tool: dict) -> BlueprintSpec:
    return BlueprintSpec.model_validate({
        "blueprint": {"name": "retry-test"},
        "tools": {"flaky": {"type": "function", "description": "flaky", **tool}},
        "agents": {"assistant": {"model": "gpt-4o", "tools": ["flaky"]}},
        "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
    })


class TestValidation:
    @pytest.mark.parametrize("side_effect", ["write", "irreversible"])
    def test_retry_on_non_idempotent_mutation_rejected(self, side_effect):
        with pytest.raises(ValidationError, match="unsafe-retry"):
            ToolDef.model_validate({
                "type": "function",
                "side_effect": side_effect,
                "retry": {"max_attempts": 3},
            })

    def test_idempotent_mutation_may_retry(self):
        tool = ToolDef.model_validate({
            "type": "function",
            "side_effect": "write",
            "idempotent": True,
            "retry": {"max_attempts": 3},
        })
        assert tool.retry is not None and tool.retry.max_attempts == 3

    def test_single_attempt_is_not_a_retry(self):
        ToolDef.model_validate({"type": "function", "side_effect": "write", "retry": {"max_attempts": 1}})

    def test_read_tool_may_retry_without_idempotent(self):
        ToolDef.model_validate({"type": "function", "side_effect": "read", "retry": {"max_attempts": 3}})


class TestLint:
    def _lint(self, tool: dict):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "lint-test"},
            "tools": {"call_api": {"type": "api", "url": "https://x.test", **tool}},
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["call_api"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        return [f for f in lint_blueprint(spec, compile_blueprint(spec)) if f.code == "unsafe-retry"]

    def test_undeclared_post_retry_warns(self):
        findings = self._lint({"method": "POST", "retry": {"max_attempts": 3}})
        assert len(findings) == 1
        assert findings[0].location == "tools.call_api"
        assert findings[0].severity.value == "warning"

    def test_get_retry_is_fine(self):
        assert not self._lint({"method": "GET", "retry": {"max_attempts": 3}})

    def test_declared_side_effect_is_fine(self):
        assert not self._lint({"method": "POST", "side_effect": "read", "retry": {"max_attempts": 3}})

    def test_no_retry_no_finding(self):
        assert not self._lint({"method": "POST"})


class TestGeneration:
    def test_no_retry_leaves_output_unchanged(self):
        files = LangGraphGenerator().generate(compile_blueprint(_spec({})))
        assert "_retry_tool_call" not in files["tools.py"]
        assert "TOOL_RETRY_POLICY" not in files["tools.py"]


def _load_tools(tmp_path, monkeypatch, tool: dict, impl_body: str):
    spec = _spec({"impl": "flaky_impl.flaky", "parameters": {"x": {"type": "string", "required": True}}, **tool})
    files = LangGraphGenerator().generate(compile_blueprint(spec))
    (tmp_path / "_abp_trace.py").write_text(files["_abp_trace.py"], encoding="utf-8")
    (tmp_path / "_abp_harness.py").write_text(files["_abp_harness.py"], encoding="utf-8")
    (tmp_path / "flaky_impl.py").write_text(impl_body, encoding="utf-8")
    (tmp_path / "generated_tools.py").write_text(files["tools.py"], encoding="utf-8")

    fake_core = types.ModuleType("langchain_core")
    fake_tools = types.ModuleType("langchain_core.tools")

    class FakeTool:
        def __init__(self, func, name=None, description=None):
            self.func, self.name = func, name or func.__name__

        def invoke(self, args):
            return self.func(**args)

    class StructuredTool:
        @classmethod
        def from_function(cls, func, name=None, description=None):
            return FakeTool(func, name=name)

    fake_tools.tool = lambda f: FakeTool(f)
    fake_tools.StructuredTool = StructuredTool
    fake_core.tools = fake_tools
    monkeypatch.setitem(sys.modules, "langchain_core", fake_core)
    monkeypatch.setitem(sys.modules, "langchain_core.tools", fake_tools)
    for name in ("_abp_trace", "_abp_harness", "flaky_impl"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("ABP_TOOL_MODE", raising=False)

    spec_obj = importlib.util.spec_from_file_location("generated_tools_retry_test", tmp_path / "generated_tools.py")
    assert spec_obj is not None and spec_obj.loader is not None
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[spec_obj.name] = module
    spec_obj.loader.exec_module(module)
    sys.modules["_abp_trace"].start_trace(
        run_id="r", blueprint="retry-test", blueprint_version="1.0", mode="mock"
    )
    return module


def _events(name: str) -> list[dict]:
    manifest = sys.modules["_abp_trace"].current_recorder().manifest
    return [e for e in manifest["trace"] if e["event"] == name]


FLAKY_THEN_OK = """
CALLS = []

def flaky(x):
    CALLS.append(x)
    if len(CALLS) < 3:
        raise RuntimeError("boom")
    return "ok"
"""

ALWAYS_FAILS = """
CALLS = []

def flaky(x):
    CALLS.append(x)
    raise RuntimeError("boom")
"""


class TestRuntime:
    def test_retries_until_success(self, tmp_path, monkeypatch):
        module = _load_tools(tmp_path, monkeypatch, {"retry": {"max_attempts": 3}}, FLAKY_THEN_OK)
        assert module.flaky.invoke({"x": "a"}) == "ok"
        assert len(sys.modules["flaky_impl"].CALLS) == 3
        scheduled = _events("retry_scheduled")
        assert [e["tool"] for e in scheduled] == ["flaky", "flaky"]
        assert scheduled[0]["metadata"]["next_attempt"] == 2
        assert not _events("retry_exhausted")
        assert not _events("tool_failed")

    def test_exhaustion_raises_and_traces(self, tmp_path, monkeypatch):
        module = _load_tools(tmp_path, monkeypatch, {"retry": {"max_attempts": 2}}, ALWAYS_FAILS)
        with pytest.raises(RuntimeError, match="boom"):
            module.flaky.invoke({"x": "a"})
        assert len(sys.modules["flaky_impl"].CALLS) == 2
        assert len(_events("retry_scheduled")) == 1
        exhausted = _events("retry_exhausted")
        assert len(exhausted) == 1 and exhausted[0]["tool"] == "flaky"
        assert len(_events("tool_failed")) == 1

    def test_approval_requested_once_across_attempts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABP_APPROVED_TOOLS", "flaky")
        module = _load_tools(
            tmp_path,
            monkeypatch,
            {"requires_approval": True, "retry": {"max_attempts": 3}},
            FLAKY_THEN_OK,
        )
        assert module.flaky.invoke({"x": "a"}) == "ok"
        assert len(_events("approval_requested")) == 1
        assert len(_events("tool_called")) == 1

    def test_tool_without_retry_runs_once(self, tmp_path, monkeypatch):
        module = _load_tools(tmp_path, monkeypatch, {}, ALWAYS_FAILS)
        with pytest.raises(RuntimeError):
            module.flaky.invoke({"x": "a"})
        assert len(sys.modules["flaky_impl"].CALLS) == 1


class TestOtherToolTypes:
    def test_api_and_retrieval_tools_generate_valid_python(self):
        import ast

        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "retry-types"},
            "retrievers": {"kb": {"type": "vector", "impl": "kb_impl.search"}},
            "tools": {
                "call_api": {
                    "type": "api",
                    "url": "https://x.test",
                    "method": "GET",
                    "retry": {"max_attempts": 3, "backoff_seconds": 0.1},
                },
                "search_kb": {"type": "retrieval", "retriever": "kb", "retry": {"max_attempts": 2}},
            },
            "agents": {"assistant": {"model": "gpt-4o", "tools": ["call_api", "search_kb"]}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
        })
        tools_py = LangGraphGenerator().generate(compile_blueprint(spec))["tools.py"]
        ast.parse(tools_py)
        assert tools_py.count("_retry_tool_call(") == 3  # def + 2 call sites
        assert '"call_api": {' in tools_py and '"backoff_seconds": 0.1' in tools_py
