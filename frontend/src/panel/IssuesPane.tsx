import type { LintFinding } from "../api";

interface Props {
  error: string | null;
  lint: LintFinding[];
  onFindingClick: (finding: LintFinding) => void;
}

/** Validation error + lint findings, clickable through to the source line. */
export function IssuesPane({ error, lint, onFindingClick }: Props) {
  if (error === null && lint.length === 0) {
    return <p className="issues-empty">No validation errors or lint findings. ✨</p>;
  }
  return (
    <div className="issues-list">
      {error !== null && (
        <div className="issue issue-error">
          <div className="issue-head">
            <span className="issue-code">validation</span>
          </div>
          <pre className="issue-message issue-message-pre">{error}</pre>
        </div>
      )}
      {lint.map((finding, index) => (
        <button
          key={`${finding.code}-${index}`}
          type="button"
          className={`issue issue-${finding.severity} ${finding.line !== null ? "issue-clickable" : ""}`}
          onClick={() => onFindingClick(finding)}
          disabled={finding.line === null}
        >
          <div className="issue-head">
            <span className="issue-code">{finding.code}</span>
            {finding.location && <span className="issue-location">{finding.location}</span>}
            {finding.line !== null && <span className="issue-line">L{finding.line}</span>}
          </div>
          <div className="issue-message">{finding.message}</div>
        </button>
      ))}
    </div>
  );
}
