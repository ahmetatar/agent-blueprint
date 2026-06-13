"""Tests for deep subgraph expansion — nesting, cycles, condition remapping."""

import pytest

from agent_blueprint.exceptions import BlueprintCompilationError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.ir.expression import rename_state_fields
from agent_blueprint.models.blueprint import BlueprintSpec


def compile_spec(raw: dict):
    return compile_blueprint(BlueprintSpec.model_validate(raw))


class TestRenameStateFields:
    def test_renames_mapped_fields(self):
        out = rename_state_fields("state.verdict == 'ok'", {"verdict": "wf__verdict"})
        assert out == "state.wf__verdict == 'ok'"

    def test_leaves_unmapped_fields(self):
        out = rename_state_fields(
            "state.verdict == 'ok' and state.global_flag", {"verdict": "wf__verdict"}
        )
        assert "state.wf__verdict" in out
        assert "state.global_flag" in out

    def test_empty_mapping_is_identity(self):
        assert rename_state_fields("state.x == 1", {}) == "state.x == 1"

    def test_invalid_expression_returned_unchanged(self):
        assert rename_state_fields("state.x ==", {"x": "y"}) == "state.x =="


class TestInnerConditionRemap:
    def test_inner_edge_condition_is_namespaced(self):
        ir = compile_spec({
            "blueprint": {"name": "cond"},
            "state": {"fields": {"q": {"type": "string"}, "verdict": {"type": "string"}}},
            "graph": {
                "entry_point": "wf",
                "nodes": {
                    "wf": {
                        "type": "subgraph",
                        "ref": "sg",
                        "input_map": {"q": "inner_q"},
                        "output_map": {"inner_v": "verdict"},
                    }
                },
                "edges": [{"from": "wf", "to": "END"}],
            },
            "subgraphs": {
                "sg": {
                    "entry_point": "a",
                    "nodes": {"a": {"type": "function"}, "b": {"type": "function"}},
                    "edges": [
                        {
                            "from": "a",
                            "to": [
                                {"condition": "state.inner_v == 'ok'", "target": "b"},
                                {"default": "END"},
                            ],
                        },
                        {"from": "b", "to": "END"},
                    ],
                },
            },
        })
        edge = next(e for e in ir.edges if e.from_node == "wf__a")
        conditional = next(t for t in edge.targets if t.condition is not None)
        assert "wf__inner_v" in conditional.condition.source

    def test_global_field_in_condition_not_remapped(self):
        ir = compile_spec({
            "blueprint": {"name": "cond2"},
            "state": {"fields": {"q": {"type": "string"}, "flag": {"type": "boolean"}}},
            "graph": {
                "entry_point": "wf",
                "nodes": {
                    "wf": {
                        "type": "subgraph",
                        "ref": "sg",
                        "input_map": {"q": "iq"},
                        "output_map": {"iq": "q"},
                    }
                },
                "edges": [{"from": "wf", "to": "END"}],
            },
            "subgraphs": {
                "sg": {
                    "entry_point": "a",
                    "nodes": {"a": {"type": "function"}, "b": {"type": "function"}},
                    "edges": [
                        {
                            "from": "a",
                            "to": [
                                {"condition": "state.flag == true", "target": "b"},
                                {"default": "END"},
                            ],
                        },
                        {"from": "b", "to": "END"},
                    ],
                },
            },
        })
        edge = next(e for e in ir.edges if e.from_node == "wf__a")
        conditional = next(t for t in edge.targets if t.condition is not None)
        assert "state.flag" in conditional.condition.source
        assert "wf__flag" not in conditional.condition.source


def _nested_spec() -> dict:
    return {
        "blueprint": {"name": "nested"},
        "state": {"fields": {"q": {"type": "string"}, "r": {"type": "string"}}},
        "graph": {
            "entry_point": "outer",
            "nodes": {
                "outer": {
                    "type": "subgraph",
                    "ref": "sg1",
                    "input_map": {"q": "iq"},
                    "output_map": {"ires": "r"},
                }
            },
            "edges": [{"from": "outer", "to": "END"}],
        },
        "subgraphs": {
            "sg1": {
                "entry_point": "inner",
                "nodes": {
                    "inner": {
                        "type": "subgraph",
                        "ref": "sg2",
                        "input_map": {"iq": "x"},
                        "output_map": {"y": "ires"},
                    }
                },
                "edges": [{"from": "inner", "to": "END"}],
            },
            "sg2": {
                "entry_point": "f",
                "nodes": {"f": {"type": "function"}},
                "edges": [{"from": "f", "to": "END"}],
            },
        },
    }


