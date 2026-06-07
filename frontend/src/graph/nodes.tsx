import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { LintFinding, VmNode } from "../api";

interface NodeData {
  vm: VmNode;
  findings: LintFinding[];
  [key: string]: unknown;
}

const ACCENT: Record<string, string> = {
  agent: "#0b6bcb",
  supervisor: "#7c5cff",
  parallel: "#c77700",
  handoff: "#b3261e",
  function: "#3d7a4f",
  subgraph: "#6b7280",
};

function LintBadge({ findings }: { findings: LintFinding[] }) {
  if (findings.length === 0) return null;
  const hasError = findings.some((f) => f.severity === "error");
  return (
    <span
      className={`node-badge ${hasError ? "node-badge-error" : "node-badge-warning"}`}
      title={findings.map((f) => `${f.code}: ${f.message}`).join("\n")}
    >
      {findings.length}
    </span>
  );
}

/** Agent / supervisor / parallel / handoff / function / collapsed-subgraph card. */
export function BlueprintNode({ data }: NodeProps) {
  const { vm, findings } = data as NodeData;
  const accent = ACCENT[vm.type] ?? "#6b7280";
  return (
    <div className="bp-node" style={{ borderTopColor: accent }}>
      <Handle type="target" position={Position.Top} />
      <div className="bp-node-head">
        <span className="bp-node-type" style={{ color: accent }}>
          {vm.type}
        </span>
        {vm.entry && <span className="bp-node-entry">entry</span>}
        {vm.retry !== undefined && <span className="bp-node-chip">retry ×{vm.retry}</span>}
        <LintBadge findings={findings} />
      </div>
      <div className="bp-node-label">{vm.label}</div>
      {vm.model && (
        <div className="bp-node-meta">
          {vm.provider}/{vm.model}
          {vm.tools && vm.tools.length > 0 && ` · ${vm.tools.length} tool${vm.tools.length > 1 ? "s" : ""}`}
        </div>
      )}
      {vm.type === "supervisor" && vm.max_iterations !== undefined && (
        <div className="bp-node-meta">max {vm.max_iterations} iterations</div>
      )}
      {vm.type === "handoff" && vm.channel && <div className="bp-node-meta">via {vm.channel}</div>}
      {vm.type === "function" && vm.action && <div className="bp-node-meta">{vm.action}</div>}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

/** START / END pill. */
export function TerminalNode({ data }: NodeProps) {
  const { vm } = data as NodeData;
  return (
    <div className={`terminal-node terminal-${vm.type}`}>
      {vm.type === "end" && <Handle type="target" position={Position.Top} />}
      {vm.label}
      {vm.type === "start" && <Handle type="source" position={Position.Bottom} />}
    </div>
  );
}

/** Expanded subgraph container. */
export function GroupNode({ data }: NodeProps) {
  const { vm, findings } = data as NodeData;
  return (
    <div className="group-node">
      <Handle type="target" position={Position.Top} />
      <div className="group-node-title">
        <span className="bp-node-type" style={{ color: ACCENT.subgraph }}>
          subgraph
        </span>
        <span className="group-node-label">{vm.label}</span>
        {vm.ref && <span className="bp-node-chip">{vm.ref}</span>}
        <LintBadge findings={findings} />
      </div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

export const nodeTypes = {
  blueprint: BlueprintNode,
  terminal: TerminalNode,
  group: GroupNode,
};
