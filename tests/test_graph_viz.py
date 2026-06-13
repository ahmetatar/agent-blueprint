"""Tests for Mermaid rendering — node labels must survive arbitrary description text."""

from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.graph_viz import to_mermaid


def _spec(description: str) -> BlueprintSpec:
    return BlueprintSpec.model_validate({
        "blueprint": {"name": "viz"},
        "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
        "agents": {"a": {"model": "gpt-4o", "system_prompt": "hi"}},
        "graph": {
            "entry_point": "n",
            "nodes": {"n": {"agent": "a", "description": description}},
            "edges": [{"from": "n", "to": "END"}],
        },
    })


def test_plain_label_is_quoted():
    out = to_mermaid(_spec("Telemetry check-in"))
    assert 'n["Telemetry check-in"]' in out


def test_special_chars_label_is_safe():
    # em dash, brackets, embedded quotes, and a newline — all previously broke
    # Mermaid because labels were emitted unquoted.
    out = to_mermaid(_spec('Second-pass — reuse [x] with "q"\nand more'))
    node_lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("n[")]
    assert len(node_lines) == 1
    line = node_lines[0]
    assert line.startswith('n["') and line.endswith('"]')
    assert "#quot;" in line and '"q"' not in line   # embedded quote entity-encoded
    assert "[x]" in line                            # brackets survive inside quotes
    assert "and more" in line                       # newline collapsed onto one line
