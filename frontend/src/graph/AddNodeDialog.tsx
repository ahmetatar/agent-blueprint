import { useState } from "react";
import type { AddNodeOp } from "../api";

interface Props {
  agents: string[];
  subgraphs: string[];
  /** Existing top-level node ids — referenced by branches / join / workers. */
  nodeIds: string[];
  existingIds: Set<string>;
  onSubmit: (op: AddNodeOp) => void;
  onCancel: () => void;
}

type Kind = "agent" | "function" | "handoff" | "parallel" | "subgraph" | "supervisor";

const KINDS: Kind[] = ["agent", "function", "handoff", "parallel", "subgraph", "supervisor"];
const CHANNELS = ["console", "webhook", "slack", "email"];

interface MapRow {
  key: string;
  value: string;
}

/**
 * Add-node dialog (top-level graph) covering every node type. References
 * (agents, subgraphs, sibling node ids for branches/join/workers) are pickers;
 * the server still strict-validates the mutated blueprint, so an incomplete
 * combination is rejected with a clear message rather than written. Wiring
 * supervisor workers / parallel branches by *drawing* on the canvas is a
 * later phase — here they are checkbox pickers.
 */
export function AddNodeDialog({
  agents,
  subgraphs,
  nodeIds,
  existingIds,
  onSubmit,
  onCancel,
}: Props) {
  const [nodeId, setNodeId] = useState("");
  const [kind, setKind] = useState<Kind>("agent");
  const [agent, setAgent] = useState(agents[0] ?? "");
  const [action, setAction] = useState("");
  const [channel, setChannel] = useState(CHANNELS[0]);
  const [messageTemplate, setMessageTemplate] = useState("");
  const [branches, setBranches] = useState<string[]>([]);
  const [join, setJoin] = useState("");
  const [workers, setWorkers] = useState<string[]>([]);
  const [ref, setRef] = useState(subgraphs[0] ?? "");
  const [inputMap, setInputMap] = useState<MapRow[]>([{ key: "", value: "" }]);
  const [outputMap, setOutputMap] = useState<MapRow[]>([{ key: "", value: "" }]);
  const [error, setError] = useState<string | null>(null);

  const toggle = (
    set: React.Dispatch<React.SetStateAction<string[]>>,
    value: string,
  ) => set((current) => (current.includes(value) ? current.filter((v) => v !== value) : [...current, value]));

  const mapObject = (rows: MapRow[]): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const row of rows) {
      const key = row.key.trim();
      if (key) out[key] = row.value.trim();
    }
    return out;
  };

  const buildNode = (): Record<string, unknown> | string => {
    switch (kind) {
      case "agent":
        if (!agent) return "Pick an agent (none are defined yet)";
        return { agent };
      case "function":
        if (!action.trim()) return "Action is required";
        return { type: "function", action: action.trim() };
      case "handoff": {
        const node: Record<string, unknown> = { type: "handoff", channel };
        if (messageTemplate.trim()) node.message_template = messageTemplate.trim();
        if (action.trim()) node.action = action.trim();
        return node;
      }
      case "parallel":
        if (branches.length === 0) return "Pick at least one branch";
        if (!join) return "Pick a join node";
        if (branches.includes(join)) return "The join node cannot also be a branch";
        return { type: "parallel", branches, join };
      case "subgraph": {
        if (!ref) return "Pick a subgraph (define one in Source first)";
        const input = mapObject(inputMap);
        const output = mapObject(outputMap);
        if (Object.keys(input).length === 0) return "input_map needs at least one entry";
        if (Object.keys(output).length === 0) return "output_map needs at least one entry";
        return { type: "subgraph", ref, input_map: input, output_map: output };
      }
      case "supervisor":
        if (!agent) return "Pick the supervisor's agent";
        if (workers.length === 0) return "Pick at least one worker";
        return { type: "supervisor", agent, workers };
    }
  };

  const submit = () => {
    const id = nodeId.trim();
    if (!id) return setError("Node id is required");
    if (existingIds.has(id)) return setError(`Node '${id}' already exists`);
    const node = buildNode();
    if (typeof node === "string") return setError(node);
    onSubmit({ op: "add_node", node_id: id, node });
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
          <select value={kind} onChange={(event) => setKind(event.target.value as Kind)}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>

        {(kind === "agent" || kind === "supervisor") && (
          <label>
            {kind === "supervisor" ? "Supervisor agent" : "Agent"}
            <select value={agent} onChange={(event) => setAgent(event.target.value)}>
              <option value="">— pick an agent —</option>
              {agents.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}

        {kind === "function" && (
          <label>
            Action
            <input
              value={action}
              onChange={(event) => setAction(event.target.value)}
              placeholder="python function name"
            />
          </label>
        )}

        {kind === "handoff" && (
          <>
            <label>
              Channel
              <select value={channel} onChange={(event) => setChannel(event.target.value)}>
                {CHANNELS.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Message template <span className="dialog-optional">(optional)</span>
              <input
                value={messageTemplate}
                onChange={(event) => setMessageTemplate(event.target.value)}
                placeholder="Escalating: {summary}"
              />
            </label>
          </>
        )}

        {(kind === "parallel" || kind === "supervisor") && (
          <fieldset className="dialog-checks">
            <legend>{kind === "parallel" ? "Branches" : "Workers"}</legend>
            {nodeIds.length === 0 ? (
              <p className="dialog-optional">No other nodes to reference yet.</p>
            ) : (
              nodeIds.map((id) => {
                const list = kind === "parallel" ? branches : workers;
                const set = kind === "parallel" ? setBranches : setWorkers;
                return (
                  <label key={id} className="dialog-check">
                    <input
                      type="checkbox"
                      checked={list.includes(id)}
                      onChange={() => toggle(set, id)}
                    />
                    {id}
                  </label>
                );
              })
            )}
          </fieldset>
        )}

        {kind === "parallel" && (
          <label>
            Join node
            <select value={join} onChange={(event) => setJoin(event.target.value)}>
              <option value="">— pick a join node —</option>
              {nodeIds.map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
          </label>
        )}

        {kind === "subgraph" && (
          <>
            <label>
              Subgraph ref
              <select value={ref} onChange={(event) => setRef(event.target.value)}>
                <option value="">— pick a subgraph —</option>
                {subgraphs.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <MapEditor label="input_map" rows={inputMap} onChange={setInputMap} />
            <MapEditor label="output_map" rows={outputMap} onChange={setOutputMap} />
          </>
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

/** Minimal key→value row editor for a subgraph node's input/output maps. */
function MapEditor({
  label,
  rows,
  onChange,
}: {
  label: string;
  rows: MapRow[];
  onChange: (rows: MapRow[]) => void;
}) {
  const update = (index: number, patch: Partial<MapRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  return (
    <fieldset className="dialog-map">
      <legend>{label}</legend>
      {rows.map((row, index) => (
        <div key={index} className="dialog-maprow">
          <input
            value={row.key}
            onChange={(event) => update(index, { key: event.target.value })}
            placeholder="key"
          />
          <span>→</span>
          <input
            value={row.value}
            onChange={(event) => update(index, { value: event.target.value })}
            placeholder="value"
          />
          <button
            type="button"
            className="dialog-maprow-remove"
            title="Remove row"
            onClick={() => onChange(rows.length > 1 ? rows.filter((_, i) => i !== index) : [{ key: "", value: "" }])}
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        className="dialog-maprow-add"
        onClick={() => onChange([...rows, { key: "", value: "" }])}
      >
        + row
      </button>
    </fieldset>
  );
}
