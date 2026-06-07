import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchBlueprint,
  fetchCurrentTask,
  fetchSchema,
  type BlueprintInfo,
  type LintFinding,
  type TaskMessage,
  type TaskRecord,
} from "./api";
import { GraphCanvas } from "./graph/GraphCanvas";
import { ActionsPane } from "./panel/ActionsPane";
import { ConfigPane } from "./panel/ConfigPane";
import { IssuesPane } from "./panel/IssuesPane";
import { SourcePane, type SourcePaneHandle } from "./panel/SourcePane";
import { useLiveReload } from "./useLiveReload";
import "./App.css";

type Tab = "issues" | "source" | "config" | "actions";

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
  const sourceRef = useRef<SourcePaneHandle | null>(null);
  const pendingLine = useRef<number | null>(null);

  const reload = useCallback(() => {
    fetchBlueprint()
      .then((next) => {
        setInfo(next);
        setFetchError(null);
      })
      .catch((e) => setFetchError(String(e)));
  }, []);

  const applyTask = useCallback((next: TaskRecord) => {
    setTask((prev) => mergeTask(prev, next));
  }, []);

  const onTaskMessage = useCallback(
    (message: TaskMessage) => {
      if (message.type === "task_progress") {
        // Append the event; the full record arrives again with task_done.
        setTask((prev) =>
          prev !== null && prev.id === message.task_id && message.event
            ? { ...prev, progress: [...prev.progress, message.event] }
            : prev,
        );
      } else if (message.task) {
        applyTask(message.task);
      }
    },
    [applyTask],
  );

  useEffect(reload, [reload]);
  useLiveReload(reload, onTaskMessage);

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
      </header>
      <div className="body">
        <section className="canvas">
          {info.graph ? (
            <GraphCanvas
              graph={info.graph}
              lint={info.lint}
              layout={info.layout}
              hash={info.hash}
              onUpdated={setInfo}
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
                onUpdated={setInfo}
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
          <div className="panel-body panel-body-source" hidden={tab !== "source"}>
            <SourcePane
              yaml={info.yaml}
              lint={info.lint}
              onSaved={setInfo}
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
