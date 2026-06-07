// Types mirror the JSON contract of GET /api/blueprint (editor/server.py).

export interface VmNode {
  id: string;
  type: string; // agent | function | handoff | parallel | subgraph | supervisor | start | end
  label: string;
  parent: string | null;
  description?: string | null;
  agent?: string;
  provider?: string;
  model?: string;
  tools?: string[];
  action?: string | null;
  channel?: string;
  workers?: string[];
  max_iterations?: number;
  retry?: number;
  entry?: boolean;
  ref?: string;
  expanded?: boolean;
  graph_ref?: string; // ops scope: "graph" | "subgraphs.<name>"
  config?: Record<string, unknown>; // effective NodeDef values (defaults filled)
  agent_config?: Record<string, unknown>; // effective AgentDef values, when agent-backed
}

/** YAML address of a real edge target — absent on synthetic edges. */
export interface EdgeRef {
  graph: string;
  from: string;
  target: string;
  condition: string | null;
}

export interface VmEdge {
  id: string;
  source: string;
  target: string;
  kind: string; // normal | conditional | default | entry | delegation | return | parallel
  label: string | null;
  ref?: EdgeRef | null;
}

export interface GraphViewModel {
  entry_point: string;
  nodes: VmNode[];
  edges: VmEdge[];
  agents: string[];
  tools: string[];
}

export interface LintFinding {
  severity: "error" | "warning";
  code: string;
  location: string;
  message: string;
  line: number | null;
  col: number | null;
}

export interface NodePosition {
  x: number;
  y: number;
}

export interface BlueprintInfo {
  path: string;
  name: string | null;
  valid: boolean;
  error: string | null;
  yaml: string;
  graph: GraphViewModel | null;
  lint: LintFinding[];
  layout: Record<string, NodePosition>;
  hash: string;
}

export async function fetchBlueprint(): Promise<BlueprintInfo> {
  const response = await fetch("/api/blueprint");
  if (!response.ok) throw new Error(`API responded ${response.status}`);
  return (await response.json()) as BlueprintInfo;
}

/** The blueprint JSON Schema (BlueprintSpec.model_json_schema) — drives config forms. */
export async function fetchSchema(): Promise<Record<string, unknown>> {
  const response = await fetch("/api/schema");
  if (!response.ok) throw new Error(`API responded ${response.status}`);
  return (await response.json()) as Record<string, unknown>;
}

/** Thrown when a whole-file save is rejected (YAML syntax error — not written). */
export class SaveRejectedError extends Error {}

export async function saveYaml(yaml: string): Promise<BlueprintInfo> {
  const response = await fetch("/api/blueprint/yaml", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml }),
  });
  if (response.status === 422) {
    const body = (await response.json()) as { detail?: string };
    throw new SaveRejectedError(body.detail ?? "YAML could not be parsed");
  }
  if (!response.ok) throw new Error(`API responded ${response.status}`);
  return (await response.json()) as BlueprintInfo;
}

// Canvas ops (POST /api/blueprint/ops) — mirror editor/ops.py.

export interface AddNodeOp {
  op: "add_node";
  graph?: string;
  node_id: string;
  node: Record<string, unknown>;
}

export interface RemoveNodeOp {
  op: "remove_node";
  graph?: string;
  node_id: string;
}

export interface AddEdgeOp {
  op: "add_edge";
  graph?: string;
  from_node: string;
  target: string;
  condition?: string | null;
}

export interface RemoveEdgeOp {
  op: "remove_edge";
  graph?: string;
  from_node: string;
  target: string;
  condition?: string | null;
}

export interface SetFieldOp {
  op: "set_field";
  path: string;
  value: unknown;
}

export interface UnsetFieldOp {
  op: "unset_field";
  path: string;
}

export type EditOp =
  | AddNodeOp
  | RemoveNodeOp
  | AddEdgeOp
  | RemoveEdgeOp
  | SetFieldOp
  | UnsetFieldOp;

/** The file changed underneath the canvas — refetch and retry. */
export class ConflictError extends Error {}

/** The op batch could not be applied or produced an invalid blueprint. */
export class OpRejectedError extends Error {}

export async function applyOps(baseHash: string, ops: EditOp[]): Promise<BlueprintInfo> {
  const response = await fetch("/api/blueprint/ops", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_hash: baseHash, ops }),
  });
  if (response.status === 409) throw new ConflictError("file changed underneath");
  if (response.status === 422) {
    const body = (await response.json()) as { detail?: string };
    throw new OpRejectedError(body.detail ?? "edit rejected");
  }
  if (!response.ok) throw new Error(`API responded ${response.status}`);
  return (await response.json()) as BlueprintInfo;
}

export async function saveLayout(positions: Record<string, NodePosition>): Promise<void> {
  const response = await fetch("/api/layout", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ positions }),
  });
  if (!response.ok) throw new Error(`API responded ${response.status}`);
}
