"""Tests for the unbounded-loop lint check."""

from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def lint(raw: dict):
    spec = BlueprintSpec.model_validate(raw)
    return [f for f in lint_blueprint(spec, compile_blueprint(spec)) if f.code == "unbounded-loop"]


def _base(nodes: dict, edges: list, entry: str = "a", extra: dict | None = None) -> dict:
    raw = {
        "blueprint": {"name": "loops"},
        "state": {"fields": {
            "messages": {"type": "list[message]", "reducer": "append"},
            "done": {"type": "boolean"},
        }},
        "graph": {"entry_point": entry, "nodes": nodes, "edges": edges},
    }
    if extra:
        raw.update(extra)
    return raw


class TestUnboundedLoops:
    def test_two_node_loop_without_exit_is_error(self):
        findings = lint(_base(
            {"a": {"type": "function"}, "b": {"type": "function"}},
            [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
        ))
        assert len(findings) == 1
        assert findings[0].severity.value == "error"
        assert "a -> b" in findings[0].message

    def test_self_loop_without_exit_is_error(self):
        findings = lint(_base(
            {"a": {"type": "function"}},
            [{"from": "a", "to": "a"}],
        ))
        assert len(findings) == 1
        assert "a" in findings[0].message

    def test_loop_with_conditional_end_exit_is_clean(self):
        findings = lint(_base(
            {"a": {"type": "function"}, "b": {"type": "function"}},
            [
                {"from": "a", "to": "b"},
                {"from": "b", "to": [
                    {"condition": "state.done == true", "target": "END"},
                    {"default": "a"},
                ]},
            ],
        ))
        assert findings == []

    def test_loop_with_exit_to_outside_node_is_clean(self):
        findings = lint(_base(
            {"a": {"type": "function"}, "b": {"type": "function"}, "c": {"type": "function"}},
            [
                {"from": "a", "to": "b"},
                {"from": "b", "to": [
                    {"condition": "state.done == true", "target": "c"},
                    {"default": "a"},
                ]},
                {"from": "c", "to": "END"},
            ],
        ))
        assert findings == []

    def test_terminating_chain_is_clean(self):
        findings = lint(_base(
            {"a": {"type": "function"}, "b": {"type": "function"}},
            [{"from": "a", "to": "b"}, {"from": "b", "to": "END"}],
        ))
        assert findings == []

    def test_loop_inside_subgraph_detected(self):
        findings = lint(_base(
            {"wf": {
                "type": "subgraph",
                "ref": "sg",
                "input_map": {"done": "x"},
                "output_map": {"x": "done"},
            }},
            [{"from": "wf", "to": "END"}],
            entry="wf",
            extra={"subgraphs": {"sg": {
                "entry_point": "p",
                "nodes": {"p": {"type": "function"}, "q": {"type": "function"}},
                "edges": [{"from": "p", "to": "q"}, {"from": "q", "to": "p"}],
            }}},
        ))
        assert len(findings) == 1
        assert findings[0].location.startswith("subgraphs.sg")

    def test_parallel_join_loop_with_exit_is_clean(self):
        # join routes back to the fan-out, but with a conditional END exit
        findings = lint(_base(
            {
                "fan": {"type": "parallel", "branches": ["b1", "b2"], "join": "m"},
                "b1": {"type": "function"},
                "b2": {"type": "function"},
                "m": {"type": "function"},
            },
            [{"from": "m", "to": [
                {"condition": "state.done == true", "target": "END"},
                {"default": "fan"},
            ]}],
            entry="fan",
        ))
        assert findings == []

    def test_parallel_join_loop_without_exit_is_error(self):
        findings = lint(_base(
            {
                "fan": {"type": "parallel", "branches": ["b1", "b2"], "join": "m"},
                "b1": {"type": "function"},
                "b2": {"type": "function"},
                "m": {"type": "function"},
            },
            [{"from": "m", "to": "fan"}],
            entry="fan",
        ))
        assert len(findings) == 1
        assert "fan" in findings[0].message
