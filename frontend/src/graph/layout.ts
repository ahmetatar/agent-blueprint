import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk-api";
import type { GraphViewModel, VmNode } from "../api";

const elk = new ELK();

export interface LayoutBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** Estimated render sizes per node kind; React Flow nodes are styled to match. */
export function nodeSize(node: VmNode): { width: number; height: number } {
  if (node.type === "start" || node.type === "end") return { width: 90, height: 36 };
  if (node.type === "subgraph" && node.expanded) return { width: 240, height: 120 }; // ELK overrides via children
  return { width: 220, height: node.model ? 78 : 60 };
}

/**
 * Hierarchical auto-layout. Subgraph groups become ELK compound nodes, so
 * child coordinates come back relative to their parent — exactly what React
 * Flow's parentId positioning expects.
 */
export async function layoutGraph(graph: GraphViewModel): Promise<Map<string, LayoutBox>> {
  const childrenOf = new Map<string | null, VmNode[]>();
  for (const node of graph.nodes) {
    const list = childrenOf.get(node.parent) ?? [];
    list.push(node);
    childrenOf.set(node.parent, list);
  }

  const toElk = (node: VmNode): ElkNode => {
    const children = childrenOf.get(node.id) ?? [];
    const size = nodeSize(node);
    return {
      id: node.id,
      width: size.width,
      height: size.height,
      children: children.map(toElk),
      layoutOptions:
        children.length > 0
          ? { "elk.padding": "[top=44,left=18,bottom=18,right=18]" }
          : undefined,
    };
  };

  const root: ElkNode = {
    id: "__root__",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.hierarchyHandling": "INCLUDE_CHILDREN",
      "elk.layered.spacing.nodeNodeBetweenLayers": "60",
      "elk.spacing.nodeNode": "40",
      "elk.spacing.componentComponent": "60",
    },
    children: (childrenOf.get(null) ?? []).map(toElk),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      sources: [edge.source],
      targets: [edge.target],
    })),
  };

  const laidOut = await elk.layout(root);
  const boxes = new Map<string, LayoutBox>();
  const collect = (elkNode: ElkNode) => {
    for (const child of elkNode.children ?? []) {
      boxes.set(child.id, {
        x: child.x ?? 0,
        y: child.y ?? 0,
        width: child.width ?? 0,
        height: child.height ?? 0,
      });
      collect(child);
    }
  };
  collect(laidOut);
  return boxes;
}
