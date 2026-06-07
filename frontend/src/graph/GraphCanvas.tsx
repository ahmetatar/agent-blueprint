import { useEffect, useState } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { GraphViewModel, LintFinding } from "../api";
import { nodeTypes } from "./nodes";
import { toFlow } from "./toFlow";

interface Props {
  graph: GraphViewModel;
  lint: LintFinding[];
}

export function GraphCanvas({ graph, lint }: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  useEffect(() => {
    let cancelled = false;
    toFlow(graph, lint).then((flow) => {
      if (cancelled) return;
      setNodes(flow.nodes);
      setEdges(flow.edges);
    });
    return () => {
      cancelled = true;
    };
  }, [graph, lint]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.2}
      nodesDraggable
      nodesConnectable={false}
      edgesFocusable={false}
    >
      <Background gap={18} />
      <MiniMap pannable zoomable />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
