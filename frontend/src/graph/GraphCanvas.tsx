import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyNodeChanges,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { saveLayout, type GraphViewModel, type LintFinding, type NodePosition } from "../api";
import { nodeTypes } from "./nodes";
import { toFlow } from "./toFlow";

interface Props {
  graph: GraphViewModel;
  lint: LintFinding[];
  layout: Record<string, NodePosition>;
}

const SAVE_DEBOUNCE_MS = 400;

export function GraphCanvas({ graph, lint, layout }: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  // Drags applied locally but possibly not yet round-tripped through the
  // server; layered over the server layout so a live-reload refetch racing a
  // debounced save doesn't snap nodes back.
  const localDrags = useRef<Record<string, NodePosition>>({});
  const saveTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    toFlow(graph, lint, { ...layout, ...localDrags.current }).then((flow) => {
      if (cancelled) return;
      setNodes(flow.nodes);
      setEdges(flow.edges);
    });
    return () => {
      cancelled = true;
    };
  }, [graph, lint, layout]);

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  const persistPositions = useCallback((current: Node[]) => {
    const positions: Record<string, NodePosition> = {};
    for (const node of current) {
      positions[node.id] = { x: node.position.x, y: node.position.y };
    }
    localDrags.current = positions;
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveLayout(positions).catch(() => {
        // Layout is editor-private convenience state — a failed save costs
        // only this session's positions, so don't interrupt the user.
      });
    }, SAVE_DEBOUNCE_MS);
  }, []);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const dragEnded = changes.some(
        (change) => change.type === "position" && change.dragging === false,
      );
      setNodes((current) => {
        const next = applyNodeChanges(changes, current);
        if (dragEnded) persistPositions(next);
        return next;
      });
    },
    [persistPositions],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
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
