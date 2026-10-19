"""Tests for supervisor routing — models, compiler, lint, and generation."""

import ast
import subprocess
import sys

import pytest
from pydantic import ValidationError

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.harness_runner import run_harness_scenario
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def _team_spec(**sup_extra) -> dict:
    return {
        "blueprint": {"name": "team"},
        "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
        "agents": {
            "boss": {"model": "gpt-4o", "system_prompt": "Coordinate the team."},
            "res": {"model": "gpt-4o", "system_prompt": "Research."},
            "wri": {"model": "gpt-4o", "system_prompt": "Write."},
        },
        "graph": {
            "entry_point": "coordinator",
            "nodes": {
                "coordinator": {
                    "type": "supervisor",
                    "agent": "boss",
                    "workers": ["research", "writer"],
                    "max_iterations": 5,
                    **sup_extra,
                },
                "research": {"agent": "res", "description": "Research specialist"},
                "writer": {"agent": "wri", "description": "Writing specialist"},
            },
            "edges": [],
        },
    }


def generate(raw: dict) -> dict[str, str]:
    return LangGraphGenerator().generate(compile_blueprint(BlueprintSpec.model_validate(raw)))


class TestSupervisorValidation:
    def test_valid_supervisor_accepted(self):
        spec = BlueprintSpec.model_validate(_team_spec())
        node = spec.graph.nodes["coordinator"]
        assert node.workers == ["research", "writer"]
        assert node.max_iterations == 5

    def test_requires_agent(self):
        raw = _team_spec()
        del raw["graph"]["nodes"]["coordinator"]["agent"]
        with pytest.raises(ValidationError, match="require an 'agent'"):
            BlueprintSpec.model_validate(raw)

    def test_requires_workers(self):
        raw = _team_spec()
        raw["graph"]["nodes"]["coordinator"]["workers"] = []
        with pytest.raises(ValidationError, match="at least one worker"):
            BlueprintSpec.model_validate(raw)

    def test_undefined_worker_rejected(self):
        raw = _team_spec()
        raw["graph"]["nodes"]["coordinator"]["workers"] = ["ghost"]
        with pytest.raises(ValidationError, match="not defined in nodes"):
            BlueprintSpec.model_validate(raw)

    def test_worker_must_be_agent_node(self):
        raw = _team_spec()
        raw["graph"]["nodes"]["fn"] = {"type": "function"}
        raw["graph"]["nodes"]["coordinator"]["workers"] = ["fn"]
        with pytest.raises(ValidationError, match="must be an agent node"):
            BlueprintSpec.model_validate(raw)

    def test_worker_outgoing_edge_rejected(self):
        raw = _team_spec()
        raw["graph"]["edges"].append({"from": "research", "to": "END"})
        with pytest.raises(ValidationError, match="return automatically"):
            BlueprintSpec.model_validate(raw)

    def test_supervisor_outgoing_edge_rejected(self):
        raw = _team_spec()
        raw["graph"]["edges"].append({"from": "coordinator", "to": "END"})
        with pytest.raises(ValidationError, match="cannot also declare explicit"):
            BlueprintSpec.model_validate(raw)

    def test_shared_worker_rejected(self):
        raw = _team_spec()
        raw["graph"]["nodes"]["sup2"] = {
            "type": "supervisor", "agent": "boss", "workers": ["research"],
        }
        with pytest.raises(ValidationError, match="worker of both"):
            BlueprintSpec.model_validate(raw)

    def test_workers_field_only_on_supervisor(self):
        raw = _team_spec()
        raw["graph"]["nodes"]["research"]["workers"] = ["writer"]
        with pytest.raises(ValidationError, match="only valid on supervisor"):
            BlueprintSpec.model_validate(raw)

    def test_on_finish_must_exist(self):
        raw = _team_spec(on_finish="ghost")
        with pytest.raises(ValidationError, match="not defined in nodes"):
            BlueprintSpec.model_validate(raw)

    def test_on_finish_cannot_be_worker(self):
        raw = _team_spec(on_finish="research")
        with pytest.raises(ValidationError, match="cannot also be a worker"):
            BlueprintSpec.model_validate(raw)


