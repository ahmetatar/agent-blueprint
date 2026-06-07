import { useState } from "react";
import type { AddNodeOp } from "../api";

interface Props {
  agents: string[];
  existingIds: Set<string>;
  onSubmit: (op: AddNodeOp) => void;
  onCancel: () => void;
}

/**
 * Minimal add-node dialog (top-level graph only): agent nodes pick one of the
 * blueprint's defined agents; function nodes name an action. Richer,
 * schema-driven config forms are phase E2c.
 */
export function AddNodeDialog({ agents, existingIds, onSubmit, onCancel }: Props) {
  const [nodeId, setNodeId] = useState("");
  const [kind, setKind] = useState<"agent" | "function">("agent");
  const [agent, setAgent] = useState(agents[0] ?? "");
  const [action, setAction] = useState("");
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    const id = nodeId.trim();
    if (!id) return setError("Node id is required");
    if (existingIds.has(id)) return setError(`Node '${id}' already exists`);
    if (kind === "agent" && !agent) return setError("Pick an agent (none are defined yet)");
    if (kind === "function" && !action.trim()) return setError("Action is required");
    onSubmit({
      op: "add_node",
      node_id: id,
      node:
        kind === "agent" ? { agent } : { type: "function", action: action.trim() },
    });
  };

  return (
    <div className="dialog-backdrop" onClick={onCancel}>
      <div
        className="dialog"
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Enter") submit();
          if (event.key === "Escape") onCancel();
        }}
      >
        <h2>Add node</h2>
        <label>
          Node id
          <input
            autoFocus
            value={nodeId}
            onChange={(event) => setNodeId(event.target.value)}
            placeholder="e.g. reviewer"
          />
        </label>
        <label>
          Type
          <select value={kind} onChange={(event) => setKind(event.target.value as "agent" | "function")}>
            <option value="agent">agent</option>
            <option value="function">function</option>
          </select>
        </label>
        {kind === "agent" ? (
          <label>
            Agent
            <select value={agent} onChange={(event) => setAgent(event.target.value)}>
              {agents.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            Action
            <input
              value={action}
              onChange={(event) => setAction(event.target.value)}
              placeholder="python function name"
            />
          </label>
        )}
        {error !== null && <p className="dialog-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="dialog-primary" onClick={submit}>
            Add
          </button>
        </div>
      </div>
    </div>
  );
}
