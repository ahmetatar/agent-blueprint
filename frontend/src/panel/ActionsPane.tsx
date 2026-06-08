import { useState } from "react";
import {
  cancelTask,
  startAction,
  TaskBusyError,
  type ActionSurface,
  type TaskAction,
  type TaskProgressEvent,
  type TaskRecord,
} from "../api";

interface Props {
  surface: ActionSurface | null;
  valid: boolean;
  task: TaskRecord | null;
  onTask: (task: TaskRecord) => void;
}

const ACTION_LABELS: Record<TaskAction, string> = {
  test: "Test",
  run: "Run",
  gate: "Gate",
  generate: "Generate",
  doctor: "Doctor",
  deploy: "Deploy",
};

const LOCAL_ENGINES = ["docker", "podman"];

/** Action buttons + live progress + result view for background tasks (E3a). */
export function ActionsPane({ surface, valid, task, onTask }: Props) {
  // Which action's parameter form is open (test → scenario picker, run → input).
  const [armed, setArmed] = useState<"test" | "run" | "deploy" | null>(null);
  const [pickedScenarios, setPickedScenarios] = useState<string[] | null>(null);
  const [runInput, setRunInput] = useState("");
  const [engine, setEngine] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  const running = task !== null && task.status === "running";

  const start = (action: TaskAction, params: Record<string, unknown> = {}) => {
    setStartError(null);
    setArmed(null);
    startAction(action, params)
      .then(onTask)
      .catch((e) =>
        setStartError(e instanceof TaskBusyError ? e.message : String(e)),
      );
  };

  if (!valid) {
    return (
      <p className="actions-empty muted">
        The blueprint does not validate — fix the errors in Issues first, then run actions here.
      </p>
    );
  }

  const scenarios = surface?.scenarios ?? [];
  const canGate = scenarios.length > 0 || (surface?.eval_suites.length ?? 0) > 0;
  const picked = pickedScenarios ?? scenarios;
  const blueprintPlatform = surface?.deploy_platform ?? null;
  const cloudPlatform =
    blueprintPlatform !== null && !LOCAL_ENGINES.includes(blueprintPlatform);
  // Pre-select the blueprint's engine when it's a local one.
  const pickedEngine =
    engine ?? (blueprintPlatform !== null && !cloudPlatform ? blueprintPlatform : "docker");

  return (
    <div className="actions-pane">
      <div className="actions-toolbar">
        <button
          type="button"
          className="action-button"
          disabled={running || scenarios.length === 0}
          title={scenarios.length === 0 ? "No harness scenarios defined" : "Run harness scenarios"}
          onClick={() => setArmed(armed === "test" ? null : "test")}
        >
          Test
        </button>
        <button
          type="button"
          className="action-button"
          disabled={running || surface?.sandbox}
          title={
            surface?.sandbox
              ? "Sandboxed runs are not supported in the editor yet — use `abp run`"
              : "One-shot run with an input message"
          }
          onClick={() => setArmed(armed === "run" ? null : "run")}
        >
          Run…
        </button>
        <button
          type="button"
          className="action-button"
          disabled={running || !canGate}
          title={
            !canGate
              ? "No harness scenarios or eval suites to gate"
              : surface?.has_gate_baseline
                ? "Run harness + evals and compare against the baseline"
                : "No baseline yet — use Update baseline first"
          }
          onClick={() => start("gate")}
        >
          Gate
        </button>
        <button
          type="button"
          className="action-button"
          disabled={running || !canGate}
          title="Run everything and overwrite the gate baseline (only when all green)"
          onClick={() => {
            if (window.confirm("Overwrite the gate baseline with the current run?")) {
              start("gate", { update_baseline: true });
            }
          }}
        >
          Update baseline
        </button>
        <button
          type="button"
          className="action-button"
          disabled={running}
          title="Generate the project next to the blueprint"
          onClick={() => start("generate")}
        >
          Generate
        </button>
        <button
          type="button"
          className="action-button"
          disabled={running}
          title="Pre-generation diagnostics"
          onClick={() => start("doctor")}
        >
          Doctor
        </button>
        <button
          type="button"
          className="action-button"
          disabled={running}
          title={
            cloudPlatform
              ? `Blueprint targets ${blueprintPlatform} — cloud deploys stay in the CLI; ` +
                "this deploys to a local container instead"
              : "Build and run the agent as a local container"
          }
          onClick={() => setArmed(armed === "deploy" ? null : "deploy")}
        >
          Deploy…
        </button>
        {running && (
          <button
            type="button"
            className="action-button action-cancel"
            onClick={() => void cancelTask()}
          >
            Cancel
          </button>
        )}
      </div>

      {armed === "test" && (
        <form
          className="action-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (picked.length === 0) return;
            // Sending no filter runs everything — keep the params minimal.
            start("test", picked.length === scenarios.length ? {} : { scenarios: picked });
          }}
        >
          <div className="scenario-picker">
            {scenarios.map((id) => (
              <label key={id} className="scenario-option">
                <input
                  type="checkbox"
                  checked={picked.includes(id)}
                  onChange={(e) =>
                    setPickedScenarios(
                      e.target.checked
                        ? [...picked, id]
                        : picked.filter((other) => other !== id),
                    )
                  }
                />
                {id}
              </label>
            ))}
          </div>
          <button type="submit" className="action-button" disabled={picked.length === 0}>
            Run {picked.length} scenario{picked.length === 1 ? "" : "s"}
          </button>
        </form>
      )}

      {armed === "run" && (
        <form
          className="action-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (runInput.trim()) start("run", { input: runInput });
          }}
        >
          <input
            className="run-input"
            type="text"
            value={runInput}
            placeholder="Input message…"
            onChange={(e) => setRunInput(e.target.value)}
            autoFocus
          />
          <button type="submit" className="action-button" disabled={!runInput.trim()}>
            Run
          </button>
          <p className="run-note muted">
            One-shot: every Run starts a fresh session (in-memory checkpointer, no
            history). A persistent chat session is coming in a later phase.
          </p>
        </form>
      )}

      {armed === "deploy" && (
        <form
          className="action-form"
          onSubmit={(e) => {
            e.preventDefault();
            // Deploy is outward-facing — always behind an explicit confirm.
            if (
              window.confirm(
                `Build the agent image and start it as a local ${pickedEngine} container?`,
              )
            ) {
              start("deploy", { engine: pickedEngine });
            }
          }}
        >
          {cloudPlatform && (
            <p className="deploy-note muted">
              This blueprint targets <code>{blueprintPlatform}</code> — cloud deploys stay in
              the CLI (<code>abp deploy</code>). The editor deploys a local container only.
            </p>
          )}
          <div className="engine-picker">
            {LOCAL_ENGINES.map((id) => (
              <label key={id} className="scenario-option">
                <input
                  type="radio"
                  name="deploy-engine"
                  checked={pickedEngine === id}
                  onChange={() => setEngine(id)}
                />
                {id}
              </label>
            ))}
          </div>
          <button type="submit" className="action-button">
            Deploy with {pickedEngine}
          </button>
        </form>
      )}

      {startError && <p className="action-start-error">{startError}</p>}

      {task && <TaskView task={task} />}
      {!task && !armed && (
        <p className="actions-empty muted">Run an action — progress and results appear here.</p>
      )}
    </div>
  );
}

