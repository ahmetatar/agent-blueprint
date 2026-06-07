import { useCallback, useEffect, useRef, useState } from "react";
import { fetchBlueprint, type BlueprintInfo, type LintFinding } from "./api";
import { GraphCanvas } from "./graph/GraphCanvas";
import { IssuesPane } from "./panel/IssuesPane";
import { SourcePane, type SourcePaneHandle } from "./panel/SourcePane";
import { useLiveReload } from "./useLiveReload";
import "./App.css";

type Tab = "issues" | "source";

export default function App() {
  const [info, setInfo] = useState<BlueprintInfo | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("issues");
  const [sourceDirty, setSourceDirty] = useState(false);
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

  useEffect(reload, [reload]);
  useLiveReload(reload);

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
          </nav>
          <div className="panel-body" hidden={tab !== "issues"}>
            <IssuesPane error={info.error} lint={info.lint} onFindingClick={showFindingInSource} />
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
