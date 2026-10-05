"""verify: bounded self-check loop on agent nodes."""

import sys

import pytest
from pydantic import ValidationError

from agent_blueprint.doctoring import DoctorSeverity, doctor_blueprint
from agent_blueprint.cli.generate import TargetFramework
from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from tests.test_generators.test_langgraph import _load_generated_nodes_module

CHECKS_PY = '''
def not_bad(output, state):
    return "answer was 'bad'" if output == "bad" else True

def always_false(output, state):
    return False

def sees_state(output, state):
    return None if "messages" in state else "state missing"
'''

CONTRACT = {
    "outputs": {
        "payload": {
            "type": "object",
            "required": ["route"],
            "properties": {"route": {"type": "string"}},
            "additionalProperties": False,
        }
    },
}


def _spec(*, verify: dict | None = None, retry: dict | None = None, contract: bool = False,
          tools: dict | None = None, agent_tools: list | None = None) -> dict:
    node: dict = {"agent": "assistant"}
    if verify is not None:
        node["verify"] = verify
    if retry is not None:
        node["retry"] = retry
    raw: dict = {
        "blueprint": {"name": "verify-test"},
        "state": {"fields": {
            "messages": {"type": "list[message]", "reducer": "append"},
            "route": {"type": "string", "nullable": True, "default": None},
        }},
        "agents": {"assistant": {"model": "gpt-4o", "tools": agent_tools or []}},
        "graph": {
            "entry_point": "assistant",
            "nodes": {"assistant": node, "fb": {"agent": "assistant"}},
            "edges": [{"from": "assistant", "to": "END"}, {"from": "fb", "to": "END"}],
        },
    }
    if tools:
        raw["tools"] = tools
    if contract:
        raw["contracts"] = {
            "nodes": {"assistant": {"output_contract": "payload", "produces": ["route"]}},
            **CONTRACT,
        }
    return raw