function TaskView({ task }: { task: TaskRecord }) {
  return (
    <div className="task-view">
      <div className="task-head">
        <span className="task-action">{ACTION_LABELS[task.action] ?? task.action}</span>
        <span className={`task-status task-status-${task.status}`}>
          {task.status === "running" ? "running…" : task.status}
        </span>
      </div>
      {task.progress.length > 0 && (
        <ul className="task-progress">
          {task.progress.map((event, index) => (
            <ProgressLine key={index} event={event} />
          ))}
        </ul>
      )}
      {task.error && <pre className="task-error">{task.error}</pre>}
      {task.status !== "running" && task.result !== null && <ResultView task={task} />}
    </div>
  );
}

function ProgressLine({ event }: { event: TaskProgressEvent }) {
  if (event.kind === "deploy_cmd") {
    return (
      <li className="progress-line">
        <code>$ {event.cmd as string}</code>
      </li>
    );
  }
  if (event.kind === "secrets_missing") {
    return (
      <li className="progress-line progress-warn">
        ⚠ secrets not found in environment: {(event.names as string[]).join(", ")}
      </li>
    );
  }
  const name = (event.scenario ?? event.suite ?? "") as string;
  const entity = event.kind.startsWith("suite") ? "eval suite" : "scenario";
  if (event.kind.endsWith("_started")) {
    return (
      <li className="progress-line">
        ▸ {entity} <code>{name}</code> running…
      </li>
    );
  }
  const passed = event.passed === true;
  return (
    <li className={`progress-line ${passed ? "progress-pass" : "progress-fail"}`}>
      {passed ? "✓" : "✗"} {entity} <code>{name}</code>
      {typeof event.score === "number" && ` — score ${(event.score as number).toFixed(3)}`}
    </li>
  );
}

