"""retry.on_exhausted: validation, compilation, lint, generation and routing."""

import ast
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from tests.test_generators.test_langgraph import _load_generated_nodes_module

FALLBACK_KEYS = '{"__abp_fallback_target": "fb", "__abp_fallback_source": "a"}'


def _spec(*, retry: dict | None = None, edges: list | None = None, policies: dict | None = None,
          extra_nodes: dict | None = None) -> dict:
    raw: dict = {
        "blueprint": {"name": "fallback-test"},
        "state": {"fields": {"trail": {"type": "array", "reducer": "append"}}},
        "agents": {"worker": {"model": "gpt-4o"}},
        "graph": {
            "entry_point": "a",
            "nodes": {
                "a": {"agent": "worker", "retry": {"max_attempts": 2, **(retry or {"on_exhausted": "fb"})}},
                "b": {"agent": "worker"},
                "fb": {"agent": "worker"},
                **(extra_nodes or {}),
            },
            "edges": edges if edges is not None else [
                {"from": "a", "to": "b"},
                {"from": "b", "to": "END"},
                {"from": "fb", "to": "END"},
            ],
        },
    }
    if policies:
        raw["policies"] = policies
    return raw


def _generate(raw: dict) -> dict[str, str]:
    return LangGraphGenerator().generate(compile_blueprint(BlueprintSpec.model_validate(raw)))


class TestValidation:
    def test_valid(self):
        spec = BlueprintSpec.model_validate(_spec())
        assert spec.graph.nodes["a"].retry.on_exhausted == "fb"

    def test_unknown_target(self):
        with pytest.raises(ValidationError, match="not defined in nodes"):
            BlueprintSpec.model_validate(_spec(retry={"on_exhausted": "ghost"}))

    def test_self_target(self):
        with pytest.raises(ValidationError, match="cannot target itself"):
            BlueprintSpec.model_validate(_spec(retry={"on_exhausted": "a"}))

    def test_non_agent_source_rejected(self):
        raw = _spec(extra_nodes={"fn": {"type": "function", "retry": {"on_exhausted": "fb"}}})
        with pytest.raises(ValidationError, match="only supported on agent nodes"):
            BlueprintSpec.model_validate(raw)

    def test_parallel_branch_rejected(self):
        raw = _spec(
            edges=[{"from": "b", "to": "END"}, {"from": "fb", "to": "END"}],
            extra_nodes={"fan": {"type": "parallel", "branches": ["a"], "join": "b"}},
        )
        raw["graph"]["entry_point"] = "fan"
        with pytest.raises(ValidationError, match="parallel branch or supervisor worker"):
            BlueprintSpec.model_validate(raw)

    def test_tool_retry_has_no_on_exhausted(self):
        from agent_blueprint.models.tools import ToolDef

        tool = ToolDef.model_validate({"type": "function", "retry": {"max_attempts": 2, "on_exhausted": "x"}})
        assert not hasattr(tool.retry, "on_exhausted")


class TestSubgraphs:
    def _raw(self):
        return {
            "blueprint": {"name": "sg-fallback"},
            "state": {"fields": {"q": {"type": "string"}, "out": {"type": "string"}}},
            "agents": {"worker": {"model": "gpt-4o"}},
            "subgraphs": {
                "inner": {
                    "entry_point": "x",
                    "nodes": {
                        "x": {"agent": "worker", "retry": {"max_attempts": 2, "on_exhausted": "y"}},
                        "y": {"agent": "worker"},
                    },
                    "edges": [{"from": "x", "to": "END"}, {"from": "y", "to": "END"}],
                }
            },
            "graph": {
                "entry_point": "sub",
                "nodes": {
                    "sub": {"type": "subgraph", "ref": "inner", "input_map": {"q": "q"}, "output_map": {"out": "out"}},
                    "after": {"agent": "worker"},
                },
                "edges": [{"from": "sub", "to": "after"}, {"from": "after", "to": "END"}],
            },
        }

    def test_inner_fallback_is_namespaced(self):
        ir = compile_blueprint(BlueprintSpec.model_validate(self._raw()))
        nodes = {n.id: n for n in ir.nodes}
        inner = [n for nid, n in nodes.items() if nid.endswith("x")][0]
        target = inner.node_def.retry.on_exhausted
        assert target in nodes and target.endswith("y") and target != "y"

    def test_outer_fallback_to_subgraph_maps_to_entry(self):
        raw = self._raw()
        raw["graph"]["nodes"]["after"]["retry"] = {"max_attempts": 2, "on_exhausted": "sub"}
        ir = compile_blueprint(BlueprintSpec.model_validate(raw))
        nodes = {n.id: n for n in ir.nodes}
        target = nodes["after"].node_def.retry.on_exhausted
        assert target in nodes and nodes[target].node_def.type.value != "subgraph"


class TestLint:
    def test_node_reachable_only_via_fallback_is_not_unreachable(self):
        raw = _spec(edges=[{"from": "a", "to": "b"}, {"from": "b", "to": "END"}, {"from": "fb", "to": "END"}])
        spec = BlueprintSpec.model_validate(raw)
        findings = lint_blueprint(spec, compile_blueprint(spec))
        assert not [f for f in findings if f.code == "unreachable-node"]

    def test_without_fallback_node_is_unreachable(self):
        raw = _spec(retry={"max_attempts": 2})
        spec = BlueprintSpec.model_validate(raw)
        findings = lint_blueprint(spec, compile_blueprint(spec))
        assert [f.location for f in findings if f.code == "unreachable-node"] == ["graph.nodes.fb"]


