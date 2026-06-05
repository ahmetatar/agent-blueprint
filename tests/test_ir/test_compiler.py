"""Tests for IR compiler."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_blueprint.exceptions import BlueprintCompilationError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_spec(name: str) -> BlueprintSpec:
    raw = load_blueprint_yaml(FIXTURES / name)
    return BlueprintSpec.model_validate(raw)


class TestCompileBasicChatbot:
    def test_compiles_successfully(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.name == "basic-chatbot"

    def test_nodes_compiled(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert len(ir.nodes) == 1
        assert ir.nodes[0].id == "assistant"

    def test_entry_point(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.entry_point == "assistant"

    def test_agent_attached_to_node(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        node = ir.nodes[0]
        assert node.agent is not None
        assert node.agent.model == "gpt-4o"


class TestCompileCustomerSupport:
    def test_conditional_edges_compiled(self):
        spec = load_spec("customer_support.yml")
        ir = compile_blueprint(spec)
        router_edges = ir.get_edges_from("router")
        assert len(router_edges) == 1
        assert router_edges[0].is_conditional

    def test_condition_expressions_parsed(self):
        spec = load_spec("customer_support.yml")
        ir = compile_blueprint(spec)
        edge = ir.get_edges_from("router")[0]
        cond_targets = [t for t in edge.targets if t.condition is not None]
        assert len(cond_targets) == 2
        # Verify condition compiles to valid Python
        code = cond_targets[0].condition.to_dict_access("state")
        result = eval(code, {}, {"state": {"department": "billing"}})
        assert isinstance(result, bool)

    def test_tool_defs_attached(self):
        spec = load_spec("customer_support.yml")
        ir = compile_blueprint(spec)
        router_node = ir.get_node("router")
        assert router_node is not None
        assert "classify_intent" in router_node.tool_defs


class TestCompilerWarnings:
    def test_no_warnings_for_anthropic_reasoning(self):
        spec = load_spec("reasoning_agent.yml")
        ir = compile_blueprint(spec)
        assert ir.warnings == []

    def test_warning_for_reasoning_with_empty_params(self):
        spec = load_spec("reasoning_openai.yml")
        ir = compile_blueprint(spec)
        assert len(ir.warnings) == 1
        assert "thinker" in ir.warnings[0]
        assert "params" in ir.warnings[0]

    def test_warning_for_reasoning_without_explicit_provider_adapter(self):
        spec = load_spec("reasoning_openai.yml")
        spec.agents["thinker"].reasoning.params = {"reasoning": {"effort": "high"}}
        spec.agents["thinker"].model = "gpt-4o"
        ir = compile_blueprint(spec)
        assert len(ir.warnings) == 1
        assert "model_provider or provider/model prefix" in ir.warnings[0]

    def test_legacy_llm_kwargs_is_rejected(self):
        raw = load_blueprint_yaml(FIXTURES / "reasoning_agent.yml")
        raw["agents"]["thinker"]["reasoning"]["llm_kwargs"] = raw["agents"]["thinker"]["reasoning"].pop("params")
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate(raw)
        assert "llm_kwargs" in str(exc_info.value)

    def test_no_warnings_when_reasoning_not_set(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.warnings == []


class TestHarnessCompilerSupport:
    def test_contracts_are_carried_into_ir(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "state": {
                "fields": {
                    "messages": {"type": "array", "default": []},
                    "route": {"type": "string", "default": None, "nullable": True},
                }
            },
            "graph": {
                "entry_point": "router",
                "nodes": {"router": {"agent": "assistant"}},
                "edges": [],
            },
            "agents": {"assistant": {"model": "gpt-4o"}},
            "contracts": {
                "state": {"required_fields": ["messages"]},
                "nodes": {"router": {"requires": ["messages"], "produces": ["route"]}},
                "outputs": {
                    "route_contract": {
                        "type": "object",
                        "required": ["route"],
                        "properties": {"route": {"type": "string"}},
                    }
                },
            },
        })
        ir = compile_blueprint(spec)
        assert ir.contracts is not None
        assert ir.contracts.state.required_fields == ["messages"]
        assert ir.nodes[0].contract is not None
        assert ir.nodes[0].contract.requires == ["messages"]

    def test_harness_is_carried_into_ir(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            "harness": {
                "defaults": {"llm_mode": "mock", "tool_mode": "stub", "seed": 42},
                "scenarios": [
                    {
                        "id": "refund_happy_path",
                        "input": {"message": "refund"},
                        "expected": {
                            "route": "billing",
                            "tools_called": ["lookup_invoice"],
                            "output_contract": "refund_response",
                            "state_assertions": ["state.route == 'billing'"],
                        },
                    }
                ],
            },
        })
        ir = compile_blueprint(spec)
        assert ir.harness is not None
        assert ir.harness.defaults.llm_mode == "mock"
        assert ir.harness.scenarios[0].id == "refund_happy_path"
        assert ir.harness.scenarios[0].expected.route == "billing"

    def test_artifacts_are_carried_into_ir_with_ownership(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {
                "entry_point": "writer",
                "nodes": {"writer": {"type": "function"}},
                "edges": [],
            },
            "contracts": {
                "outputs": {
                    "prd_contract": {
                        "type": "object",
                        "required": ["title"],
                        "properties": {"title": {"type": "string"}},
                    }
                }
            },
            "artifacts": {
                "prd_doc": {
                    "format": "markdown",
                    "producer": "writer",
                    "contract": "prd_contract",
                    "path": "artifacts/prd.md",
                }
            },
        })
        ir = compile_blueprint(spec)

        assert ir.artifacts["prd_doc"].path == "artifacts/prd.md"
        assert ir.artifacts["prd_doc"].producer == "writer"
        assert ir.artifact_owners == {"writer": ["prd_doc"]}

    def test_evals_are_carried_into_ir(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {
                "entry_point": "router",
                "nodes": {"router": {"type": "function"}},
                "edges": [],
            },
            "evals": {
                "suites": [
                    {
                        "id": "router_accuracy",
                        "metric": "exact_match",
                        "dataset": "datasets/router_cases.yaml",
                    }
                ]
            },
        })

        ir = compile_blueprint(spec)

        assert ir.evals is not None
        assert ir.evals.suites[0].id == "router_accuracy"
        assert ir.evals.suites[0].dataset == "datasets/router_cases.yaml"


class TestReusableSubgraphs:
    def test_parallel_nodes_compile(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {
                "entry_point": "fanout",
                "nodes": {
                    "fanout": {
                        "type": "parallel",
                        "branches": ["research", "pricing"],
                        "join": "merge",
                    },
                    "research": {"type": "function"},
                    "pricing": {"type": "function"},
                    "merge": {"type": "function"},
                },
                "edges": [{"from": "merge", "to": "END"}],
            },
        })

        ir = compile_blueprint(spec)

        fanout = ir.get_node("fanout")
        assert fanout is not None
        assert fanout.node_def.type == "parallel"
        assert fanout.node_def.branches == ["research", "pricing"]
        assert fanout.node_def.join == "merge"

    def test_subgraph_nodes_expand_with_namespaced_adapters(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "state": {
                "fields": {
                    "messages": {"type": "list[message]", "reducer": "append"},
                    "prd": {"type": "object", "default": None, "nullable": True},
                }
            },
            "agents": {"writer_agent": {"model": "gpt-4o"}},
            "graph": {
                "entry_point": "prd_pipeline",
                "nodes": {
                    "prd_pipeline": {
                        "type": "subgraph",
                        "ref": "prd_generation_v1",
                        "input_map": {"messages": "messages"},
                        "output_map": {"prd": "prd"},
                    },
                    "after": {"type": "function"},
                },
                "edges": [{"from": "prd_pipeline", "to": "after"}],
            },
            "subgraphs": {
                "prd_generation_v1": {
                    "entry_point": "writer",
                    "nodes": {"writer": {"agent": "writer_agent"}},
                    "edges": [{"from": "writer", "to": "END"}],
                }
            },
        })

        ir = compile_blueprint(spec)

        assert ir.entry_point == "prd_pipeline__entry"
        assert ir.get_node("prd_pipeline") is None
        assert ir.get_node("prd_pipeline__entry") is not None
        assert ir.get_node("prd_pipeline__writer") is not None
        assert ir.get_node("prd_pipeline__exit") is not None
        assert "prd_pipeline__messages" in ir.state.fields
        assert "prd_pipeline__prd" in ir.state.fields
        assert [(edge.from_node, [target.target for target in edge.targets]) for edge in ir.edges] == [
            ("prd_pipeline__entry", ["prd_pipeline__writer"]),
            ("prd_pipeline__writer", ["prd_pipeline__exit"]),
            ("prd_pipeline__exit", ["after"]),
        ]


class TestStateInvariantCompilation:
    @staticmethod
    def _spec_with_invariants(invariants: list[str]) -> BlueprintSpec:
        return BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "state": {
                "fields": {
                    "messages": {"type": "array", "default": []},
                    "count": {"type": "integer", "default": 0},
                }
            },
            "graph": {
                "entry_point": "assistant",
                "nodes": {"assistant": {"agent": "assistant"}},
                "edges": [],
            },
            "agents": {"assistant": {"model": "gpt-4o"}},
            "contracts": {"state": {"invariants": invariants}},
        })

    def test_invariants_are_compiled_into_ir(self):
        spec = self._spec_with_invariants(["state.count >= 0"])
        ir = compile_blueprint(spec)
        assert len(ir.compiled_invariants) == 1
        assert ir.compiled_invariants[0].source == "state.count >= 0"
        code = ir.compiled_invariants[0].to_dict_access("merged")
        assert eval(code, {}, {"merged": {"count": 1}}) is True
        assert eval(code, {}, {"merged": {"count": -1}}) is False

    def test_no_contracts_yields_empty_invariants(self):
        spec = load_spec("basic_chatbot.yml")
        ir = compile_blueprint(spec)
        assert ir.compiled_invariants == []

    def test_invalid_invariant_raises_compilation_error(self):
        spec = self._spec_with_invariants(["state.count >= "])
        with pytest.raises(BlueprintCompilationError) as exc_info:
            compile_blueprint(spec)
        assert "contracts.state.invariants[0]" in str(exc_info.value)

    def test_invariant_referencing_function_call_is_rejected(self):
        spec = self._spec_with_invariants(["state.count >= 0", "len(state.messages) > 0"])
        with pytest.raises(BlueprintCompilationError) as exc_info:
            compile_blueprint(spec)
        assert "contracts.state.invariants[1]" in str(exc_info.value)
