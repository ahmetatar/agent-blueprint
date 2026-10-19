"""Inner ReAct loops must terminate: tool-policy violations and max_turns.

The runtime tests execute the generated project on real LangGraph through
the harness (skipped where LangGraph is not installed).
"""

import subprocess
import sys

import pytest

from agent_blueprint.harness_runner import run_harness_scenario
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def _require_langgraph() -> None:
    probe = subprocess.run([sys.executable, "-c", "import langgraph.graph"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("langgraph is not installed")


def _looping_spec(**policies) -> dict:
    """An agent whose mocked model asks for the same tool on every turn."""
    return {
        "blueprint": {"name": "loop"},
        "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
        "agents": {"worker": {"model": "gpt-4o", "tools": ["lookup"]}},
        "tools": {
            "lookup": {
                "type": "function",
                "impl": "lookup_impl.lookup",
                "description": "Look up a record",
                "parameters": {"record_id": {"type": "string", "required": True}},
            }
        },
        "graph": {
            "entry_point": "worker",
            "nodes": {"worker": {"agent": "worker"}},
            "edges": [{"from": "worker", "to": "END"}],
        },
        "policies": policies,
    }


def _run(tmp_path, raw: dict, turns: int = 6):
    _require_langgraph()
    (tmp_path / "lookup_impl.py").write_text("def lookup(record_id):\n    return 'ok'\n")
    call = {"tool_calls": [{"name": "lookup", "args": {"record_id": "1"}}]}
    raw["harness"] = {
        "scenarios": [
            {
                "id": "loop",
                "input": {"message": "go"},
                "fixtures": {
                    "llm_outputs": {"worker": [call] * turns + [{"content": "Done."}]},
                    "tool_outputs": {"lookup": "ok"},
                },
            }
        ]
    }
    ir = compile_blueprint(BlueprintSpec.model_validate(raw))
    return run_harness_scenario(ir, ir.harness.scenarios[0], install=False, source_dir=tmp_path)


def _events(result, name: str) -> list[dict]:
    assert result.trace_manifest is not None, result.stderr
    return [e for e in result.trace_manifest["trace"] if e.get("event") == name]


class TestToolPolicyStopsRun:
    def test_max_calls_per_node_fails_fast(self, tmp_path):
        result = _run(tmp_path, _looping_spec(tool_usage={"max_calls_per_node": 2}))
        assert not result.passed
        assert len(_events(result, "tool_called")) == 2
        violations = _events(result, "policy_violation")
        assert len(violations) == 1
        assert violations[0]["metadata"]["policy_kind"] == "max_calls_per_node"


def _with_turns(raw: dict, *, max_turns: int | None = None, on_max_turns: str | None = None):
    agent = raw["agents"]["worker"]
    if max_turns is not None:
        agent["max_turns"] = max_turns
    if on_max_turns is not None:
        agent["on_max_turns"] = on_max_turns
    return raw


def _generate_nodes(raw: dict) -> str:
    from agent_blueprint.generators.langgraph import LangGraphGenerator

    return LangGraphGenerator().generate(compile_blueprint(BlueprintSpec.model_validate(raw)))[
        "nodes.py"
    ]


class TestMaxTurnsModel:
    def test_must_be_positive(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate(_with_turns(_looping_spec(), max_turns=0))

    def test_unset_generates_no_turn_limit(self):
        nodes_py = _generate_nodes(_looping_spec())
        assert "MAX_TURNS_BY_NODE" not in nodes_py
        assert "_max_turns_reached" not in nodes_py

    def test_settings_default_applies_to_tool_agents(self):
        raw = _looping_spec()
        raw["settings"] = {"default_max_turns": 4}
        nodes_py = _generate_nodes(raw)
        assert '"worker": {\n        "max_turns": 4,\n        "on_exhausted": "fail",' in nodes_py

    def test_agent_value_overrides_settings_default(self):
        raw = _with_turns(_looping_spec(), max_turns=2, on_max_turns="finish")
        raw["settings"] = {"default_max_turns": 9}
        nodes_py = _generate_nodes(raw)
        assert '"max_turns": 2,' in nodes_py
        assert '"on_exhausted": "finish",' in nodes_py


class TestMaxTurnsRuntime:
    def test_fail_stops_the_run_deterministically(self, tmp_path):
        result = _run(tmp_path, _with_turns(_looping_spec(), max_turns=3))
        assert not result.passed
        assert len(_events(result, "tool_called")) == 2
        exhausted = _events(result, "max_turns_exhausted")
        assert len(exhausted) == 1
        assert exhausted[0]["node"] == "worker"
        assert exhausted[0]["metadata"] == {"max_turns": 3, "turns": 3, "on_exhausted": "fail"}
        assert "max_turns=3" in result.stderr

    def test_finish_ends_the_node_and_continues(self, tmp_path):
        result = _run(tmp_path, _with_turns(_looping_spec(), max_turns=3, on_max_turns="finish"))
        assert result.passed, (result.failures, result.stderr)
        assert len(_events(result, "tool_called")) == 2
        assert _events(result, "max_turns_exhausted")[0]["metadata"]["on_exhausted"] == "finish"
        assert [e["node"] for e in _events(result, "node_finished")] == ["worker"]

    def test_supervisor_tool_loop_is_bounded(self, tmp_path):
        _require_langgraph()
        (tmp_path / "lookup_impl.py").write_text("def lookup(record_id):\n    return 'ok'\n")
        raw = _looping_spec()
        raw["agents"]["helper"] = {"model": "gpt-4o"}
        raw["agents"]["worker"]["max_turns"] = 2
        raw["graph"] = {
            "entry_point": "lead",
            "nodes": {
                "lead": {"type": "supervisor", "agent": "worker", "workers": ["aide"]},
                "aide": {"agent": "helper", "description": "Helper"},
            },
            "edges": [],
        }
        call = {"tool_calls": [{"name": "lookup", "args": {"record_id": "1"}}]}
        raw["harness"] = {
            "scenarios": [
                {
                    "id": "loop",
                    "input": {"message": "go"},
                    "fixtures": {
                        "llm_outputs": {"lead": [call] * 5 + [{"content": "Done."}]},
                        "tool_outputs": {"lookup": "ok"},
                    },
                }
            ]
        }
        ir = compile_blueprint(BlueprintSpec.model_validate(raw))
        result = run_harness_scenario(
            ir, ir.harness.scenarios[0], install=False, source_dir=tmp_path
        )
        assert not result.passed
        assert len(_events(result, "tool_called")) == 1
        assert _events(result, "max_turns_exhausted")[0]["node"] == "lead"


def _tool_loop_findings(raw: dict) -> list:
    from agent_blueprint.linting import lint_blueprint

    spec = BlueprintSpec.model_validate(raw)
    return [f for f in lint_blueprint(spec, compile_blueprint(spec)) if f.code == "unbounded-tool-loop"]


class TestUnboundedToolLoopLint:
    def test_warns_when_nothing_bounds_the_loop(self):
        findings = _tool_loop_findings(_looping_spec())
        assert [(f.severity.value, f.location) for f in findings] == [
            ("warning", "graph.nodes.worker")
        ]

    @pytest.mark.parametrize(
        "bound",
        [
            {"agent": {"max_turns": 5}},
            {"settings": {"default_max_turns": 5}},
            {"policies": {"tool_usage": {"max_calls_per_node": 3}}},
            {"policies": {"tool_usage": {"max_calls_per_run": 3}}},
            {"policies": {"budgets": {"max_tokens_per_run": 1000}}},
            {"policies": {"budgets": {"max_cost_usd": 0.5}}},
        ],
    )
    def test_any_bound_silences_it(self, bound):
        raw = _looping_spec(**bound.get("policies", {}))
        raw["agents"]["worker"].update(bound.get("agent", {}))
        if "settings" in bound:
            raw["settings"] = bound["settings"]
        assert _tool_loop_findings(raw) == []

    def test_agents_without_tools_are_not_flagged(self):
        raw = _looping_spec()
        raw["agents"]["worker"]["tools"] = []
        del raw["tools"]
        assert _tool_loop_findings(raw) == []