class TestGeneration:
    def test_no_fallback_leaves_output_unchanged(self):
        files = _generate(_spec(retry={"max_attempts": 2}))
        assert "_RetryFallback" not in files["nodes.py"]
        assert "__abp_fallback" not in files["graph.py"]

    @pytest.mark.parametrize("case", ["static", "conditional", "escalation", "no_edges"])
    def test_generated_python_is_valid(self, case):
        files = _generate(_case_spec(case))
        ast.parse(files["nodes.py"])
        ast.parse(files["graph.py"])
        assert '"on_exhausted": \'fb\'' in files["nodes.py"] or '"on_exhausted": "fb"' in files["nodes.py"]
        assert "__abp_fallback_source" in files["graph.py"]


def _case_spec(case: str) -> dict:
    if case == "static":
        return _spec()
    if case == "conditional":
        raw = _spec(edges=[
            {"from": "a", "to": [{"condition": "state.trail", "target": "b"}, {"default": "END"}]},
            {"from": "b", "to": "END"},
            {"from": "fb", "to": "END"},
        ])
        raw["graph"]["edges"][0]["to"] = [{"condition": "state.go == true", "target": "b"}, {"default": "END"}]
        raw["state"]["fields"]["go"] = {"type": "boolean"}
        return raw
    if case == "escalation":
        return _spec(policies={"escalation": {"on_low_confidence": "b", "confidence_threshold": 0.5}})
    return _spec(edges=[{"from": "b", "to": "END"}, {"from": "fb", "to": "END"}])


class TestNodeRuntime:
    def _module(self, tmp_path, monkeypatch, retry: dict):
        spec = _spec(retry=retry)
        spec["agents"] = {"assistant": {"model": "gpt-4o"}}
        for node in spec["graph"]["nodes"].values():
            node["agent"] = "assistant"
        spec["graph"]["entry_point"] = "assistant"
        spec["graph"]["nodes"] = {"assistant": spec["graph"]["nodes"]["a"], "fb": spec["graph"]["nodes"]["fb"]}
        spec["graph"]["nodes"]["assistant"]["retry"] = {"max_attempts": 2, "backoff_seconds": 0, **retry}
        spec["graph"]["edges"] = [{"from": "assistant", "to": "END"}, {"from": "fb", "to": "END"}]
        module = _load_generated_nodes_module(
            tmp_path,
            monkeypatch,
            spec_data=spec,
            llm_script=[{"raises": RuntimeError("down")}, {"raises": RuntimeError("down")}],
        )
        sys.modules["_abp_trace"].start_trace(
            run_id="r", blueprint="fallback-test", blueprint_version="1.0", mode="mock"
        )
        return module

    def test_exhaustion_returns_fallback_signal_and_traces(self, tmp_path, monkeypatch):
        module = self._module(tmp_path, monkeypatch, {"on_exhausted": "fb"})
        result = module.node_assistant({"messages": [module.HumanMessage("hi")]})
        assert result == {"__abp_fallback_target": "fb", "__abp_fallback_source": "assistant"}
        events = [e["event"] for e in sys.modules["_abp_trace"].current_recorder().manifest["trace"]]
        assert events == ["node_started", "retry_scheduled", "retry_exhausted", "retry_fallback"]

    def test_without_fallback_still_raises(self, tmp_path, monkeypatch):
        module = self._module(tmp_path, monkeypatch, {})
        with pytest.raises(RuntimeError, match="down"):
            module.node_assistant({"messages": [module.HumanMessage("hi")]})


def _langgraph_available() -> bool:
    probe = subprocess.run([sys.executable, "-c", "import langgraph.graph"], capture_output=True)
    return probe.returncode == 0


class TestRealLangGraphRouting:
    """End to end on the real LangGraph runtime, in a subprocess (other tests fake langgraph)."""

    @pytest.mark.parametrize("case", ["static", "conditional", "escalation", "no_edges"])
    @pytest.mark.parametrize("fails", [True, False])
    def test_routes_to_fallback_only_on_exhaustion(self, tmp_path, case, fails):
        if not _langgraph_available():
            pytest.skip("langgraph is not installed")
        files = _generate(_case_spec(case))
        stub_a = FALLBACK_KEYS if fails else '{"trail": ["a"]}'
        (tmp_path / "state.py").write_text(files["state.py"], encoding="utf-8")
        (tmp_path / "graph.py").write_text(files["graph.py"], encoding="utf-8")
        (tmp_path / "nodes.py").write_text(
            f"def node_a(state):\n    return {stub_a}\n\n"
            'def node_b(state):\n    return {"trail": ["b"]}\n\n'
            'def node_fb(state):\n    return {"trail": ["fb"]}\n',
            encoding="utf-8",
        )
        driver = (
            "import json\nfrom graph import graph\n"
            'result = graph.invoke({"trail": [], "go": True}, {"configurable": {"thread_id": "t"}})\n'
            "print(json.dumps(result))\n"
        )
        done = subprocess.run(
            [sys.executable, "-c", driver], cwd=tmp_path, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stderr
        trail = json.loads(done.stdout.strip().splitlines()[-1])["trail"]
        if fails:
            assert trail == ["fb"]
        else:
            assert "fb" not in trail
            assert trail[0] == "a"