function ResultView({ task }: { task: TaskRecord }) {
  const result = task.result as Record<string, unknown>;
  switch (task.action) {
    case "doctor": {
      const findings = result.findings as Array<Record<string, string>>;
      if (findings.length === 0) {
        return <p className="task-summary">No doctor findings. ✨</p>;
      }
      return (
        <div className="task-result">
          {findings.map((finding, index) => (
            <div key={index} className={`issue issue-${finding.severity}`}>
              <div className="issue-head">
                <span className="issue-code">{finding.code}</span>
                {finding.location && (
                  <span className="issue-location">{finding.location}</span>
                )}
              </div>
              <div className="issue-message">{finding.message}</div>
            </div>
          ))}
        </div>
      );
    }
    case "generate": {
      const files = result.files as string[];
      return (
        <div className="task-result">
          <p className="task-summary">
            Generated {files.length} files into <code>{result.output_dir as string}</code>
          </p>
          <ul className="file-list">
            {files.map((file) => (
              <li key={file}>
                <code>{file}</code>
              </li>
            ))}
          </ul>
        </div>
      );
    }
    case "test": {
      return (
        <div className="task-result">
          <p className="task-summary">
            {result.passed_count as number} passed, {result.failed_count as number} failed
          </p>
          <FailureList
            items={(result.scenarios as Array<Record<string, unknown>>)
              .filter((s) => !s.passed)
              .flatMap((s) =>
                (s.failures as string[]).map((failure) => `${s.scenario as string}: ${failure}`),
              )}
          />
        </div>
      );
    }
    case "run": {
      const finalState = result.final_state as Record<string, unknown> | null;
      return (
        <div className="task-result">
          <p className="task-summary">exit code {result.returncode as number}</p>
          {(result.stdout as string) && (
            <pre className="task-output">{result.stdout as string}</pre>
          )}
          {(result.stderr as string) && (
            <pre className="task-output task-output-stderr">{result.stderr as string}</pre>
          )}
          <StateInspector state={finalState} />
        </div>
      );
    }
    case "gate": {
      if (result.baseline_written) {
        return (
          <p className="task-summary">
            Baseline written to <code>{result.baseline_written as string}</code>
          </p>
        );
      }
      if (result.message) {
        return <p className="task-summary">{result.message as string}</p>;
      }
      return (
        <div className="task-result">
          <p className="task-summary">
            {result.passed ? "Gate PASSED" : "Gate FAILED"}
            {!result.all_green && " — run is not all green"}
          </p>
          <FailureList items={(result.regressions as string[]) ?? []} />
          {((result.improvements as string[]) ?? []).map((line) => (
            <p key={line} className="gate-improved">
              ↑ {line}
            </p>
          ))}
          {((result.new_entries as string[]) ?? []).map((line) => (
            <p key={line} className="gate-new">
              + {line}
            </p>
          ))}
        </div>
      );
    }
    case "deploy": {
      const missing = (result.missing_secrets as string[]) ?? [];
      return (
        <div className="task-result">
          <p className="task-summary">
            {task.status === "passed"
              ? `Container started (${result.engine as string})`
              : (result.message as string)}
          </p>
          {task.status === "passed" && (result.url as string | null) && (
            <p className="deploy-url">
              <a href={result.url as string} target="_blank" rel="noreferrer">
                {result.url as string}
              </a>{" "}
              — <code>POST {result.url as string}/invoke</code>
            </p>
          )}
          {missing.length > 0 && (
            <p className="deploy-note muted">
              Secrets not injected (not in the editor's environment): {missing.join(", ")}
            </p>
          )}
        </div>
      );
    }
    default:
      return null;
  }
}

/**
 * Post-run state inspector (E5.3). Renders the trace manifest's `final_state`
 * — scalars verbatim, message lists as a count (the recorder collapses them to
 * `{__messages__: N}`), everything else as compact JSON. Per-node state values
 * are not shown: the trace carries only hashes per node, so a per-node value
 * view needs opt-in content capture (E5.4, a core change).
 */
function StateInspector({ state }: { state: Record<string, unknown> | null }) {
  if (!state || Object.keys(state).length === 0) return null;
  return (
    <div className="state-inspector">
      <p className="state-inspector-head">Final state</p>
      <dl className="state-fields">
        {Object.entries(state).map(([key, value]) => (
          <div key={key} className="state-field">
            <dt>{key}</dt>
            <dd>{formatStateValue(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function formatStateValue(value: unknown): string {
  if (value !== null && typeof value === "object" && "__messages__" in value) {
    const count = (value as { __messages__: number }).__messages__;
    return `${count} message${count === 1 ? "" : "s"}`;
  }
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function FailureList({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="failure-list">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}
