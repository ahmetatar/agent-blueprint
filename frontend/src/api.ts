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
}

export interface VmEdge {
  id: string;
  source: string;
  target: string;
  kind: string; // normal | conditional | default | entry | delegation | return | parallel
  label: string | null;
}

export interface GraphViewModel {
  entry_point: string;
  nodes: VmNode[];
  edges: VmEdge[];
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
}

export async function fetchBlueprint(): Promise<BlueprintInfo> {
  const response = await fetch("/api/blueprint");
  if (!response.ok) throw new Error(`API responded ${response.status}`);
  return (await response.json()) as BlueprintInfo;
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

export async function saveLayout(positions: Record<string, NodePosition>): Promise<void> {
  const response = await fetch("/api/layout", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ positions }),
  });
  if (!response.ok) throw new Error(`API responded ${response.status}`);
}