class TestSupervisorCompilation:
    def test_iteration_state_field_injected(self):
        ir = compile_blueprint(BlueprintSpec.model_validate(_team_spec()))
        field = ir.state.fields["_abp_supervisor_iters"]
        assert field.reducer.value == "merge"

    def test_no_injection_without_supervisor(self):
        raw = _team_spec()
        raw["graph"] = {
            "entry_point": "research",
            "nodes": {"research": {"agent": "res"}},
            "edges": [{"from": "research", "to": "END"}],
        }
        ir = compile_blueprint(BlueprintSpec.model_validate(raw))
        assert "_abp_supervisor_iters" not in ir.state.fields

    def test_subgraph_namespaces_workers_and_on_finish(self):
        raw = {
            "blueprint": {"name": "nested-team"},
            "state": {"fields": {
                "messages": {"type": "list[message]", "reducer": "append"},
                "q": {"type": "string"},
            }},
            "agents": {
                "boss": {"model": "gpt-4o", "system_prompt": "x"},
                "w1": {"model": "gpt-4o", "system_prompt": "y"},
                "s1": {"model": "gpt-4o", "system_prompt": "z"},
            },
            "graph": {
                "entry_point": "team",
                "nodes": {"team": {
                    "type": "subgraph", "ref": "sg",
                    "input_map": {"q": "iq"}, "output_map": {"iq": "q"},
                }},
                "edges": [{"from": "team", "to": "END"}],
            },
            "subgraphs": {"sg": {
                "entry_point": "sup",
                "nodes": {
                    "sup": {"type": "supervisor", "agent": "boss",
                            "workers": ["worker"], "on_finish": "closer"},
                    "worker": {"agent": "w1"},
                    "closer": {"agent": "s1"},
                },
                "edges": [{"from": "closer", "to": "END"}],
            }},
        }
        ir = compile_blueprint(BlueprintSpec.model_validate(raw))
        sup = next(n for n in ir.nodes if n.id == "team__sup")
        assert sup.node_def.workers == ["team__worker"]
        assert sup.node_def.on_finish == "team__closer"


class TestSupervisorLint:
    def test_workers_not_flagged_unreachable(self):
        spec = BlueprintSpec.model_validate(_team_spec())
        findings = lint_blueprint(spec, compile_blueprint(spec))
        assert not [f for f in findings if f.code == "unreachable-node"]

    def test_supervisor_loop_not_flagged_unbounded(self):
        spec = BlueprintSpec.model_validate(_team_spec())
        findings = lint_blueprint(spec, compile_blueprint(spec))
        assert not [f for f in findings if f.code == "unbounded-loop"]


class TestSupervisorGeneration:
    def test_generated_modules_are_valid_python(self):
        files = generate(_team_spec())
        for name in ("nodes.py", "graph.py", "state.py", "main.py"):
            ast.parse(files[name])

    def test_transfer_infrastructure(self):
        nodes_py = generate(_team_spec())["nodes.py"]
        assert "from langgraph.types import Command" in nodes_py
        assert '"transfer_to_research": "research"' in nodes_py
        assert '"transfer_to_writer": "writer"' in nodes_py
        assert "SUPERVISOR_TRANSFER_TOOLS" in nodes_py
        # tool description comes from the worker node description
        assert "Research specialist" in nodes_py

    def test_supervisor_returns_command(self):
        nodes_py = generate(_team_spec())["nodes.py"]
        assert "def node_coordinator(state: AgentState) -> Command:" in nodes_py
        assert "return Command(goto=target, update=updates)" in nodes_py
        assert "return Command(goto=END, update=updates)" in nodes_py

    def test_on_finish_target_in_goto(self):
        raw = _team_spec(on_finish="closer")
        raw["graph"]["nodes"]["closer"] = {"agent": "wri"}
        raw["graph"]["edges"].append({"from": "closer", "to": "END"})
        nodes_py = generate(raw)["nodes.py"]
        assert 'return Command(goto="closer", update=updates)' in nodes_py

    def test_max_iterations_guard(self):
        nodes_py = generate(_team_spec())["nodes.py"]
        assert "iteration > 5" in nodes_py
        assert '"supervisor_iterations_exhausted"' in nodes_py

    def test_handoff_trace_event(self):
        nodes_py = generate(_team_spec())["nodes.py"]
        assert '"agent_handoff"' in nodes_py
        assert '"handoff": True' in nodes_py

    def test_worker_return_edges(self):
        graph_py = generate(_team_spec())["graph.py"]
        assert 'builder.add_edge("research", "coordinator")' in graph_py
        assert 'builder.add_edge("writer", "coordinator")' in graph_py

    def test_state_declares_iteration_channel(self):
        state_py = generate(_team_spec())["state.py"]
        assert "_abp_supervisor_iters" in state_py
        assert "merge_reducer" in state_py

    def test_no_supervisor_artifacts_without_supervisor(self):
        raw = _team_spec()
        raw["graph"] = {
            "entry_point": "research",
            "nodes": {"research": {"agent": "res"}},
            "edges": [{"from": "research", "to": "END"}],
        }
        files = generate(raw)
        assert "SUPERVISOR_TRANSFER_TOOLS" not in files["nodes.py"]
        assert "from langgraph.types import Command" not in files["nodes.py"]
        assert "_abp_supervisor_iters" not in files["state.py"]

    def test_worker_nodes_use_unchanged_agent_template(self):
        nodes_py = generate(_team_spec())["nodes.py"]
        # workers are plain agent nodes returning dicts, not Commands
        assert "def node_research(state: AgentState) -> dict:" in nodes_py
        assert "def node_writer(state: AgentState) -> dict:" in nodes_py


