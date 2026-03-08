"""Mermaid and ASCII graph visualization utilities."""

from agent_blueprint.models.blueprint import BlueprintSpec


def _safe_id(name: str) -> str:
    """Convert a node name to a Mermaid-safe identifier."""
    # END and START are reserved keywords in Mermaid's parser
    if name == "END":
        return "__END__"
    if name == "START":
        return "__START__"
    return name.replace("-", "_")


def to_mermaid(spec: BlueprintSpec) -> str:
    """Generate a Mermaid flowchart from a BlueprintSpec."""
    lines = ["flowchart TD"]

    # Node definitions
    for node_name, node in spec.graph.nodes.items():
        label = node.description or (node.agent or node_name)
        safe_name = _safe_id(node_name)
        lines.append(f"    {safe_name}[{label}]")

    lines.append("    __END__([END])")

    # Entry point marker
    entry = _safe_id(spec.graph.entry_point)
    lines.append(f"    __START__([START]) --> {entry}")

    # Edge definitions
    for edge in spec.graph.edges:
        from_name = _safe_id(edge.from_node)
        targets = edge.get_targets()

        if len(targets) == 1 and targets[0].condition is None:
            target = _safe_id(targets[0].target)
            lines.append(f"    {from_name} --> {target}")
        else:
            for t in targets:
                target = _safe_id(t.target)
                if t.condition:
                    safe_cond = t.condition.replace('"', "'")
                    lines.append(f'    {from_name} -->|"{safe_cond}"| {target}')
                else:
                    lines.append(f"    {from_name} --> {target}")

    return "\n".join(lines)
