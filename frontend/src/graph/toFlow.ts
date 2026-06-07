import type { Edge, Node } from "@xyflow/react";
import { MarkerType } from "@xyflow/react";
import type { GraphViewModel, LintFinding, NodePosition, VmNode } from "../api";
import { layoutGraph } from "./layout";

export interface FlowGraph {
  nodes: Node[];
  edges: Edge[];
}

/** Map a lint location to the canvas node it badges (top-level nodes only). */
function findingNodeId(finding: LintFinding): string | null {
  const match = /^graph\.nodes\.([^.[]+)$/.exec(finding.location);
  return match ? match[1] : null;
}

function rfNodeType(node: VmNode): string {
  if (node.type === "start" || node.type === "end") return "terminal";
  if (node.type === "subgraph" && node.expanded) return "group";
  return "blueprint";
}

const EDGE_STYLE: Record<string, Partial<Edge>> = {
  conditional: { style: { stroke: "#7c5cff" }, labelStyle: { fill: "#5b41d6", fontSize: 11 } },
  default: { style: { stroke: "#9aa0a6", strokeDasharray: "6 4" }, labelStyle: { fill: "#9aa0a6", fontSize: 11 } },
  entry: { style: { stroke: "#1b7f3b" } },
  delegation: { animated: true, style: { stroke: "#0b6bcb", strokeDasharray: "4 3" } },
  return: { style: { stroke: "#0b6bcb", strokeDasharray: "2 5", opacity: 0.45 } },
  parallel: { style: { stroke: "#c77700" } },
  normal: { style: { stroke: "#6b7280" } },
};

/**
 * View-model + lint findings → laid-out React Flow nodes/edges.
 *
 * `saved` positions (the layout sidecar, parent-relative like React Flow's
 * own coordinates) win over the ELK pass per node; ELK still provides sizes
 * and the positions of any node the sidecar doesn't know yet.
 */
export async function toFlow(
  graph: GraphViewModel,
  lint: LintFinding[],
  saved: Record<string, NodePosition> = {},
): Promise<FlowGraph> {
  const boxes = await layoutGraph(graph);

  const badges = new Map<string, LintFinding[]>();
  for (const finding of lint) {
    const nodeId = findingNodeId(finding);
    if (nodeId === null) continue;
    badges.set(nodeId, [...(badges.get(nodeId) ?? []), finding]);
  }

  // Parents must precede children in React Flow's node array; the view-model
  // already emits groups before their members, so order is preserved.
  const nodes: Node[] = graph.nodes.map((node) => {
    const box = boxes.get(node.id);
    const isGroup = rfNodeType(node) === "group";
    const position = saved[node.id] ?? { x: box?.x ?? 0, y: box?.y ?? 0 };
    return {
      id: node.id,
      type: rfNodeType(node),
      position,
      // Explicit dimensions (from the layout pass) keep edge anchors and the
      // minimap correct before the DOM has measured the custom nodes.
      width: box?.width,
      height: box?.height,
      data: { vm: node, findings: badges.get(node.id) ?? [] },
      // Terminals are synthetic; everything else can be deleted via ops.
      deletable: node.type !== "start" && node.type !== "end",
      ...(node.parent !== null ? { parentId: node.parent, extent: "parent" as const } : {}),
      ...(isGroup
        ? { style: { width: box?.width ?? 240, height: box?.height ?? 120 }, zIndex: -1 }
        : {}),
    };
  });

  const edges: Edge[] = graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    label: edge.label ?? undefined,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    // Only edges that exist in the YAML (`ref` present) are selectable and
    // deletable; synthetic edges (entry/supervisor/parallel) are display-only.
    data: { ref: edge.ref ?? null },
    selectable: Boolean(edge.ref),
    deletable: Boolean(edge.ref),
    ...EDGE_STYLE[edge.kind],
  }));

  return { nodes, edges };
}
