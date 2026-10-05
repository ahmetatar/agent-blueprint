"""Conditional-edge routers must see a node's undeclared same-superstep signals.

LangGraph builds a router's input from its first parameter's type annotation: a
router annotated with the state TypedDict only sees declared channels, so the
ephemeral `__abp_*` routing keys are dropped and the reroute never fires. The
generated routers therefore stay unannotated. These tests run the generated
graph on the real LangGraph runtime (skipped where it is not installed).
"""

import copy
import json
import subprocess
import sys

import pytest

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec

ESCALATION_KEYS = '{"__abp_escalation_target": "handoff_review", "__abp_escalation_source": "assistant"}'


def _spec(*, conditional: bool) -> dict:
    edges: list = [
        {"from": "handoff_review", "to": "END"},
        {"from": "other", "to": "END"},
    ]
    edges.append(
        {"from": "assistant", "to": [{"condition": "state.go == true", "target": "other"}, {"default": "END"}]}
        if conditional
        else {"from": "assistant", "to": "END"}
    )
    return {
        "blueprint": {"name": "router-signals"},
        "state": {"fields": {"trail": {"type": "array", "reducer": "append"}, "go": {"type": "boolean"}}},
        "agents": {"w": {"model": "gpt-4o"}},
        "graph": {
            "entry_point": "assistant",
            "nodes": {
                "assistant": {"agent": "w"},
                "other": {"agent": "w"},
                "handoff_review": {"type": "handoff", "channel": "console"},
            },
            "edges": edges,
        },
        "policies": {"escalation": {"on_low_confidence": "handoff_review", "confidence_threshold": 0.75}},
    }


def _langgraph_available() -> bool:
    probe = subprocess.run([sys.executable, "-c", "import langgraph.graph"], capture_output=True)
    return probe.returncode == 0


def _run(tmp_path, raw: dict, assistant_update: str, state: dict) -> dict:
    # Subprocess: other tests install fake `langgraph.*` modules in-process.
    if not _langgraph_available():
        pytest.skip("langgraph is not installed")
    files = LangGraphGenerator().generate(compile_blueprint(BlueprintSpec.model_validate(copy.deepcopy(raw))))
    (tmp_path / "state.py").write_text(files["state.py"], encoding="utf-8")
    (tmp_path / "graph.py").write_text(files["graph.py"], encoding="utf-8")
    (tmp_path / "nodes.py").write_text(
        f"def node_assistant(state):\n    return {assistant_update}\n\n"
        'def node_other(state):\n    return {"trail": ["other"]}\n\n'
        'def node_handoff_review(state):\n    return {"trail": ["handoff"]}\n',
        encoding="utf-8",
    )
    driver = (
        "import json\nfrom graph import graph\n"
        f"state = {state!r}\n"
        'result = graph.invoke({"trail": [], **state}, {"configurable": {"thread_id": "t"}})\n'
        "print(json.dumps(result))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", driver], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


@pytest.mark.parametrize("conditional", [False, True])
def test_low_confidence_escalation_reroutes_on_real_langgraph(tmp_path, conditional):
    update = f'{{"trail": ["assistant"], **{ESCALATION_KEYS}}}'
    result = _run(tmp_path, _spec(conditional=conditional), update, {"go": True})
    assert result["trail"] == ["assistant", "handoff"]


def test_no_escalation_signal_keeps_normal_routing(tmp_path):
    result = _run(tmp_path, _spec(conditional=False), '{"trail": ["assistant"]}', {})
    assert result["trail"] == ["assistant"]


def test_conditions_on_declared_state_still_route(tmp_path):
    spec = _spec(conditional=True)
    taken = _run(tmp_path, spec, '{"trail": ["assistant"]}', {"go": True})
    assert taken["trail"] == ["assistant", "other"]
    skipped = _run(tmp_path, spec, '{"trail": ["assistant"]}', {"go": False})
    assert skipped["trail"] == ["assistant"]
