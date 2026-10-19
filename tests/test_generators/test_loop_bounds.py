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
