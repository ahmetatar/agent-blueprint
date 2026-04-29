"""Tests for BlueprintSpec validation."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> dict:
    return load_blueprint_yaml(FIXTURES / name)


class TestValidBlueprints:
    def test_contracts_block_loads(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "state": {
                "fields": {
                    "messages": {"type": "array", "default": []},
                    "request_id": {"type": "string", "default": None, "nullable": True},
                    "route": {"type": "string", "default": None, "nullable": True},
                    "final_answer": {"type": "string", "default": None, "nullable": True},
                    "research_findings": {"type": "array", "default": []},
                }
            },
            "graph": {
                "entry_point": "router",
                "nodes": {
                    "router": {"agent": "router_agent"},
                    "writer": {"agent": "writer_agent"},
                },
                "edges": [],
            },
            "agents": {
                "router_agent": {"model": "gpt-4o"},
                "writer_agent": {"model": "gpt-4o"},
            },
            "contracts": {
                "state": {
                    "required_fields": ["messages"],
                    "immutable_fields": ["request_id"],
                    "invariants": ["state.route in [null, 'billing', 'support', 'sales']"],
                },
                "nodes": {
                    "router": {
                        "requires": ["messages"],
                        "produces": ["route"],
                        "forbids_mutation": ["final_answer"],
                    },
                    "writer": {
                        "requires": ["research_findings"],
                        "produces": ["final_answer"],
                        "output_contract": "final_answer_contract",
                    },
                },
                "outputs": {
                    "final_answer_contract": {
                        "type": "object",
                        "required": ["answer", "confidence"],
                        "properties": {
                            "answer": {"type": "string"},
                            "confidence": {"type": "number"},
                        },
                    }
                },
            },
        })
        assert spec.contracts is not None
        assert spec.contracts.state.required_fields == ["messages"]
        assert spec.contracts.nodes["router"].forbids_mutation == ["final_answer"]
        assert spec.contracts.outputs["final_answer_contract"].type == "object"

    def test_basic_chatbot_loads(self):
        raw = load("basic_chatbot.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.blueprint.name == "basic-chatbot"
        assert "assistant" in spec.agents
        assert spec.graph.entry_point == "assistant"

    def test_variable_interpolation(self):
        raw = load("basic_chatbot.yml")
        spec = BlueprintSpec.model_validate(raw)
        # ${settings.default_model} should resolve to "gpt-4o"
        assert spec.agents["assistant"].model == "gpt-4o"

    def test_customer_support_loads(self):
        raw = load("customer_support.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.blueprint.name == "customer-support-agent"
        assert len(spec.agents) == 3
        assert len(spec.tools) == 2
        assert len(spec.graph.nodes) == 3

    def test_customer_support_edges(self):
        raw = load("customer_support.yml")
        spec = BlueprintSpec.model_validate(raw)
        # router should have conditional edges
        router_edges = [e for e in spec.graph.edges if e.from_node == "router"]
        assert len(router_edges) == 1
        targets = router_edges[0].get_targets()
        assert len(targets) == 3

    def test_rag_blueprint_loads(self):
        raw = load("rag_agent.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert "support_docs" in spec.retrievers
        assert spec.tools["search_kb"].retriever == "support_docs"
        assert spec.agents["assistant"].rag is not None
        assert spec.agents["assistant"].rag.retrieval_tool == "search_kb"

    def test_harness_block_loads(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            "harness": {
                "defaults": {
                    "llm_mode": "mock",
                    "tool_mode": "stub",
                    "seed": 42,
                    "freeze_env": ["OPENAI_API_KEY"],
                    "normalize": {
                        "whitespace": True,
                        "timestamps": True,
                        "ids": True,
                    },
                },
                "scenarios": [
                    {
                        "id": "refund_happy_path",
                        "input": {"message": "I want a refund"},
                        "expected": {
                            "route": "billing",
                            "tools_called": ["lookup_invoice", "issue_refund"],
                            "output_contract": "refund_response",
                            "state_assertions": ["state.route == 'billing'"],
                            "artifacts": ["none"],
                            "approvals_triggered": True,
                        },
                    }
                ],
            },
        })
        assert spec.harness is not None
        assert spec.harness.defaults.llm_mode == "mock"
        assert spec.harness.defaults.tool_mode == "stub"
        assert spec.harness.scenarios[0].expected.route == "billing"
        assert spec.harness.scenarios[0].expected.tools_called == ["lookup_invoice", "issue_refund"]

    def test_harness_file_fields_are_accepted_for_non_breaking_compatibility(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            "harness": {
                "file": "harness/refund.yml",
                "files": ["harness/common.yml"],
                "scenarios": [
                    {
                        "id": "inline_case",
                        "input": {},
                        "expected": {},
                    }
                ],
            },
        })
        assert spec.harness is not None
        assert spec.harness.file == "harness/refund.yml"
        assert spec.harness.files == ["harness/common.yml"]

    def test_harness_fixture_blocks_load(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"agent": "assistant"}}, "edges": []},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "harness": {
                "defaults": {
                    "fixtures": {
                        "llm_outputs": {
                            "n": [{"content": "default reply"}],
                        },
                        "tool_outputs": {
                            "lookup_invoice": {"result": {"status": "paid"}},
                        },
                    }
                },
                "scenarios": [
                    {
                        "id": "fixture_case",
                        "input": {"message": "hello"},
                        "expected": {},
                        "fixtures": {
                            "tool_outputs": {
                                "issue_refund": [{"result": {"approved": True}}],
                            }
                        },
                    }
                ],
            },
        })
        assert spec.harness is not None
        assert spec.harness.defaults.fixtures.llm_outputs["n"][0]["content"] == "default reply"
        assert spec.harness.scenarios[0].fixtures.tool_outputs["issue_refund"][0]["result"]["approved"] is True

    def test_harness_replay_trace_fields_load(self):
        spec = BlueprintSpec.model_validate({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            "harness": {
                "defaults": {"replay_trace": "/tmp/golden-trace.json"},
                "scenarios": [
                    {
                        "id": "replay_case",
                        "input": {},
                        "expected": {},
                        "replay_trace": "/tmp/scenario-trace.json",
                    }
                ],
            },
        })
        assert spec.harness is not None
        assert spec.harness.defaults.replay_trace == "/tmp/golden-trace.json"
        assert spec.harness.scenarios[0].replay_trace == "/tmp/scenario-trace.json"


class TestMcpTools:
    def test_mcp_blueprint_loads(self):
        raw = load("mcp_tools.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert "stitch" in spec.mcp_servers
        assert "filesystem" in spec.mcp_servers

    def test_mcp_server_transports(self):
        raw = load("mcp_tools.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.mcp_servers["stitch"].transport == "sse"
        assert spec.mcp_servers["stitch"].url == "http://localhost:3100/sse"
        assert spec.mcp_servers["filesystem"].transport == "stdio"
        assert spec.mcp_servers["filesystem"].command == "npx"

    def test_mcp_tool_references(self):
        raw = load("mcp_tools.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.tools["create_project"].type == "mcp"
        assert spec.tools["create_project"].server == "stitch"
        assert spec.tools["create_project"].tool == "create_project"
        assert spec.tools["generate_screen"].server == "stitch"
        assert spec.tools["read_file"].server == "filesystem"

    def test_mcp_tool_undefined_server_raises(self):
        raw = load("mcp_tools.yml")
        raw["tools"]["bad_tool"] = {
            "type": "mcp",
            "server": "nonexistent_server",
            "tool": "some_tool",
        }
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate(raw)
        assert "nonexistent_server" in str(exc_info.value)

    def test_mcp_tool_missing_server_field_raises(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "mcp_servers": {"stitch": {"transport": "sse", "url": "http://localhost:3100/sse"}},
                "tools": {"t": {"type": "mcp", "tool": "create_project"}},  # server eksik
                "graph": {"entry_point": "n", "nodes": {"n": {}}, "edges": []},
            })

    def test_mcp_server_stdio_missing_command_raises(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "mcp_servers": {"fs": {"transport": "stdio"}},  # command eksik
                "graph": {"entry_point": "n", "nodes": {"n": {}}, "edges": []},
            })


class TestInvalidBlueprints:
    def test_contracts_unknown_graph_node_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "state": {"fields": {"messages": {"type": "array", "default": []}}},
                "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
                "contracts": {"nodes": {"missing_node": {"requires": ["messages"]}}},
            })
        assert "missing_node" in str(exc_info.value)

    def test_contracts_unknown_state_field_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "state": {"fields": {"messages": {"type": "array", "default": []}}},
                "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
                "contracts": {"state": {"required_fields": ["missing_field"]}},
            })
        assert "missing_field" in str(exc_info.value)

    def test_contracts_unknown_output_contract_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "state": {"fields": {"messages": {"type": "array", "default": []}}},
                "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
                "contracts": {"nodes": {"n": {"output_contract": "missing_contract"}}},
            })
        assert "missing_contract" in str(exc_info.value)

    def test_legacy_agent_output_schema_is_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "state": {"fields": {"messages": {"type": "array", "default": []}}},
                "agents": {
                    "assistant": {
                        "model": "gpt-4o",
                        "output_schema": {
                            "route": {"type": "string"},
                        },
                    }
                },
                "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            })
        assert "output_schema is no longer supported" in str(exc_info.value)

    def test_missing_agent_reference(self):
        raw = load("invalid_missing_agent.yml")
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate(raw)
        assert "nonexistent_agent" in str(exc_info.value)

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                # missing 'graph' field
            })

    def test_invalid_entry_point(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "graph": {
                    "entry_point": "nonexistent",
                    "nodes": {"real_node": {"agent": None, "type": "function"}},
                    "edges": [],
                }
            })

    def test_retrieval_tool_undefined_retriever_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "tools": {"search": {"type": "retrieval", "retriever": "missing"}},
                "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            })
        assert "missing" in str(exc_info.value)

    def test_agent_rag_must_reference_retrieval_tool(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "tools": {"search": {"type": "function"}},
                "agents": {"a": {"rag": {"tool": "search"}}},
                "graph": {"entry_point": "n", "nodes": {"n": {"agent": "a"}}, "edges": []},
            })
        assert "retrieval tool" in str(exc_info.value)

    def test_harness_duplicate_scenario_ids_raise(self):
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
                "harness": {
                    "scenarios": [
                        {"id": "dup", "input": {}, "expected": {}},
                        {"id": "dup", "input": {}, "expected": {}},
                    ]
                },
            })
        assert "duplicate id" in str(exc_info.value)
