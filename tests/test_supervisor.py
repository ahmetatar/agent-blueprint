"""Tests for supervisor routing — models, compiler, lint, and generation."""

import ast

import pytest
from pydantic import ValidationError

from agent_blueprint.generators.langgraph import LangGraphGenerator
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