class TestNestedSubgraphs:
    def test_two_level_expansion_node_ids(self):
        ir = compile_spec(_nested_spec())
        ids = {n.id for n in ir.nodes}
        assert ids == {
            "outer__entry",
            "outer__exit",
            "outer__inner__entry",
            "outer__inner__exit",
            "outer__inner__f",
        }

    def test_two_level_state_keys_chain(self):
        ir = compile_spec(_nested_spec())
        keys = set(ir.state.fields)
        # pass 1 namespaces sg1 fields, pass 2 namespaces sg2 fields
        assert "outer__iq" in keys
        assert "outer__inner__x" in keys
        assert "outer__inner__y" in keys

    def test_entry_point_resolves_through_nesting(self):
        ir = compile_spec(_nested_spec())
        assert ir.entry_point == "outer__entry"

    def test_inner_adapter_maps_namespaced_parent_field(self):
        ir = compile_spec(_nested_spec())
        entry = next(n for n in ir.nodes if n.id == "outer__inner__entry")
        # the nested node's outer-facing input field was remapped in pass 1
        assert entry.node_def.metadata["input_map"] == {"outer__iq": "x"}

    def test_reference_cycle_detected(self):
        raw = _nested_spec()
        # make sg2 point back to sg1
        raw["subgraphs"]["sg2"]["nodes"]["f"] = {
            "type": "subgraph",
            "ref": "sg1",
            "input_map": {"x": "iq"},
            "output_map": {"ires": "y"},
        }
        with pytest.raises(BlueprintCompilationError, match="cycle"):
            compile_spec(raw)

    def test_cycle_error_renders_ref_chain(self):
        raw = _nested_spec()
        raw["subgraphs"]["sg2"]["nodes"]["f"] = {
            "type": "subgraph",
            "ref": "sg1",
            "input_map": {"x": "iq"},
            "output_map": {"ires": "y"},
        }
        with pytest.raises(BlueprintCompilationError, match="sg1 -> sg2 -> sg1"):
            compile_spec(raw)

    def test_parallel_inside_subgraph_still_namespaced(self):
        ir = compile_spec({
            "blueprint": {"name": "psg"},
            "state": {"fields": {"q": {"type": "string"}}},
            "graph": {
                "entry_point": "wf",
                "nodes": {
                    "wf": {
                        "type": "subgraph",
                        "ref": "sg",
                        "input_map": {"q": "iq"},
                        "output_map": {"iq": "q"},
                    }
                },
                "edges": [{"from": "wf", "to": "END"}],
            },
            "subgraphs": {
                "sg": {
                    "entry_point": "fan",
                    "nodes": {
                        "fan": {"type": "parallel", "branches": ["b1", "b2"], "join": "m"},
                        "b1": {"type": "function"},
                        "b2": {"type": "function"},
                        "m": {"type": "function"},
                    },
                    "edges": [{"from": "m", "to": "END"}],
                },
            },
        })
        fan = next(n for n in ir.nodes if n.id == "wf__fan")
        assert fan.node_def.branches == ["wf__b1", "wf__b2"]
        assert fan.node_def.join == "wf__m"


class TestSupervisorOnFinishToSubgraph:
    """A supervisor's on_finish targeting a subgraph node must be remapped to
    that subgraph's entry adapter, or the generated Command(goto=...) hits an
    unknown node at runtime."""

    def _spec(self) -> dict:
        return {
            "blueprint": {"name": "sup-sub"},
            "state": {"fields": {
                "messages": {"type": "list[message]", "reducer": "append"},
                "plan": {"type": "list", "default": []},
                "reviewed": {"type": "list", "default": []},
            }},
            "agents": {
                "lead": {"model": "gpt-4o"},
                "w": {"model": "gpt-4o"},
                "rev": {"model": "gpt-4o"},
            },
            "graph": {
                "entry_point": "boss",
                "nodes": {
                    "boss": {
                        "type": "supervisor",
                        "agent": "lead",
                        "workers": ["w"],
                        "on_finish": "gate",
                    },
                    "w": {"agent": "w"},
                    "gate": {
                        "type": "subgraph",
                        "ref": "review",
                        "input_map": {"plan": "inner_plan"},
                        "output_map": {"inner_reviewed": "reviewed"},
                    },
                },
                "edges": [{"from": "gate", "to": "END"}],
            },
            "subgraphs": {
                "review": {
                    "entry_point": "r",
                    "nodes": {"r": {"agent": "rev"}},
                    "edges": [{"from": "r", "to": "END"}],
                },
            },
        }

    def test_on_finish_remapped_to_subgraph_entry(self):
        ir = compile_spec(self._spec())
        ids = {n.id for n in ir.nodes}
        assert "gate__entry" in ids
        boss = next(n for n in ir.nodes if n.id == "boss")
        assert boss.node_def.on_finish == "gate__entry"