class TestValidation:
    def test_needs_a_check(self):
        with pytest.raises(ValidationError, match="at least one check"):
            BlueprintSpec.model_validate(_spec(verify={"max_attempts": 2}))

    def test_max_attempts_at_least_two(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate(_spec(verify={"max_attempts": 1, "functions": ["m.f"]}))

    def test_function_must_be_dotted(self):
        with pytest.raises(ValidationError, match="dotted"):
            BlueprintSpec.model_validate(_spec(verify={"functions": ["nodots"]}))

    def test_only_on_agent_nodes(self):
        raw = _spec()
        raw["graph"]["nodes"]["fn"] = {"type": "function", "verify": {"functions": ["m.f"]}}
        with pytest.raises(ValidationError, match="only supported on agent nodes"):
            BlueprintSpec.model_validate(raw)

    def test_output_contract_check_needs_node_contract(self):
        with pytest.raises(ValidationError, match="requires contracts.nodes.assistant.output_contract"):
            BlueprintSpec.model_validate(_spec(verify={"output_contract": True}))

    def test_output_contract_check_ok_with_contract(self):
        BlueprintSpec.model_validate(_spec(verify={"output_contract": True}, contract=True))

    @pytest.mark.parametrize("side_effect", ["write", "irreversible"])
    def test_non_idempotent_mutating_tool_rejected(self, side_effect):
        raw = _spec(
            verify={"functions": ["m.f"]},
            tools={"mutate": {"type": "function", "side_effect": side_effect}},
            agent_tools=["mutate"],
        )
        with pytest.raises(ValidationError, match="re-runs the node"):
            BlueprintSpec.model_validate(raw)

    def test_idempotent_mutating_tool_allowed(self):
        BlueprintSpec.model_validate(_spec(
            verify={"functions": ["m.f"]},
            tools={"mutate": {"type": "function", "side_effect": "write", "idempotent": True}},
            agent_tools=["mutate"],
        ))

    def test_unresolvable_function_flagged_by_doctor(self):
        spec = BlueprintSpec.model_validate(_spec(verify={"functions": ["no_such_pkg.check"]}))
        findings = doctor_blueprint(spec, compile_blueprint(spec), target=TargetFramework.langgraph)
        assert any(
            f.code == "unresolved-impl-import" and f.severity == DoctorSeverity.error
            and f.location == "graph.nodes.assistant.verify.functions"
            for f in findings
        )


class TestGeneration:
    def test_no_verify_leaves_output_unchanged(self):
        spec = BlueprintSpec.model_validate(_spec())
        nodes_py = LangGraphGenerator().generate(compile_blueprint(spec))["nodes.py"]
        for marker in ("_with_verification", "VERIFY_POLICY_BY_NODE", "_OutputContractError"):
            assert marker not in nodes_py


def _module(tmp_path, monkeypatch, raw: dict, llm_script: list[dict]):
    module = _load_generated_nodes_module(tmp_path, monkeypatch, spec_data=raw, llm_script=llm_script)
    # The node builds a fresh LLM per attempt; share one script so replies advance across attempts.
    shared = [dict(item) for item in llm_script]
    fake_llm = sys.modules["langchain_openai"].ChatOpenAI
    monkeypatch.setattr(fake_llm, "__init__", lambda self, *a, **k: setattr(self, "_script", shared))
    (tmp_path / "verify_checks.py").write_text(CHECKS_PY, encoding="utf-8")
    monkeypatch.delitem(sys.modules, "verify_checks", raising=False)
    sys.modules["_abp_trace"].start_trace(
        run_id="r", blueprint="verify-test", blueprint_version="1.0", mode="mock"
    )
    return module


def _events() -> list[str]:
    return [e["event"] for e in sys.modules["_abp_trace"].current_recorder().manifest["trace"]]


def _spy(monkeypatch) -> list[list]:
    seen: list[list] = []
    fake_llm = sys.modules["langchain_openai"].ChatOpenAI
    original = fake_llm.invoke
    monkeypatch.setattr(fake_llm, "invoke", lambda self, w: (seen.append(list(w)), original(self, w))[1])
    return seen


class TestRuntime:
    def test_retries_with_feedback_until_check_passes(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(verify={"max_attempts": 3, "functions": ["verify_checks.not_bad"]}),
            [{"content": "bad"}, {"content": "good"}],
        )
        seen = _spy(monkeypatch)
        result = module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert [m.content for m in result["messages"]] == ["good"]  # failed attempt discarded
        assert len(seen) == 2
        feedback = seen[1][-1].content
        assert "failed verification" in feedback and "answer was 'bad'" in feedback
        failed = [e for e in sys.modules["_abp_trace"].current_recorder().manifest["trace"]
                  if e["event"] == "verification_failed"]
        assert len(failed) == 1 and failed[0]["metadata"] == {"attempt": 1, "max_attempts": 3}

    def test_passing_first_time_runs_once(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(verify={"functions": ["verify_checks.not_bad"]}),
            [{"content": "good"}],
        )
        seen = _spy(monkeypatch)
        module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert len(seen) == 1
        assert "verification_failed" not in _events()

    def test_check_receives_state_and_updates(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(verify={"functions": ["verify_checks.sees_state"]}),
            [{"content": "ok"}],
        )
        module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert "verification_failed" not in _events()

    def test_exhaustion_raises_without_fallback(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(verify={"max_attempts": 2, "functions": ["verify_checks.always_false"]}),
            [{"content": "a"}, {"content": "b"}],
        )
        with pytest.raises(module.VerificationError, match="failed verification after 2 attempts"):
            module.node_assistant({"messages": [module.HumanMessage("hi")]})
        events = _events()
        assert events.count("verification_failed") == 2
        assert events[-1] == "verification_exhausted"

    def test_exhaustion_routes_to_on_exhausted(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(
                verify={"max_attempts": 2, "functions": ["verify_checks.always_false"]},
                retry={"on_exhausted": "fb"},
            ),
            [{"content": "a"}, {"content": "b"}],
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert result == {"__abp_fallback_target": "fb", "__abp_fallback_source": "assistant"}
        assert _events()[-2:] == ["verification_exhausted", "retry_fallback"]

    def test_output_contract_failure_is_re_attempted(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(verify={"max_attempts": 2, "output_contract": True}, contract=True),
            [{"content": "not json"}, {"content": '{"route": "billing"}'}],
        )
        result = module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert result["route"] == {"route": "billing"}
        events = _events()
        assert "contract_failed" in events and "verification_failed" in events
        assert events[-1] == "node_finished"

    def test_output_contract_failure_not_retried_without_flag(self, tmp_path, monkeypatch):
        module = _module(
            tmp_path, monkeypatch,
            _spec(verify={"functions": ["verify_checks.not_bad"]}, contract=True),
            [{"content": "not json"}, {"content": '{"route": "billing"}'}],
        )
        with pytest.raises(ValueError, match="output contract"):
            module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert "verification_failed" not in _events()
