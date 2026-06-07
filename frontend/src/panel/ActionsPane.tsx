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
};

/** Action buttons + live progress + result view for background tasks (E3a). */
export function ActionsPane({ surface, valid, task, onTask }: Props) {
  // Which action's parameter form is open (test → scenario picker, run → input).
  const [armed, setArmed] = useState<"test" | "run" | null>(null);
  const [pickedScenarios, setPickedScenarios] = useState<string[] | null>(null);
  const [runInput, setRunInput] = useState("");
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
      return (
        <div className="task-result">
          <p className="task-summary">exit code {result.returncode as number}</p>
          {(result.stdout as string) && (
            <pre className="task-output">{result.stdout as string}</pre>
          )}
          {(result.stderr as string) && (
            <pre className="task-output task-output-stderr">{result.stderr as string}</pre>
          )}
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
    default:
      return null;
  }
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