def _supervisor_with_tool_spec() -> dict:
    raw = _team_spec()
    raw["agents"]["boss"]["tools"] = ["lookup"]
    raw["tools"] = {
        "lookup": {
            "type": "function",
            "impl": "lookup_impl.lookup",
            "description": "Look up a record",
            "parameters": {"record_id": {"type": "string", "required": True}},
        }
    }
    raw["policies"] = {"tool_usage": {"require_explicit_arguments": True}}
    return raw


def _supervisor_function(nodes_py: str) -> ast.FunctionDef:
    tree = ast.parse(nodes_py)
    return next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "node_coordinator"
    )


class TestSupervisorToolPolicies:
    def test_regular_tool_calls_are_validated(self):
        fn = _supervisor_function(generate(_supervisor_with_tool_spec())["nodes.py"])
        called = {
            n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "validate_tool_arguments" in called

    def test_policy_violation_recorded_on_real_langgraph(self, tmp_path):
        # Subprocess run of the generated project; skipped where langgraph is absent.
        probe = subprocess.run([sys.executable, "-c", "import langgraph.graph"], capture_output=True)
        if probe.returncode != 0:
            pytest.skip("langgraph is not installed")
        (tmp_path / "lookup_impl.py").write_text("def lookup(record_id):\n    return 'ok'\n")
        raw = _supervisor_with_tool_spec()
        raw["harness"] = {
            "scenarios": [
                {
                    "id": "bad_args",
                    "input": {"message": "find record 7"},
                    "fixtures": {
                        "llm_outputs": {
                            "coordinator": [
                                {"tool_calls": [{"name": "lookup", "args": {"bogus": 1}}]},
                                {"content": "Done."},
                            ]
                        }
                    },
                }
            ]
        }
        ir = compile_blueprint(BlueprintSpec.model_validate(raw))
        result = run_harness_scenario(
            ir, ir.harness.scenarios[0], install=False, source_dir=tmp_path
        )
        assert result.trace_manifest is not None, result.stderr
        violations = [
            e for e in result.trace_manifest["trace"] if e.get("event") == "policy_violation"
        ]
        assert violations, result.trace_manifest["trace"]
        assert violations[0]["node"] == "coordinator"
        assert violations[0]["tool"] == "lookup"


def _guarded_team_spec() -> dict:
    """A team where the supervisor and one worker declare every node guarantee."""
    raw = _supervisor_with_tool_spec()
    raw["agents"]["res"]["tools"] = ["lookup"]
    hitl = {"enabled": True, "trigger": "before_tool_call", "tools": ["lookup"]}
    raw["agents"]["boss"]["human_in_the_loop"] = hitl
    raw["agents"]["res"]["human_in_the_loop"] = hitl
    raw["state"]["fields"].update(
        {
            "summary": {"type": "string"},
            "confidence": {"type": "float"},
            "ticket_id": {"type": "string"},
            "notes": {"type": "string"},
        }
    )
    node_contract = {
        "requires": ["messages"],
        "produces": ["summary", "confidence"],
        "output_contract": "report",
        "forbids_mutation": ["notes"],
    }
    raw["contracts"] = {
        "state": {"immutable_fields": ["ticket_id"], "invariants": ["state.confidence != 2"]},
        "nodes": {"coordinator": dict(node_contract), "research": dict(node_contract)},
        "outputs": {
            "report": {
                "type": "object",
                "required": ["summary", "confidence"],
                "properties": {"summary": {"type": "string"}, "confidence": {"type": "number"}},
            }
        },
    }
    raw["artifacts"] = {
        "coord_report": {
            "format": "json", "producer": "coordinator", "contract": "report",
            "path": "artifacts/coord.json",
        },
        "research_report": {
            "format": "json", "producer": "research", "contract": "report",
            "path": "artifacts/research.json",
        },
    }
    raw["policies"]["escalation"] = {"on_low_confidence": "writer", "confidence_threshold": 0.5}
    return raw


def _node_calls(nodes_py: str, name: str) -> set[str]:
    fn = next(
        n for n in ast.parse(nodes_py).body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    return {
        n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


# Every runtime guarantee an agent node enforces, keyed by the helper that
# enforces it. A supervisor declaring the same feature must enforce it too.
GUARANTEE_HELPERS = {
    "requires": "_require_state_fields",
    "tool_arguments": "validate_tool_arguments",
    "budgets": "register_llm_budget_usage",
    "invariants": "_check_state_invariants",
    "immutable_fields": "_ensure_fields_not_mutated",
    "produces": "_require_produced_fields",
    "output_contract": "_validate_node_output_contract",
    "artifacts": "_write_node_artifacts",
    "human_in_the_loop": "_require_human_review",
    "low_confidence_escalation": "_apply_low_confidence_escalation",
}


class TestSupervisorGuaranteeParity:
    @pytest.mark.parametrize("guarantee", list(GUARANTEE_HELPERS))
    def test_supervisor_enforces_what_agent_enforces(self, guarantee):
        nodes_py = generate(_guarded_team_spec())["nodes.py"]
        helper = GUARANTEE_HELPERS[guarantee]
        assert helper in _node_calls(nodes_py, "node_research")
        assert helper in _node_calls(nodes_py, "node_coordinator")

    def test_supervisor_injects_rag_context(self):
        raw = _team_spec()
        raw["retrievers"] = {"docs": {"impl": "kb.search", "description": "Docs"}}
        raw["tools"] = {"search_kb": {"type": "retrieval", "retriever": "docs", "description": "KB"}}
        raw["agents"]["boss"]["rag"] = {"tool": "search_kb", "mode": "context_only"}
        nodes_py = generate(raw)["nodes.py"]
        assert "_last_user_message_content" in _node_calls(nodes_py, "node_coordinator")


def _require_langgraph() -> None:
    probe = subprocess.run([sys.executable, "-c", "import langgraph.graph"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("langgraph is not installed")


def _contracted_supervisor_spec() -> dict:
    """Supervisor that must answer with a report and escalates low confidence."""
    raw = _team_spec()
    raw["state"]["fields"].update(
        {"summary": {"type": "string"}, "confidence": {"type": "float"}}
    )
    raw["agents"]["rev"] = {"model": "gpt-4o", "system_prompt": "Review."}
    raw["graph"]["nodes"]["review"] = {"agent": "rev"}
    raw["graph"]["edges"].append({"from": "review", "to": "END"})
    raw["contracts"] = {
        "state": {"invariants": ["state.confidence == null or state.confidence <= 1"]},
        "nodes": {
            "coordinator": {"produces": ["summary", "confidence"], "output_contract": "report"}
        },
        "outputs": {
            "report": {
                "type": "object",
                "required": ["summary", "confidence"],
                "properties": {"summary": {"type": "string"}, "confidence": {"type": "number"}},
            }
        },
    }
    raw["artifacts"] = {
        "team_report": {
            "format": "json",
            "producer": "coordinator",
            "contract": "report",
            "path": "artifacts/report.json",
        }
    }
    raw["policies"] = {"escalation": {"on_low_confidence": "review", "confidence_threshold": 0.5}}
    return raw


def _run_supervisor_scenario(tmp_path, final_answer: str, expected: dict | None = None):
    _require_langgraph()
    raw = _contracted_supervisor_spec()
    raw["harness"] = {
        "scenarios": [
            {
                "id": "s",
                "input": {"message": "Summarise the findings"},
                "fixtures": {
                    "llm_outputs": {
                        "coordinator": [
                            {"tool_calls": [{"name": "transfer_to_research", "args": {}}]},
                            {"content": final_answer},
                        ],
                        "research": [{"content": "Found three issues."}],
                        "review": [{"content": "Reviewed by a human."}],
                    }
                },
                "expected": expected or {},
            }
        ]
    }
    ir = compile_blueprint(BlueprintSpec.model_validate(raw))
    return run_harness_scenario(ir, ir.harness.scenarios[0], install=False, source_dir=tmp_path)


def _events(result, name: str) -> list[dict]:
    assert result.trace_manifest is not None, result.stderr
    return [e for e in result.trace_manifest["trace"] if e.get("event") == name]


class TestSupervisorGuaranteesRuntime:
    """Supervisor guarantees on the real LangGraph runtime (skipped without it)."""

    def test_valid_answer_produces_fields_and_artifact(self, tmp_path):
        result = _run_supervisor_scenario(
            tmp_path,
            '{"summary": "three issues", "confidence": 0.9}',
            {
                "route": "coordinator",
                "state_assertions": ["state.summary == 'three issues'"],
                "artifacts": ["team_report"],
            },
        )
        assert result.passed, (result.failures, result.stderr)

    def test_invalid_answer_fails_output_contract(self, tmp_path):
        result = _run_supervisor_scenario(tmp_path, "no structured answer")
        assert not result.passed
        failed = _events(result, "contract_failed")
        assert failed and failed[0]["node"] == "coordinator"

    def test_invariant_violation_fails_the_run(self, tmp_path):
        result = _run_supervisor_scenario(tmp_path, '{"summary": "x", "confidence": 7}')
        assert not result.passed
        assert any(e["node"] == "coordinator" for e in _events(result, "contract_failed"))

    def test_low_confidence_escalates_instead_of_finishing(self, tmp_path):
        result = _run_supervisor_scenario(
            tmp_path, '{"summary": "unsure", "confidence": 0.2}', {"route": "review"}
        )
        assert result.passed, (result.failures, result.stderr)
        started = [e["node"] for e in _events(result, "node_started")]
        assert started[-1] == "review"

    def test_human_review_blocks_supervisor_tool_call(self, tmp_path, monkeypatch):
        _require_langgraph()
        monkeypatch.setenv("ABP_HITL_MODE", "deny")
        (tmp_path / "lookup_impl.py").write_text("def lookup(record_id):\n    return 'ok'\n")
        raw = _supervisor_with_tool_spec()
        raw["agents"]["boss"]["human_in_the_loop"] = {
            "enabled": True,
            "trigger": "before_tool_call",
            "tools": ["lookup"],
        }
        raw["harness"] = {
            "scenarios": [
                {
                    "id": "review",
                    "input": {"message": "find record 7"},
                    "fixtures": {
                        "llm_outputs": {
                            "coordinator": [
                                {"tool_calls": [{"name": "lookup", "args": {"record_id": "7"}}]},
                                {"content": "Done."},
                            ]
                        }
                    },
                }
            ]
        }
        ir = compile_blueprint(BlueprintSpec.model_validate(raw))
        result = run_harness_scenario(
            ir, ir.harness.scenarios[0], install=False, source_dir=tmp_path
        )
        assert not result.passed
        # Human review events are reported on stderr, not in the trace.
        assert '"event": "human_review_requested"' in result.stderr
        assert '"node": "coordinator"' in result.stderr
        assert "Human review required for node 'coordinator'" in result.stderr
