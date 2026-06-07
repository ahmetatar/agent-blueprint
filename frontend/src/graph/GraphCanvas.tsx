import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  applyOps,
  ConflictError,
  OpRejectedError,
  saveLayout,
  type BlueprintInfo,
  type EditOp,
  type EdgeRef,
  type GraphViewModel,
  type LintFinding,
  type NodePosition,
  type VmNode,
} from "../api";
import type { RunState } from "../App";
import { AddNodeDialog } from "./AddNodeDialog";
import { nodeTypes } from "./nodes";
import { toFlow } from "./toFlow";

interface Props {
  graph: GraphViewModel;
  lint: LintFinding[];
  layout: Record<string, NodePosition>;
  hash: string;
  runStates: Record<string, RunState>;
  onUpdated: (info: BlueprintInfo) => void;
  onConflict: () => void;
  onSelect: (nodeId: string | null) => void;
}

const SAVE_DEBOUNCE_MS = 400;
const TOAST_MS = 6000;

export function GraphCanvas({
  graph,
  lint,
  layout,
  hash,
  runStates,
  onUpdated,
  onConflict,
  onSelect,
}: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  // Drags applied locally but possibly not yet round-tripped through the
  // server; layered over the server layout so a live-reload refetch racing a
  // debounced save doesn't snap nodes back.
  const localDrags = useRef<Record<string, NodePosition>>({});
  const saveTimer = useRef<number | undefined>(undefined);
  const hashRef = useRef(hash);
  hashRef.current = hash;
  const runStatesRef = useRef(runStates);
  runStatesRef.current = runStates;

  const nodeById = useMemo(() => {
    const map = new Map<string, VmNode>();
    for (const node of graph.nodes) map.set(node.id, node);
    return map;
  }, [graph]);

  useEffect(() => {
    let cancelled = false;
    toFlow(graph, lint, { ...layout, ...localDrags.current }).then((flow) => {
      if (cancelled) return;
      // Re-apply live run states — a rebuild (live reload, applied op) must
      // not wipe the highlights of a run in progress.
      setNodes(
        flow.nodes.map((node) => ({
          ...node,
          data: { ...node.data, runState: runStatesRef.current[node.id] },
        })),
      );
      setEdges(flow.edges);
    });
    return () => {
      cancelled = true;
    };
  }, [graph, lint, layout]);

  useEffect(() => {
    // Live highlight updates: patch only the nodes whose state changed.
    setNodes((current) =>
      current.map((node) => {
        const next = runStates[node.id];
        return node.data.runState === next
          ? node
          : { ...node, data: { ...node.data, runState: next } };
      }),
    );
  }, [runStates]);

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  useEffect(() => {
    if (toast === null) return;
    const timer = window.setTimeout(() => setToast(null), TOAST_MS);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const submitOps = useCallback(
    (ops: EditOp[]) => {
      applyOps(hashRef.current, ops)
        .then(onUpdated)
        .catch((e) => {
          if (e instanceof ConflictError) {
            onConflict();
            setToast("File changed underneath — canvas refreshed, try again");
          } else if (e instanceof OpRejectedError) {
            setToast(`Edit rejected: ${e.message}`);
          } else {
            setToast(String(e));
          }
        });
    },
    [onUpdated, onConflict],
  );

  /** Map a canvas endpoint to its ops scope + YAML node id. */
  const opEndpoint = useCallback(
    (id: string): { graph: string; node: string } | null => {
      if (id === "__start__") return { graph: "graph", node: "START" };
      const vm = nodeById.get(id);
      if (!vm) return null;
      if (vm.type === "end") {
        if (vm.parent === null) return { graph: "graph", node: "END" };
        const group = nodeById.get(vm.parent); // group-internal END terminal
        return group?.ref ? { graph: `subgraphs.${group.ref}`, node: "END" } : null;
      }
      return { graph: vm.graph_ref ?? "graph", node: vm.label };
    },
    [nodeById],
  );

  const isValidConnection = useCallback(
    (connection: Connection | Edge) => {
      if (!connection.source || !connection.target) return false;
      const src = opEndpoint(connection.source);
      const tgt = opEndpoint(connection.target);
      return src !== null && tgt !== null && src.graph === tgt.graph;
    },
    [opEndpoint],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      const src = opEndpoint(connection.source);
      const tgt = opEndpoint(connection.target);
      if (!src || !tgt) return;
      if (src.graph !== tgt.graph) {
        setToast("Edges cannot cross a subgraph boundary");
        return;
      }
      submitOps([{ op: "add_edge", graph: src.graph, from_node: src.node, target: tgt.node }]);
    },
    [opEndpoint, submitOps],
  );

  // Drag an edge endpoint to a different node. Moving the target end is an
  // in-place retarget (keeps the entry's position in the `to` list — order
  // is routing semantics for overlapping conditions — plus its comments);
  // moving the source end relocates the entry to another edge's list.
  const onReconnect = useCallback(
    (oldEdge: Edge, connection: Connection) => {
      const ref = (oldEdge.data?.ref ?? null) as EdgeRef | null;
      if (!ref) return;
      const src = opEndpoint(connection.source);
      const tgt = opEndpoint(connection.target);
      if (!src || !tgt) return;
      if (src.graph !== ref.graph || tgt.graph !== ref.graph) {
        setToast("Edges cannot cross a subgraph boundary");
        return;
      }
      if (src.node === ref.from && tgt.node === ref.target) return; // dropped in place
      if (src.node === ref.from) {
        submitOps([
          {
            op: "retarget_edge",
            graph: ref.graph,
            from_node: ref.from,
            target: ref.target,
            condition: ref.condition,
            new_target: tgt.node,
          },
        ]);
      } else {
        submitOps([
          {
            op: "remove_edge",
            graph: ref.graph,
            from_node: ref.from,
            target: ref.target,
            condition: ref.condition,
          },
          {
            op: "add_edge",
            graph: ref.graph,
            from_node: src.node,
            target: ref.target,
            condition: ref.condition,
          },
        ]);
      }
    },
    [opEndpoint, submitOps],
  );

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

  // Removals are server-driven (the op response re-renders the canvas), so
  // drop `remove` changes here and let onDelete translate them into ops.
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      const kept = changes.filter((change) => change.type !== "remove");
      const dragEnded = changes.some(
        (change) => change.type === "position" && change.dragging === false,
      );
      setNodes((current) => {
        const next = applyNodeChanges(kept, current);
        if (dragEnded) persistPositions(next);
        return next;
      });
    },
    [persistPositions],
  );

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    const kept = changes.filter((change) => change.type !== "remove");
    setEdges((current) => applyEdgeChanges(kept, current));
  }, []);

  const [selection, setSelection] = useState<{ nodes: Node[]; edges: Edge[] }>({
    nodes: [],
    edges: [],
  });

  const onSelectionChange = useCallback(
    ({ nodes: selectedNodes, edges: selectedEdges }: { nodes: Node[]; edges: Edge[] }) => {
      setSelection({ nodes: selectedNodes, edges: selectedEdges });
      onSelect(selectedNodes.length === 1 ? selectedNodes[0].id : null);
    },
    [onSelect],
  );

  const onDelete = useCallback(
    ({ nodes: deletedNodes, edges: deletedEdges }: { nodes: Node[]; edges: Edge[] }) => {
      const removedIds = new Set(deletedNodes.map((node) => node.id));
      const ancestorRemoved = (vm: VmNode): boolean => {
        let parent = vm.parent;
        while (parent !== null) {
          if (removedIds.has(parent)) return true;
          parent = nodeById.get(parent)?.parent ?? null;
        }
        return false;
      };

      const ops: EditOp[] = [];
      const names: string[] = [];
      for (const node of deletedNodes) {
        const vm = nodeById.get(node.id);
        if (!vm || vm.type === "start" || vm.type === "end") continue;
        // Children of a deleted subgraph *instance* disappear with it on the
        // canvas, but must not be removed from the shared subgraph definition.
        if (ancestorRemoved(vm)) continue;
        ops.push({ op: "remove_node", graph: vm.graph_ref ?? "graph", node_id: vm.label });
        names.push(vm.label);
      }
      for (const edge of deletedEdges) {
        // remove_node cascades edge cleanup server-side.
        if (removedIds.has(edge.source) || removedIds.has(edge.target)) continue;
        const ref = (edge.data?.ref ?? null) as EdgeRef | null;
        if (!ref) continue;
        ops.push({
          op: "remove_edge",
          graph: ref.graph,
          from_node: ref.from,
          target: ref.target,
          condition: ref.condition,
        });
      }
      if (ops.length === 0) return;
      if (
        names.length > 0 &&
        !window.confirm(`Remove node(s) ${names.join(", ")}? Connected edges are removed too.`)
      ) {
        return;
      }
      submitOps(ops);
    },
    [nodeById, submitOps],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onReconnect={onReconnect}
      onDelete={onDelete}
      onSelectionChange={onSelectionChange}
      isValidConnection={isValidConnection}
      fitView
      minZoom={0.2}
      nodesDraggable
      nodesConnectable
      edgesFocusable
    >
      <Background gap={18} />
      <MiniMap pannable zoomable />
      <Controls showInteractive={false} />
      <Panel position="top-left">
        <button type="button" className="canvas-button" onClick={() => setAdding(true)}>
          + Node
        </button>
      </Panel>
      <SelectionToolbar selection={selection} />
      {toast !== null && (
        <Panel position="top-center">
          <div className="canvas-toast">
            <span>{toast}</span>
            <button type="button" onClick={() => setToast(null)}>
              ×
            </button>
          </div>
        </Panel>
      )}
      {adding && (
        <AddNodeDialog
          agents={graph.agents}
          existingIds={new Set(graph.nodes.map((node) => node.label))}
          onSubmit={(op) => {
            setAdding(false);
            submitOps([op]);
          }}
          onCancel={() => setAdding(false)}
        />
      )}
    </ReactFlow>
  );
}

