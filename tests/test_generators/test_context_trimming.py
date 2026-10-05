"""Agent memory (conversation_buffer) context trimming — generation and runtime."""

import sys

import pytest
from pydantic import ValidationError

from agent_blueprint.cli.generate import TargetFramework
from agent_blueprint.doctoring import DoctorSeverity, doctor_blueprint
from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from tests.test_generators.test_langgraph import _load_generated_nodes_module


def _spec(memory: dict | None, **agent_extra) -> dict:
    agent: dict = {"model": "gpt-4o", **agent_extra}
    if memory is not None:
        agent["memory"] = memory
    return {
        "blueprint": {"name": "ctx-test"},
        "agents": {"assistant": agent},
        "graph": {
            "entry_point": "assistant",
            "nodes": {"assistant": {"agent": "assistant"}},
            "edges": [],
        },
    }


def _generate(memory: dict | None) -> dict[str, str]:
    return LangGraphGenerator().generate(compile_blueprint(BlueprintSpec.model_validate(_spec(memory))))


def _run_node(tmp_path, monkeypatch, memory: dict, build_messages):
    """Run the node once; return (messages the LLM saw, trace events, module)."""
    module = _load_generated_nodes_module(
        tmp_path, monkeypatch, spec_data=_spec(memory), llm_script=[{"content": "ok"}]
    )
    seen: list[list] = []
    fake_llm = sys.modules["langchain_openai"].ChatOpenAI
    original = fake_llm.invoke

    def spy(self, working):
        seen.append(list(working))
        return original(self, working)

    monkeypatch.setattr(fake_llm, "invoke", spy)
    trace_mod = sys.modules["_abp_trace"]
    trace_mod.start_trace(run_id="r", blueprint="ctx-test", blueprint_version="1.0", mode="mock")
    state = {"messages": build_messages(module)}
    result = module.node_assistant(state)
    events = trace_mod.current_recorder().manifest["trace"]
    return seen[0], events, state, result


def _human(module, n: int) -> list:
    return [module.HumanMessage(f"m{i}") for i in range(n)]


class TestMemoryValidation:
    @pytest.mark.parametrize("field", ["max_messages", "max_tokens"])
    def test_non_positive_limits_rejected(self, field):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate(_spec({field: 0}))

    @pytest.mark.parametrize("kind", ["summary", "vector"])
    def test_unsupported_types_fail_generation(self, kind):
        with pytest.raises(GeneratorError, match=f"node assistant \\({kind}\\)"):
            _generate({"type": kind, "max_messages": 5})

    @pytest.mark.parametrize("kind", ["summary", "vector"])
    def test_unsupported_types_flagged_by_doctor(self, kind):
        spec = BlueprintSpec.model_validate(_spec({"type": kind}))
        findings = doctor_blueprint(spec, compile_blueprint(spec), target=TargetFramework.langgraph)
        assert any(
            f.code == "target-incompatible-feature" and f.severity == DoctorSeverity.error
            and "assistant" in f.message
            for f in findings
        )


class TestGeneration:
    def test_no_memory_output_has_no_context_policy(self):
        nodes_py = _generate(None)["nodes.py"]
        assert "CONTEXT_POLICY_BY_NODE" not in nodes_py
        assert "_apply_context_policy" not in nodes_py

    def test_memory_renders_policy_and_call_site(self):
        nodes_py = _generate({"max_messages": 4, "max_tokens": 100})["nodes.py"]
        assert '"max_messages": 4' in nodes_py
        assert '"max_tokens": 100' in nodes_py
        assert "llm.invoke(_apply_context_policy(node_id, working))" in nodes_py


