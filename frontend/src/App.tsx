import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchBlueprint,
  fetchChat,
  fetchCurrentTask,
  fetchSchema,
  saveYaml,
  type BlueprintInfo,
  type ChatState,
  type ChatWsMessage,
  type LintFinding,
  type TaskMessage,
  type TaskRecord,
  type TraceEvent,
} from "./api";
import { GraphCanvas } from "./graph/GraphCanvas";
import { ActionsPane } from "./panel/ActionsPane";
import { ChatPane } from "./panel/ChatPane";
import { ConfigPane } from "./panel/ConfigPane";
import { IssuesPane } from "./panel/IssuesPane";
import { SourcePane, type SourcePaneHandle } from "./panel/SourcePane";
import { useLiveReload } from "./useLiveReload";
import "./App.css";

type Tab = "issues" | "source" | "config" | "actions" | "chat";

const IDLE_CHAT: ChatState = { status: "idle", thread_id: null, error: null, history: [] };

/** Live execution state of a canvas node, derived from streamed trace events. */
export type RunState = "running" | "ok" | "error";

/**
 * Merge an incoming task snapshot over the current one. The POST response
 * (status "running") can resolve after the WS already delivered task_done —
 * never let a stale "running" overwrite a terminal status, and keep the
 * longer progress list while running.
 */
function mergeTask(prev: TaskRecord | null, next: TaskRecord): TaskRecord {
  if (prev !== null && prev.id === next.id && next.status === "running") {
    if (prev.status !== "running") return prev;
    if (prev.progress.length > next.progress.length) {
      return { ...next, progress: prev.progress };
    }
  }
  return next;
}