/** Floating delete affordance for the current selection — the keyboard
 * shortcut existed since E2b but was undiscoverable. `deleteElements` routes
 * through the same onDelete → ops path as Backspace. */
function SelectionToolbar({ selection }: { selection: { nodes: Node[]; edges: Edge[] } }) {
  const { deleteElements } = useReactFlow();
  const nodes = selection.nodes.filter((node) => node.deletable !== false);
  const edges = selection.edges.filter((edge) => edge.deletable !== false);
  const count = nodes.length + edges.length;
  if (count === 0) return null;

  let label: string;
  if (count === 1 && edges.length === 1) {
    const ref = (edges[0].data?.ref ?? null) as EdgeRef | null;
    label = ref
      ? `edge ${ref.from} → ${ref.target}`
      : `edge ${edges[0].source} → ${edges[0].target}`;
  } else if (count === 1 && nodes.length === 1) {
    label = `node ${nodes[0].id}`;
  } else {
    label = `${count} selected`;
  }
  return (
    <Panel position="top-center">
      <div className="selection-toolbar">
        <span className="selection-label">{label}</span>
        <button
          type="button"
          className="selection-delete"
          title="Delete selection (Backspace)"
          onClick={() => void deleteElements({ nodes, edges })}
        >
          Delete ⌫
        </button>
      </div>
    </Panel>
  );
}
