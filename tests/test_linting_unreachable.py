"""Tests for the unreachable-node lint check, incl. escalation-target reachability."""

from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def lint(raw: dict):
    spec = BlueprintSpec.model_validate(raw)
    return [f for f in lint_blueprint(spec, compile_blueprint(spec)) if f.code == "unreachable-node"]


def _base(nodes: dict, edges: list, entry: str = "a", extra: dict | None = None) -> dict:
    raw = {
        "blueprint": {"name": "reach"},
        "state": {"fields": {
            "messages": {"type": "list[message]", "reducer": "append"},
            "confidence": {"type": "number", "nullable": True},
        }},
        "graph": {"entry_point": entry, "nodes": nodes, "edges": edges},
    }
    if extra:
        raw.update(extra)
    return raw


_ESCALATION = {"policies": {"escalation": {"on_low_confidence": "esc", "confidence_threshold": 0.7}}}


class TestUnreachableNodes:
    def test_orphan_node_without_policy_is_unreachable(self):
        # 'esc' has no incoming edge and no policy points at it → flagged.
        findings = lint(_base(
            {"a": {"type": "function"}, "esc": {"type": "function"}},
            [{"from": "a", "to": "END"}, {"from": "esc", "to": "END"}],
        ))
        assert len(findings) == 1
        assert "esc" in findings[0].message

    def test_escalation_target_reachable_via_policy_is_clean(self):
        # Same graph, but policies.escalation.on_low_confidence points at 'esc':
        # the generator wires it into every edge router, so it IS reachable.
        findings = lint(_base(
            {"a": {"type": "function"}, "esc": {"type": "function"}},
            [{"from": "a", "to": "END"}, {"from": "esc", "to": "END"}],
            extra=_ESCALATION,
        ))
        assert findings == []

    def test_truly_unreachable_node_still_flagged_with_policy(self):
        # 'dead' is reachable via neither an edge nor the escalation policy.
        findings = lint(_base(
            {
                "a": {"type": "function"},
                "esc": {"type": "function"},
                "dead": {"type": "function"},
            },
            [
                {"from": "a", "to": "END"},
                {"from": "esc", "to": "END"},
                {"from": "dead", "to": "END"},
            ],
            extra=_ESCALATION,
        ))
        assert len(findings) == 1
        assert "dead" in findings[0].message
