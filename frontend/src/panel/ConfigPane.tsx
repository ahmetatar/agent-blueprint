import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyOps,
  ConflictError,
  OpRejectedError,
  type BlueprintInfo,
  type EditOp,
  type VmNode,
} from "../api";
import {
  AGENT_FIELDS,
  NODE_TYPE_FIELDS,
  RETRYABLE_TYPES,
  resolveFields,
  type FieldSpec,
} from "./schemaForm";

interface Props {
  schema: Record<string, unknown> | null;
  node: VmNode;
  agents: string[];
  tools: string[];
  hash: string;
  onUpdated: (info: BlueprintInfo) => void;
  onConflict: () => void;
}

/** Form value model: strings for inputs, booleans, string[] for multi-selects. */
type FieldValue = string | boolean | string[];
type Values = Record<string, FieldValue>;

function fromConfig(spec: FieldSpec, raw: unknown): FieldValue {
  if (spec.kind === "boolean") return Boolean(raw);
  if (spec.kind === "string-list") return Array.isArray(raw) ? raw.map(String) : [];
  return raw === null || raw === undefined ? "" : String(raw);
}

function sectionValues(specs: FieldSpec[], config: Record<string, unknown>, prefix: string): Values {
  const values: Values = {};
  for (const spec of specs) values[`${prefix}.${spec.name}`] = fromConfig(spec, config[spec.name]);
  return values;
}

function isEqual(a: FieldValue, b: FieldValue): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Schema-driven config panel for the selected node: NodeDef fields (per node
 * type), the retry policy, and — for agent-backed nodes — the linked agent
 * definition. Changes apply as targeted set_field/unset_field ops; an empty
 * value reverts the field to its default (unset).
 */
