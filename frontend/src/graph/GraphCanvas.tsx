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
  validateExpression,
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
      <SelectionToolbar
        selection={selection}
        stateFields={graph.state_fields}
        submitOps={submitOps}
      />
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
          subgraphs={graph.subgraphs}
          nodeIds={graph.nodes
            .filter(
              (node) =>
                node.parent === null && node.type !== "start" && node.type !== "end",
            )
            .map((node) => node.label)}
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

/** Floating affordances for the current selection: a delete button (the
 * keyboard shortcut existed since E2b but was undiscoverable) and, for a
 * single real edge, an inline condition / default editor (E4b). `deleteElements`
 * routes through the same onDelete → ops path as Backspace. */
function SelectionToolbar({
  selection,
  stateFields,
  submitOps,
}: {
  selection: { nodes: Node[]; edges: Edge[] };
  stateFields: string[];
  submitOps: (ops: EditOp[]) => void;
}) {
  const { deleteElements } = useReactFlow();
  const nodes = selection.nodes.filter((node) => node.deletable !== false);
  const edges = selection.edges.filter((edge) => edge.deletable !== false);
  const count = nodes.length + edges.length;
  if (count === 0) return null;

  const soleEdgeRef =
    count === 1 && edges.length === 1
      ? ((edges[0].data?.ref ?? null) as EdgeRef | null)
      : null;

  let label: string;
  if (count === 1 && edges.length === 1) {
    label = soleEdgeRef
      ? `edge ${soleEdgeRef.from} → ${soleEdgeRef.target}`
      : `edge ${edges[0].source} → ${edges[0].target}`;
  } else if (count === 1 && nodes.length === 1) {
    label = `node ${nodes[0].id}`;
  } else {
    label = `${count} selected`;
  }
  return (
    <Panel position="top-center">
      <div className="selection-toolbar nodrag nopan">
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
      {soleEdgeRef && (
        <EdgeConditionEditor
          key={edges[0].id}
          edgeRef={soleEdgeRef}
          kind={(edges[0].data?.kind ?? "normal") as string}
          stateFields={stateFields}
          submitOps={submitOps}
        />
      )}
    </Panel>
  );
}

/** Inline editor for a route edge's `condition` / `default` flag (E4b). Lives
 * under the selection toolbar; applies as a single `set_edge_condition` op
 * after a live check by the expression parser (POST /api/expression/validate). */
function EdgeConditionEditor({
  edgeRef,
  kind,
  stateFields,
  submitOps,
}: {
  edgeRef: EdgeRef;
  kind: string;
  stateFields: string[];
  submitOps: (ops: EditOp[]) => void;
}) {
  const initialCondition = edgeRef.condition ?? "";
  const initialDefault = kind === "default";
  const [condition, setCondition] = useState(initialCondition);
  const [isDefault, setIsDefault] = useState(initialDefault);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const trimmed = condition.trim();
  const conditionActive = !isDefault && trimmed !== "";

  // Live-validate the typed condition (debounced); empty/default need no check.
  useEffect(() => {
    if (!conditionActive) {
      setError(null);
      setChecking(false);
      return;
    }
    setChecking(true);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      validateExpression(trimmed)
        .then((result) => {
          if (cancelled) return;
          setError(result.valid ? null : (result.error ?? "invalid condition"));
        })
        .catch(() => {
          if (!cancelled) setError(null); // network hiccup — let the server gate the write
        })
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [trimmed, conditionActive]);

  const insertField = (field: string) => {
    const token = `state.${field}`;
    const el = inputRef.current;
    setIsDefault(false);
    if (!el) {
      setCondition((current) => current + token);
      return;
    }
    const start = el.selectionStart ?? condition.length;
    const end = el.selectionEnd ?? condition.length;
    const next = condition.slice(0, start) + token + condition.slice(end);
    setCondition(next);
    requestAnimationFrame(() => {
      el.focus();
      const caret = start + token.length;
      el.setSelectionRange(caret, caret);
    });
  };

  const dirty = trimmed !== initialCondition.trim() || isDefault !== initialDefault;
  const blocked = checking || error !== null;

  const apply = () => {
    const newCondition = conditionActive ? trimmed : null;
    submitOps([
      {
        op: "set_edge_condition",
        graph: edgeRef.graph,
        from_node: edgeRef.from,
        target: edgeRef.target,
        condition: edgeRef.condition,
        new_condition: newCondition,
        new_default: newCondition === null ? isDefault : false,
      },
    ]);
  };

  return (
    <div
      className="edge-config nodrag nopan"
      onKeyDown={(e) => e.stopPropagation()} // don't let Backspace delete the edge
    >
      <label className="edge-config-row">
        <span className="edge-config-name">Condition</span>
        <input
          ref={inputRef}
          type="text"
          className="edge-config-input"
          placeholder="state.priority == 'high'"
          value={condition}
          disabled={isDefault}
          spellCheck={false}
          onChange={(e) => {
            setCondition(e.target.value);
            if (e.target.value.trim() !== "") setIsDefault(false);
          }}
        />
      </label>
      <label className="edge-config-default">
        <input
          type="checkbox"
          checked={isDefault}
          onChange={(e) => setIsDefault(e.target.checked)}
        />
        default (unconditional)
      </label>
      {stateFields.length > 0 && (
        <div className="edge-config-chips">
          <span className="edge-config-name">Insert</span>
          {stateFields.map((field) => (
            <button
              key={field}
              type="button"
              className="edge-config-chip"
              title={`Insert state.${field}`}
              onClick={() => insertField(field)}
            >
              {field}
            </button>
          ))}
        </div>
      )}
      {error !== null && <div className="edge-config-error">{error}</div>}
      <div className="edge-config-actions">
        <button
          type="button"
          className="edge-config-apply"
          disabled={!dirty || blocked}
          onClick={apply}
        >
          Apply
        </button>
      </div>
    </div>
  );
}
