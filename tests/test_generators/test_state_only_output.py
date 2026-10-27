"""`contracts.nodes.<id>.append_to_messages: false` keeps a node's output out of the conversation.

A router/triage node's raw JSON reply would otherwise become the conversation's
last assistant turn, and the next agent (notably small local models) answers it
with an empty reply. The runtime tests run the generated graph on the real
LangGraph runtime (skipped where it is not installed).
"""

import copy
import json
import os
import subprocess
import sys

import pytest

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec

TRIAGE_JSON = '{"intent": "refund", "confidence": 0.9}'
DESK_REPLY = "Which order is it?"


def _spec(*, append_to_messages: bool | None = False, verify: bool = False) -> dict:
    triage_contract: dict = {"produces": ["intent", "confidence"], "output_contract": "triage_result"}
    if append_to_messages is not None:
        triage_contract["append_to_messages"] = append_to_messages
    triage_node: dict = {"agent": "classifier"}
    if verify:
        triage_node["verify"] = {"functions": ["checks.reply_is_visible"]}
    return {
        "blueprint": {"name": "state-only-output"},
        "state": {
            "fields": {
                "messages": {"type": "list[message]", "reducer": "append"},
                "intent": {"type": "string"},
                "confidence": {"type": "number"},
            }
        },
        "contracts": {
            "nodes": {"triage": triage_contract},
            "outputs": {
                "triage_result": {
                    "type": "object",
                    "required": ["intent", "confidence"],
                    "properties": {"intent": {"type": "string"}, "confidence": {"type": "number"}},
                }
            },
        },
        "agents": {
            "classifier": {"model": "gpt-4o", "system_prompt": "Classify. JSON only."},
            "desk": {"model": "gpt-4o", "system_prompt": "Help the customer."},
        },
        "graph": {
            "entry_point": "triage",
            "nodes": {"triage": triage_node, "refund_desk": {"agent": "desk"}},
            "edges": [{"from": "triage", "to": "refund_desk"}, {"from": "refund_desk", "to": "END"}],
        },
    }


def _generate(raw: dict) -> dict[str, str]:
    spec = BlueprintSpec.model_validate(copy.deepcopy(raw))
    return LangGraphGenerator().generate(compile_blueprint(spec))


class TestValidation:
    def test_defaults_to_appending(self):
        spec = BlueprintSpec.model_validate(_spec(append_to_messages=None))
        assert spec.contracts is not None
        assert spec.contracts.nodes["triage"].append_to_messages is True

    def test_requires_output_contract(self):
        raw = _spec()
        del raw["contracts"]["nodes"]["triage"]["output_contract"]
        with pytest.raises(ValueError, match="requires output_contract and produces"):
            BlueprintSpec.model_validate(raw)

    def test_requires_produces(self):
        raw = _spec()
        del raw["contracts"]["nodes"]["triage"]["produces"]
        with pytest.raises(ValueError, match="requires output_contract and produces"):
            BlueprintSpec.model_validate(raw)

    def test_rejects_non_agent_node(self):
        raw = _spec()
        raw["graph"]["nodes"]["refund_desk"] = {"type": "handoff", "channel": "console"}
        raw["contracts"]["nodes"]["refund_desk"] = {
            "produces": ["intent"],
            "output_contract": "triage_result",
            "append_to_messages": False,
        }
        with pytest.raises(ValueError, match="only supported on agent nodes"):
            BlueprintSpec.model_validate(raw)


class TestGeneration:
    def test_unset_flag_leaves_generated_code_unchanged(self):
        assert _generate(_spec(append_to_messages=None)) == _generate(_spec(append_to_messages=True))
        assert "_without_messages" not in _generate(_spec(append_to_messages=None))["nodes.py"]

    def test_wraps_only_the_flagged_node(self):
        nodes_py = _generate(_spec())["nodes.py"]
        assert 'node_triage = _without_messages(\n    "messages", node_triage\n)' in nodes_py
        assert "node_refund_desk = _without_messages" not in nodes_py


def _langgraph_available() -> bool:
    probe = subprocess.run([sys.executable, "-c", "import langgraph.graph"], capture_output=True)
    return probe.returncode == 0


def _run(tmp_path, raw: dict, triage_output: str = TRIAGE_JSON) -> dict:
    # Subprocess: other tests install fake `langgraph.*` modules in-process.
    if not _langgraph_available():
        pytest.skip("langgraph is not installed")
    for name, content in _generate(raw).items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "checks.py").write_text(
        "def reply_is_visible(output, state):\n"
        "    return '\"intent\"' in output or f'verify saw {output!r}'\n",
        encoding="utf-8",
    )
    driver = (
        "import json\nfrom langchain_core.messages import HumanMessage\nfrom graph import graph\n"
        'result = graph.invoke({"messages": [HumanMessage("my order is broken")]},'
        ' {"configurable": {"thread_id": "t"}})\n'
        'print(json.dumps({"messages": [[m.type, m.content] for m in result["messages"]],'
        ' "intent": result.get("intent")}))\n'
    )
    fixtures = {"llm_outputs": {"triage": [{"content": triage_output}], "refund_desk": [{"content": DESK_REPLY}]}}
    env = {**os.environ, "ABP_LLM_MODE": "mock", "ABP_HARNESS_FIXTURES": json.dumps(fixtures)}
    done = subprocess.run(
        [sys.executable, "-c", driver], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


class TestRuntime:
    def test_flagged_output_reaches_state_but_not_the_conversation(self, tmp_path):
        result = _run(tmp_path, _spec())
        assert result["intent"] == "refund"
        assert result["messages"] == [["human", "my order is broken"], ["ai", DESK_REPLY]]

    def test_default_keeps_the_output_in_the_conversation(self, tmp_path):
        result = _run(tmp_path, _spec(append_to_messages=None))
        assert result["messages"] == [
            ["human", "my order is broken"],
            ["ai", TRIAGE_JSON],
            ["ai", DESK_REPLY],
        ]

    def test_verification_still_sees_the_reply(self, tmp_path):
        # The wrapper is outermost: verify checks run before the reply is dropped.
        result = _run(tmp_path, _spec(verify=True))
        assert result["messages"] == [["human", "my order is broken"], ["ai", DESK_REPLY]]

    def test_contract_still_validates_the_dropped_reply(self, tmp_path):
        with pytest.raises(AssertionError, match="output contract"):
            _run(tmp_path, _spec(), triage_output='{"intent": "refund"}')