class TestRuntimeTrimming:
    def test_max_messages_trims_view_only(self, tmp_path, monkeypatch):
        seen, events, state, result = _run_node(
            tmp_path, monkeypatch, {"max_messages": 3}, lambda m: _human(m, 8)
        )
        assert [m.content for m in seen] == ["m5", "m6", "m7"]
        # checkpointed state and node output are untouched
        assert len(state["messages"]) == 8
        assert [m.content for m in result["messages"]] == ["ok"]
        trimmed = [e for e in events if e["event"] == "context_trimmed"]
        assert len(trimmed) == 1
        assert trimmed[0]["metadata"]["dropped_messages"] == 5

    def test_under_limit_no_trim_no_event(self, tmp_path, monkeypatch):
        seen, events, _, _ = _run_node(
            tmp_path, monkeypatch, {"max_messages": 10}, lambda m: _human(m, 3)
        )
        assert len(seen) == 3
        assert not [e for e in events if e["event"] == "context_trimmed"]

    def test_system_prompt_kept_and_not_counted_as_message(self, tmp_path, monkeypatch):
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data=_spec({"max_messages": 2}, system_prompt="Be brief."),
            llm_script=[{"content": "ok"}],
        )
        seen: list[list] = []
        fake_llm = sys.modules["langchain_openai"].ChatOpenAI
        original = fake_llm.invoke
        monkeypatch.setattr(
            fake_llm, "invoke", lambda self, w: (seen.append(list(w)), original(self, w))[1]
        )
        sys.modules["_abp_trace"].start_trace(
            run_id="r", blueprint="ctx-test", blueprint_version="1.0", mode="mock"
        )
        module.node_assistant({"messages": _human(module, 5)})
        assert [m.content for m in seen[0]] == ["Be brief.", "m3", "m4"]

    def test_max_tokens_trims_oldest(self, tmp_path, monkeypatch):
        # ~4 chars/token: each 40-char message ≈ 10 tokens; budget 25 keeps two.
        seen, _, _, _ = _run_node(
            tmp_path,
            monkeypatch,
            {"max_tokens": 25},
            lambda m: [m.HumanMessage(f"{i}" * 40) for i in range(5)],
        )
        assert [m.content[0] for m in seen] == ["3", "4"]

    def test_latest_message_kept_even_if_over_budget(self, tmp_path, monkeypatch):
        seen, _, _, _ = _run_node(
            tmp_path, monkeypatch, {"max_tokens": 1}, lambda m: [m.HumanMessage("x" * 400)]
        )
        assert len(seen) == 1

    def test_tool_call_pair_never_split_and_window_opens_on_human(self, tmp_path, monkeypatch):
        def build(m):
            ai_message = sys.modules["langchain_core.messages"].AIMessage
            return [
                m.HumanMessage("old question"),
                ai_message("", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
                m.ToolMessage("result", "1"),
                m.HumanMessage("new question"),
            ]

        # limit 2 would otherwise keep [tool, human] (orphaned ToolMessage)
        seen, _, _, _ = _run_node(tmp_path, monkeypatch, {"max_messages": 2}, build)
        assert [m.content for m in seen] == ["new question"]


def _tool_turns(m, results: list[str]) -> list:
    """Human, then one (assistant tool call, tool result) pair per result."""
    ai_message = sys.modules["langchain_core.messages"].AIMessage
    msgs: list = [m.HumanMessage("go")]
    for i, content in enumerate(results):
        msgs.append(ai_message("", tool_calls=[{"name": "t", "args": {}, "id": str(i)}]))
        msgs.append(m.ToolMessage(content, str(i)))
    return msgs


class TestToolOutputCompaction:
    @pytest.mark.parametrize("field", ["max_tool_result_chars", "keep_recent_tool_results"])
    def test_non_positive_limits_rejected(self, field):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate(_spec({field: 0}))

    def test_memory_with_only_compaction_fields_renders_policy(self):
        nodes_py = _generate({"max_tool_result_chars": 50})["nodes.py"]
        assert '"max_tool_result_chars": 50' in nodes_py
        assert "_compact_tool_results" in nodes_py

    def test_oversized_result_truncated_head_and_tail_state_untouched(self, tmp_path, monkeypatch):
        big = "A" * 100 + "Z" * 100
        seen, events, state, _ = _run_node(
            tmp_path,
            monkeypatch,
            {"max_tool_result_chars": 20},
            lambda m: _tool_turns(m, [big]),
        )
        tool_view = [x for x in seen if getattr(x, "type", None) == "tool"][0]
        assert tool_view.content.startswith("A" * 10)
        assert tool_view.content.endswith("Z" * 10)
        assert "180 chars omitted" in tool_view.content
        # checkpointed message is the original object, still full size
        assert [x for x in state["messages"] if getattr(x, "type", None) == "tool"][0].content == big
        compacted = [e for e in events if e["event"] == "context_compacted"]
        assert len(compacted) == 1
        assert compacted[0]["metadata"]["compacted_results"] == 1
        assert compacted[0]["metadata"]["chars_saved"] > 0
        assert not [e for e in events if e["event"] == "context_trimmed"]
        assert big not in str(events)  # hashes/sizes only, never content

    def test_old_results_stubbed_recent_kept(self, tmp_path, monkeypatch):
        results = ["first-" + "x" * 80, "second-" + "y" * 80, "third-" + "z" * 80]
        seen, events, _, _ = _run_node(
            tmp_path,
            monkeypatch,
            {"keep_recent_tool_results": 1},
            lambda m: _tool_turns(m, results),
        )
        tool_view = [x.content for x in seen if getattr(x, "type", None) == "tool"]
        assert tool_view[0].startswith("[tool result compacted: 86 chars, sha256:")
        assert tool_view[1].startswith("[tool result compacted:")
        assert tool_view[2] == results[2]
        # tool-call/result pairing preserved: same message count and order
        assert len(seen) == 7
        event = [e for e in events if e["event"] == "context_compacted"][0]
        assert event["metadata"]["compacted_results"] == 2

    def test_stub_not_used_when_larger_than_content(self, tmp_path, monkeypatch):
        seen, events, _, _ = _run_node(
            tmp_path,
            monkeypatch,
            {"keep_recent_tool_results": 1},
            lambda m: _tool_turns(m, ["ok", "fine"]),
        )
        assert [x.content for x in seen if getattr(x, "type", None) == "tool"] == ["ok", "fine"]
        assert not [e for e in events if e["event"] == "context_compacted"]

    def test_compaction_shrinks_budget_before_window_trim(self, tmp_path, monkeypatch):
        # Without compaction the 400-char result (~100 tokens) would blow the
        # 60-token budget and evict the whole tool exchange.
        seen, events, _, _ = _run_node(
            tmp_path,
            monkeypatch,
            {"max_tokens": 60, "max_tool_result_chars": 40},
            lambda m: _tool_turns(m, ["q" * 400]),
        )
        assert len(seen) == 3
        assert not [e for e in events if e["event"] == "context_trimmed"]
        assert [e for e in events if e["event"] == "context_compacted"]
