/**
 * Resolve blueprint JSON-Schema properties into renderable field specs.
 *
 * The config forms are driven by `GET /api/schema` (BlueprintSpec's JSON
 * Schema), so new model fields show up in the editor without frontend
 * changes — only the per-node-type *visibility* lists below are curated by
 * hand. Anything the resolver can't render as a simple input degrades to a
 * read-only JSON view ("edit in Source"), never to a broken control.
 */

// JSON Schema fragments are inherently dynamic; keep the loose typing local.
type Schema = Record<string, any>;

export type FieldKind =
  | "string"
  | "text" // multiline string
  | "integer"
  | "number"
  | "boolean"
  | "enum"
  | "string-list"
  | "json"; // read-only fallback

export interface FieldSpec {
  name: string;
  kind: FieldKind;
  required: boolean;
  description?: string;
  options?: string[]; // enum members
  min?: number;
}

/** Long-form string fields render as textareas. */
const TEXTAREA_FIELDS = new Set(["system_prompt", "message_template", "description"]);

/** Which NodeDef fields the panel shows, per node type. */
export const NODE_TYPE_FIELDS: Record<string, string[]> = {
  agent: ["agent", "description"],
  function: ["action", "description"],
  handoff: ["channel", "message_template", "description"],
  supervisor: ["agent", "workers", "max_iterations", "on_finish", "description"],
  parallel: ["branches", "join", "failure_policy", "description"],
  subgraph: ["ref", "description"],
};

/** Node types that support a retry policy sub-section. */
export const RETRYABLE_TYPES = new Set(["agent", "function", "handoff", "supervisor", "subgraph"]);

/** Editable AgentDef fields (complex ones — memory, rag, … — stay in Source). */
export const AGENT_FIELDS = [
  "model",
  "model_provider",
  "system_prompt",
  "temperature",
  "max_tokens",
  "tools",
];

function deref(root: Schema, node: Schema): Schema {
  const ref = node?.$ref as string | undefined;
  if (!ref) return node;
  const name = ref.split("/").pop() ?? "";
  return (root.$defs as Schema | undefined)?.[name] ?? node;
}

function toSpec(root: Schema, name: string, prop: Schema, required: boolean): FieldSpec {
  const description = (prop.description ?? undefined) as string | undefined;
  let p: Schema = prop;
  if (Array.isArray(p.anyOf)) {
    const nonNull = (p.anyOf as Schema[]).filter((member) => member.type !== "null");
    if (nonNull.length !== 1) return { name, kind: "json", required, description };
    p = nonNull[0];
  }
  p = deref(root, p);
  if (Array.isArray(p.enum)) {
    return {
      name,
      kind: "enum",
      required,
      description: description ?? (p.description as string | undefined),
      options: (p.enum as unknown[]).map(String),
    };
  }
  if (p.type === "boolean") return { name, kind: "boolean", required, description };
  if (p.type === "integer" || p.type === "number") {
    const exclusive = p.exclusiveMinimum as number | undefined;
    const min =
      (p.minimum as number | undefined) ??
      (exclusive !== undefined ? (p.type === "integer" ? exclusive + 1 : exclusive) : undefined);
    return { name, kind: p.type, required, description, min };
  }
  if (p.type === "array") {
    const items = deref(root, (p.items as Schema) ?? {});
    if (items.type === "string" || Array.isArray(items.enum)) {
      return { name, kind: "string-list", required, description };
    }
    return { name, kind: "json", required, description };
  }
  if (p.type === "string") {
    return { name, kind: TEXTAREA_FIELDS.has(name) ? "text" : "string", required, description };
  }
  return { name, kind: "json", required, description };
}

/** Field specs for `names` out of `$defs[defName]`, in the given order. */
export function resolveFields(
  root: Record<string, unknown>,
  defName: string,
  names: string[],
): FieldSpec[] {
  const def = (root.$defs as Schema | undefined)?.[defName] as Schema | undefined;
  if (!def) return [];
  const requiredNames = new Set<string>((def.required as string[]) ?? []);
  const specs: FieldSpec[] = [];
  for (const name of names) {
    const prop = (def.properties as Schema | undefined)?.[name] as Schema | undefined;
    if (!prop) continue;
    specs.push(toSpec(root as Schema, name, prop, requiredNames.has(name)));
  }
  return specs;
}