export function ConfigPane({ schema, node, agents, tools, hash, onUpdated, onConflict }: Props) {
  const nodeFields = useMemo(
    () => (schema ? resolveFields(schema, "NodeDef", NODE_TYPE_FIELDS[node.type] ?? []) : []),
    [schema, node.type],
  );
  const retryFields = useMemo(
    () =>
      schema && RETRYABLE_TYPES.has(node.type)
        ? resolveFields(schema, "RetryPolicyDef", ["max_attempts", "backoff_seconds"])
        : [],
    [schema, node.type],
  );
  const agentFields = useMemo(
    () => (schema && node.agent_config ? resolveFields(schema, "AgentDef", AGENT_FIELDS) : []),
    [schema, node.agent_config],
  );

  const buildValues = useCallback((): Values => {
    const config = node.config ?? {};
    const retry = (config.retry as Record<string, unknown>) ?? {};
    return {
      ...sectionValues(nodeFields, config, "node"),
      ...sectionValues(retryFields, retry, "retry"),
      ...sectionValues(agentFields, node.agent_config ?? {}, "agent"),
    };
  }, [node, nodeFields, retryFields, agentFields]);

  const [values, setValues] = useState<Values>(buildValues);
  const initialRef = useRef<Values>(values);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const dirty = Object.keys(values).some((key) => !isEqual(values[key], initialRef.current[key]));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // Track the underlying node: resync on selection change or external edits,
  // but never clobber in-progress form edits.
  useEffect(() => {
    if (dirtyRef.current) return;
    const fresh = buildValues();
    initialRef.current = fresh;
    setValues(fresh);
    setError(null);
  }, [buildValues]);

  const setField = (key: string, value: FieldValue) =>
    setValues((current) => ({ ...current, [key]: value }));

  const fieldPath = (prefix: string, name: string): string => {
    const base = `${node.graph_ref ?? "graph"}.nodes.${node.label}`;
    if (prefix === "node") return `${base}.${name}`;
    if (prefix === "retry") return `${base}.retry.${name}`;
    return `agents.${node.agent}.${name}`;
  };

  const buildOps = (): EditOp[] => {
    const ops: EditOp[] = [];
    const sections: Array<[string, FieldSpec[]]> = [
      ["node", nodeFields],
      ["retry", retryFields],
      ["agent", agentFields],
    ];
    for (const [prefix, specs] of sections) {
      for (const spec of specs) {
        const key = `${prefix}.${spec.name}`;
        const value = values[key];
        if (isEqual(value, initialRef.current[key])) continue;
        const path = fieldPath(prefix, spec.name);
        if (spec.kind === "boolean") {
          ops.push({ op: "set_field", path, value });
          continue;
        }
        if (spec.kind === "string-list") {
          const list = value as string[];
          ops.push(list.length === 0 ? { op: "unset_field", path } : { op: "set_field", path, value: list });
          continue;
        }
        const text = (value as string).trim();
        if (text === "") {
          ops.push({ op: "unset_field", path });
          continue;
        }
        if (spec.kind === "integer" || spec.kind === "number") {
          const parsed = spec.kind === "integer" ? Number.parseInt(text, 10) : Number.parseFloat(text);
          if (Number.isNaN(parsed)) throw new Error(`'${spec.name}' must be a number`);
          ops.push({ op: "set_field", path, value: parsed });
          continue;
        }
        ops.push({ op: "set_field", path, value: text });
      }
    }
    return ops;
  };

  const apply = async () => {
    let ops: EditOp[];
    try {
      ops = buildOps();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    if (ops.length === 0) return;
    setBusy(true);
    try {
      const info = await applyOps(hash, ops);
      initialRef.current = { ...values };
      setError(null);
      onUpdated(info);
    } catch (e) {
      if (e instanceof ConflictError) {
        onConflict();
        setError("File changed underneath — values refreshed, re-apply your change");
        initialRef.current = values; // keep edits, let the resync effect settle
      } else if (e instanceof OpRejectedError) {
        setError(e.message);
      } else {
        setError(String(e));
      }
    } finally {
      setBusy(false);
    }
  };

  const renderField = (prefix: string, spec: FieldSpec) => {
    const key = `${prefix}.${spec.name}`;
    const value = values[key];
    const title = spec.description;
    if (spec.kind === "boolean") {
      return (
        <label key={key} className="config-field config-field-inline" title={title}>
          <input
            type="checkbox"
            checked={value as boolean}
            onChange={(event) => setField(key, event.target.checked)}
          />
          {spec.name}
        </label>
      );
    }
    if (spec.kind === "string-list" && prefix === "agent" && spec.name === "tools") {
      const selected = new Set(value as string[]);
      return (
        <fieldset key={key} className="config-field config-tools" title={title}>
          <legend>tools</legend>
          {tools.length === 0 && <span className="muted">no tools defined</span>}
          {tools.map((tool) => (
            <label key={tool} className="config-field-inline">
              <input
                type="checkbox"
                checked={selected.has(tool)}
                onChange={(event) => {
                  const next = new Set(selected);
                  if (event.target.checked) next.add(tool);
                  else next.delete(tool);
                  // Keep the blueprint's tool order stable.
                  setField(key, tools.filter((name) => next.has(name)));
                }}
              />
              {tool}
            </label>
          ))}
        </fieldset>
      );
    }
    let control: React.ReactNode;
    if (spec.kind === "enum" || (prefix === "node" && spec.name === "agent")) {
      const options = spec.kind === "enum" ? spec.options ?? [] : agents;
      control = (
        <select value={value as string} onChange={(event) => setField(key, event.target.value)}>
          {!spec.required && <option value="">(default)</option>}
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    } else if (spec.kind === "text") {
      control = (
        <textarea
          rows={4}
          value={value as string}
          onChange={(event) => setField(key, event.target.value)}
        />
      );
    } else if (spec.kind === "string-list") {
      control = (
        <input
          value={(value as string[]).join(", ")}
          placeholder="comma-separated"
          onChange={(event) =>
            setField(
              key,
              event.target.value
                .split(",")
                .map((item) => item.trim())
                .filter(Boolean),
            )
          }
        />
      );
    } else if (spec.kind === "json") {
      control = <span className="muted">edit in Source</span>;
    } else {
      control = (
        <input
          type={spec.kind === "integer" || spec.kind === "number" ? "number" : "text"}
          min={spec.min}
          step={spec.kind === "number" ? "any" : undefined}
          value={value as string}
          onChange={(event) => setField(key, event.target.value)}
        />
      );
    }
    return (
      <label key={key} className="config-field" title={title}>
        <span className="config-field-name">
          {spec.name}
          {spec.required && <span className="config-required">*</span>}
        </span>
        {control}
      </label>
    );
  };

  if (schema === null) {
    return <p className="config-empty muted">Schema unavailable — config forms are disabled.</p>;
  }
  return (
    <div className="config-pane">
      <div className="config-head">
        <strong>{node.label}</strong>
        <span className="config-kind">{node.type}</span>
      </div>
      <div className="config-sections">
        <section>
          <h3>Node</h3>
          {nodeFields.map((spec) => renderField("node", spec))}
        </section>
        {retryFields.length > 0 && (
          <section>
            <h3>Retry</h3>
            {retryFields.map((spec) => renderField("retry", spec))}
          </section>
        )}
        {agentFields.length > 0 && (
          <section>
            <h3>Agent · {node.agent}</h3>
            {agentFields.map((spec) => renderField("agent", spec))}
          </section>
        )}
      </div>
      {error !== null && <p className="config-error">{error}</p>}
      <footer className="config-footer">
        <span className="muted">{dirty ? "Unapplied changes" : "In sync"}</span>
        <button
          type="button"
          className="save-button"
          disabled={!dirty || busy}
          onClick={() => void apply()}
        >
          {busy ? "Applying…" : "Apply"}
        </button>
      </footer>
    </div>
  );
}
