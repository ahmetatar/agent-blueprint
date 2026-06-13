"""The harness '*' default fixture: one entry covers any node/tool without its own."""

import importlib.util
import sys
import types

import pytest

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def _stub_langchain_messages(monkeypatch):
    """invoke() lazily imports AIMessage from langchain_core, which is a
    generated-project dep not installed in CI. Stub it so the test exercises the
    fixture-selection logic without the real package."""
    class AIMessage:
        def __init__(self, content="", tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []
            self.usage_metadata = None

    fake_pkg = types.ModuleType("langchain_core")
    fake_msgs = types.ModuleType("langchain_core.messages")
    fake_msgs.AIMessage = AIMessage
    monkeypatch.setitem(sys.modules, "langchain_core", fake_pkg)
    monkeypatch.setitem(sys.modules, "langchain_core.messages", fake_msgs)


def _load_harness(tmp_path, monkeypatch):
    spec = BlueprintSpec.model_validate({
        "blueprint": {"name": "harness-default-test"},
        "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
        "agents": {"a": {"model": "gpt-4o"}},
        "graph": {"entry_point": "a", "nodes": {"a": {"agent": "a"}}, "edges": []},
    })
    files = LangGraphGenerator().generate(compile_blueprint(spec))
    (tmp_path / "_abp_trace.py").write_text(files["_abp_trace.py"], encoding="utf-8")
    (tmp_path / "_abp_harness.py").write_text(files["_abp_harness.py"], encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "_abp_trace", raising=False)
    monkeypatch.delitem(sys.modules, "_abp_harness", raising=False)
    spec_obj = importlib.util.spec_from_file_location("_abp_harness", tmp_path / "_abp_harness.py")
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules["_abp_harness"] = module
    spec_obj.loader.exec_module(module)
    return module


class TestDefaultLlmFixture:
    def test_star_covers_unmocked_node_and_specific_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABP_LLM_MODE", "mock")
        _stub_langchain_messages(monkeypatch)
        m = _load_harness(tmp_path, monkeypatch)
        m._HARNESS_FIXTURES_CACHE = {
            "llm_outputs": {"*": [{"content": "DEFAULT"}], "special": [{"content": "SPECIAL"}]},
            "tool_outputs": {},
        }
        # An unmocked node does not raise and resolves via "*".
        assert m.build_llm("some__namespaced__node", lambda: None).invoke([]).content == "DEFAULT"
        # A node with its own fixture takes precedence over the default.
        assert m.build_llm("special", lambda: None).invoke([]).content == "SPECIAL"

    def test_without_default_unmocked_node_still_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABP_LLM_MODE", "mock")
        m = _load_harness(tmp_path, monkeypatch)
        m._HARNESS_FIXTURES_CACHE = {"llm_outputs": {"special": [{"content": "x"}]}, "tool_outputs": {}}
        with pytest.raises(RuntimeError, match="No mocked LLM fixture"):
            m.build_llm("unmocked", lambda: None)


class TestDefaultToolFixture:
    def test_star_covers_unstubbed_tool_and_specific_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABP_TOOL_MODE", "stub")
        m = _load_harness(tmp_path, monkeypatch)
        monkeypatch.setattr(m, "record_tool_output", lambda *a, **k: None)
        m._HARNESS_FIXTURES_CACHE = {
            "llm_outputs": {},
            "tool_outputs": {"*": {"ok": True}, "named": {"v": 1}},
        }
        assert m.consume_tool_output("anything", {}) == {"ok": True}
        assert m.consume_tool_output("named", {}) == {"v": 1}

    def test_without_default_unstubbed_tool_still_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABP_TOOL_MODE", "stub")
        m = _load_harness(tmp_path, monkeypatch)
        m._HARNESS_FIXTURES_CACHE = {"llm_outputs": {}, "tool_outputs": {"named": {}}}
        with pytest.raises(RuntimeError, match="No stubbed tool fixture"):
            m.consume_tool_output("anything", {})
