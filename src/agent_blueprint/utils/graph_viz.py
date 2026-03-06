"""Mermaid and ASCII graph visualization utilities."""

from agent_blueprint.models.blueprint import BlueprintSpec


def to_mermaid(spec: BlueprintSpec) -> str:
    """Generate a Mermaid flowchart from a BlueprintSpec."""
    lines = ["flowchart TD"]

    # Node definitions
    for node_name, node in spec.graph.nodes.items():
        label = node.description or (node.agent or node_name)
        safe_name = node_name.replace("-", "_")
        lines.append(f"    {safe_name}[{label}]")

    lines.append("    END([END])")

    # Entry point marker
    entry = spec.graph.entry_point.replace("-", "_")
    lines.append(f"    START([START]) --> {entry}")

    # Edge definitions
    for edge in spec.graph.edges:
        from_name = edge.from_node.replace("-", "_")
        targets = edge.get_targets()

        if len(targets) == 1 and targets[0].condition is None:
            target = targets[0].target.replace("-", "_")
            lines.append(f"    {from_name} --> {target}")
        else:
            for t in targets:
                target = t.target.replace("-", "_")
                if t.condition:
                    safe_cond = t.condition.replace('"', "'")
                    lines.append(f'    {from_name} -->|"{safe_cond}"| {target}')
                else:
                    lines.append(f"    {from_name} --> {target}")

    return "\n".join(lines)