export default function App() {
  const [info, setInfo] = useState<BlueprintInfo | null>(null);
  const [schema, setSchema] = useState<Record<string, unknown> | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("issues");
  const [sourceDirty, setSourceDirty] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [chat, setChat] = useState<ChatState>(IDLE_CHAT);
  // Canvas-id keyed run states; cleared when a task (or a new scenario
  // within it) starts, kept after task_done so the last run stays visible.
  const [runStates, setRunStates] = useState<Record<string, RunState>>({});
  // Undo/redo as a stack of whole-file YAML snapshots: every editor mutation
  // (canvas op, config form, source save) pushes the prior YAML; undo writes
  // a popped snapshot back via the whole-file save. Snapshots restore comments
  // and quoting byte-for-byte, and the model is uniform across all edit kinds.
  // Disk/live-reload updates use plain setInfo and deliberately leave the
  // stacks untouched (undo history is this session's local edits).
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [redoStack, setRedoStack] = useState<string[]>([]);
  const restoring = useRef(false);
  const sourceRef = useRef<SourcePaneHandle | null>(null);
  const pendingLine = useRef<number | null>(null);

  // Trace events carry runtime (flattened-graph) node ids — map to canvas ids.
  const runtimeToCanvas = useMemo(() => {
    const map = new Map<string, string>();
    for (const node of info?.graph?.nodes ?? []) {
      if (node.runtime_id) map.set(node.runtime_id, node.id);
    }
    return map;
  }, [info]);
  const runtimeToCanvasRef = useRef(runtimeToCanvas);
  runtimeToCanvasRef.current = runtimeToCanvas;

  const onTraceEvent = useCallback((event: TraceEvent) => {
    if (event.event === "run_started") {
      setRunStates({}); // next scenario in the same task starts clean
      return;
    }
    const node = event.node ? runtimeToCanvasRef.current.get(event.node) : undefined;
    if (node === undefined) return;
    setRunStates((prev) => {
      if (prev[node] === "error") return prev; // errors stick
      if (event.error !== undefined) return { ...prev, [node]: "error" };
      if (event.event === "node_started") return { ...prev, [node]: "running" };
      if (event.event === "node_finished") return { ...prev, [node]: "ok" };
      return prev;
    });
  }, []);

  const infoRef = useRef<BlueprintInfo | null>(null);
  infoRef.current = info;

  const reload = useCallback(() => {
    fetchBlueprint()
      .then((next) => {
        setInfo(next);
        setFetchError(null);
      })
      .catch((e) => setFetchError(String(e)));
  }, []);

  // An editor mutation: remember the prior YAML for undo and drop the redo
  // future. Wired into every write-back callback (canvas / config / source).
  const handleEdit = useCallback((next: BlueprintInfo) => {
    const prev = infoRef.current;
    if (prev !== null && prev.yaml !== next.yaml) {
      setUndoStack((stack) => [...stack, prev.yaml]);
      setRedoStack([]);
    }
    setInfo(next);
  }, []);

  const restore = useCallback((yaml: string, direction: "undo" | "redo") => {
    const current = infoRef.current;
    if (current === null || restoring.current) return;
    restoring.current = true;
    saveYaml(yaml)
      .then((next) => {
        if (direction === "undo") {
          setRedoStack((stack) => [...stack, current.yaml]);
          setUndoStack((stack) => stack.slice(0, -1));
        } else {
          setUndoStack((stack) => [...stack, current.yaml]);
          setRedoStack((stack) => stack.slice(0, -1));
        }
        setInfo(next);
        setFetchError(null);
      })
      .catch((e) => setFetchError(String(e)))
      .finally(() => {
        restoring.current = false;
      });
  }, []);

  const undo = useCallback(() => {
    const yaml = undoStack[undoStack.length - 1];
    if (yaml !== undefined) restore(yaml, "undo");
  }, [undoStack, restore]);

  const redo = useCallback(() => {
    const yaml = redoStack[redoStack.length - 1];
    if (yaml !== undefined) restore(yaml, "redo");
  }, [redoStack, restore]);

  const applyTask = useCallback((next: TaskRecord) => {
    setTask((prev) => mergeTask(prev, next));
  }, []);

  const onTaskMessage = useCallback(
    (message: TaskMessage) => {
      if (message.type === "task_trace") {
        onTraceEvent(message.event);
      } else if (message.type === "task_progress") {
        // Append the event; the full record arrives again with task_done.
        setTask((prev) =>
          prev !== null && prev.id === message.task_id && message.event
            ? { ...prev, progress: [...prev.progress, message.event] }
            : prev,
        );
      } else {
        if (message.type === "task_started") setRunStates({});
        applyTask(message.task);
      }
    },
    [applyTask, onTraceEvent],
  );

  const onChatMessage = useCallback((message: ChatWsMessage) => {
    if (message.type === "chat_status") {
      setChat((prev) => ({
        ...prev,
        status: message.status,
        thread_id: message.thread_id,
        error: message.error,
        // A (re)start clears history; the server snapshot is the source of truth.
        history: message.status === "starting" ? [] : prev.history,
      }));
    } else if (message.type === "chat_message") {
      setChat((prev) => ({ ...prev, history: [...prev.history, message.message] }));
    }
  }, []);

  useEffect(reload, [reload]);
  useLiveReload(reload, onTaskMessage, onChatMessage);

  useEffect(() => {
    // Resync an already-running chat session when a tab (re)loads.
    fetchChat()
      .then(setChat)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return;
      // The Source pane (Monaco) and any form input own their native undo;
      // only the canvas/global scope uses the snapshot history.
      if (sourceDirty) return;
      const target = event.target as HTMLElement | null;
      if (
        target !== null &&
        (target.closest(".monaco-editor") !== null ||
          target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "z" && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if (key === "y" || (key === "z" && event.shiftKey)) {
        event.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [undo, redo, sourceDirty]);

  useEffect(() => {
    // A reloaded tab resyncs with a task that is already running (or just ran).
    fetchCurrentTask()
      .then((current) => {
        if (current !== null) applyTask(current);
      })
      .catch(() => undefined);
  }, [applyTask]);

  useEffect(() => {
    // The schema only changes with the installed abp version — fetch once.
    fetchSchema()
      .then(setSchema)
      .catch(() => setSchema(null));
  }, []);

  const onSelect = useCallback((nodeId: string | null) => {
    // Canvas re-renders (after an applied op or live reload) reset React
    // Flow's selection — keep the panel on the last selected node instead of
    // yanking the form away mid-edit.
    if (nodeId === null) return;
    setSelectedId(nodeId);
    setTab("config");
  }, []);

  const showFindingInSource = (finding: LintFinding) => {
    if (finding.line === null) return;
    if (sourceRef.current) {
      setTab("source");
      sourceRef.current.revealLine(finding.line);
    } else {
      pendingLine.current = finding.line; // Monaco mounts after the tab switch
      setTab("source");
    }
  };

  if (fetchError) {
    return (
      <main className="state-page">
        <h1>ABP Editor</h1>
        <p className="error-text">Could not reach the editor API: {fetchError}</p>
      </main>
    );
  }
  if (!info) {
    return (
      <main className="state-page">
        <p className="muted">Loading blueprint…</p>
      </main>
    );
  }

  const errorCount =
    info.lint.filter((f) => f.severity === "error").length + (info.valid ? 0 : 1);
  const warningCount = info.lint.filter((f) => f.severity === "warning").length;
  // Only nodes that exist in the YAML carry a config (terminals don't).
  const selectedNode =
    selectedId !== null
      ? info.graph?.nodes.find((node) => node.id === selectedId && node.config) ?? null
      : null;

  return (
    <div className="app">
      <header className="header">
        <span className="header-title">{info.name ?? "(unnamed blueprint)"}</span>
        <span className={`badge ${info.valid ? "badge-valid" : "badge-invalid"}`}>
          {info.valid ? "valid" : "invalid"}
        </span>
        <span className="header-path" title={info.path}>
          {info.path}
        </span>
        <span className="header-live" title="Live-reloads when the file changes on disk">
          ● live
        </span>
        <div className="header-history">
          <button
            type="button"
            className="history-btn"
            onClick={undo}
            disabled={undoStack.length === 0 || sourceDirty}
            title="Undo (⌘/Ctrl+Z)"
          >
            ↶ Undo
          </button>
          <button
            type="button"
            className="history-btn"
            onClick={redo}
            disabled={redoStack.length === 0 || sourceDirty}
            title="Redo (⌘/Ctrl+Shift+Z)"
          >
            ↷ Redo
          </button>
        </div>
      </header>
      <div className="body">
        <section className="canvas">
          {info.graph ? (
            <GraphCanvas
              graph={info.graph}
              lint={info.lint}
              layout={info.layout}
              hash={info.hash}
              runStates={runStates}
              onUpdated={handleEdit}
              onConflict={reload}
              onSelect={onSelect}
            />
          ) : (
            <div className="canvas-empty">
              <p>The blueprint does not validate, so there is no graph to draw.</p>
              <p className="muted">Fix the validation error (see Issues) and save — the canvas updates live.</p>
            </div>
          )}
        </section>
        <aside className="panel">
          <nav className="tabs">
            <button
              type="button"
              className={tab === "issues" ? "tab tab-active" : "tab"}
              onClick={() => setTab("issues")}
            >
              Issues
              {errorCount > 0 && <span className="count count-error">{errorCount}</span>}
              {warningCount > 0 && <span className="count count-warning">{warningCount}</span>}
            </button>
            <button
              type="button"
              className={tab === "config" ? "tab tab-active" : "tab"}
              onClick={() => setTab("config")}
            >
              Config
            </button>
            <button
              type="button"
              className={tab === "source" ? "tab tab-active" : "tab"}
              onClick={() => setTab("source")}
            >
              Source
              {sourceDirty && (
                <span className="dirty-dot" title="Unsaved changes">
                  ●
                </span>
              )}
            </button>
            <button
              type="button"
              className={tab === "actions" ? "tab tab-active" : "tab"}
              onClick={() => setTab("actions")}
            >
              Actions
              {task?.status === "running" && (
                <span className="running-dot" title="A task is running">
                  ●
                </span>
              )}
            </button>
            <button
              type="button"
              className={tab === "chat" ? "tab tab-active" : "tab"}
              onClick={() => setTab("chat")}
            >
              Chat
              {chat.status === "ready" && (
                <span className="running-dot" title="A chat session is live">
                  ●
                </span>
              )}
            </button>
          </nav>
          <div className="panel-body" hidden={tab !== "issues"}>
            <IssuesPane error={info.error} lint={info.lint} onFindingClick={showFindingInSource} />
          </div>
          <div className="panel-body panel-body-config" hidden={tab !== "config"}>
            {selectedNode && info.graph ? (
              <ConfigPane
                key={selectedNode.id}
                schema={schema}
                node={selectedNode}
                agents={info.graph.agents}
                tools={info.graph.tools}
                hash={info.hash}
                onUpdated={handleEdit}
                onConflict={reload}
              />
            ) : (
              <p className="config-empty muted">Select a node on the canvas to edit its config.</p>
            )}
          </div>
          <div className="panel-body" hidden={tab !== "actions"}>
            <ActionsPane
              surface={info.actions}
              valid={info.valid}
              task={task}
              onTask={applyTask}
            />
          </div>
          <div className="panel-body panel-body-chat" hidden={tab !== "chat"}>
            <ChatPane chat={chat} valid={info.valid} />
          </div>
          <div className="panel-body panel-body-source" hidden={tab !== "source"}>
            <SourcePane
              yaml={info.yaml}
              lint={info.lint}
              onSaved={handleEdit}
              onDirtyChange={setSourceDirty}
              onReady={(handle) => {
                sourceRef.current = handle;
                if (pendingLine.current !== null) {
                  handle.revealLine(pendingLine.current);
                  pendingLine.current = null;
                }
              }}
            />
          </div>
        </aside>
      </div>
    </div>
  );
}
