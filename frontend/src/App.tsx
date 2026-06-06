import { useEffect, useState, type CSSProperties } from "react";

interface BlueprintInfo {
  path: string;
  name: string | null;
  valid: boolean;
  error: string | null;
  yaml: string;
}

const styles: Record<string, CSSProperties> = {
  page: {
    fontFamily: "system-ui, sans-serif",
    maxWidth: 880,
    margin: "3rem auto",
    padding: "0 1.5rem",
    color: "#1a1a1a",
  },
  badge: {
    display: "inline-block",
    padding: "0.15rem 0.6rem",
    borderRadius: 999,
    fontSize: "0.8rem",
    fontWeight: 600,
    marginLeft: "0.75rem",
    verticalAlign: "middle",
  },
  source: {
    background: "#f6f6f6",
    border: "1px solid #e2e2e2",
    borderRadius: 8,
    padding: "1rem",
    fontSize: "0.85rem",
    overflowX: "auto",
    whiteSpace: "pre",
  },
  muted: { color: "#777" },
};

export default function App() {
  const [info, setInfo] = useState<BlueprintInfo | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/blueprint")
      .then((r) => {
        if (!r.ok) throw new Error(`API responded ${r.status}`);
        return r.json();
      })
      .then(setInfo)
      .catch((e) => setFetchError(String(e)));
  }, []);

  if (fetchError) {
    return (
      <main style={styles.page}>
        <h1>ABP Editor</h1>
        <p style={{ color: "#b00020" }}>Could not reach the editor API: {fetchError}</p>
      </main>
    );
  }
  if (!info) {
    return (
      <main style={styles.page}>
        <p style={styles.muted}>Loading blueprint…</p>
      </main>
    );
  }

  return (
    <main style={styles.page}>
      <h1>
        {info.name ?? "(unnamed blueprint)"}
        <span
          style={{
            ...styles.badge,
            background: info.valid ? "#e3f6e8" : "#fdeaea",
            color: info.valid ? "#1b7f3b" : "#b00020",
          }}
        >
          {info.valid ? "valid" : "invalid"}
        </span>
      </h1>
      <p style={styles.muted}>{info.path}</p>
      {info.error && <p style={{ color: "#b00020" }}>{info.error}</p>}
      <p style={styles.muted}>
        The visual canvas arrives in a later phase — this page proves the packaging spine:
        a React app served from inside the <code>abp</code> wheel, talking to the local API.
      </p>
      <h2>Source</h2>
      <pre style={styles.source}>{info.yaml}</pre>
    </main>
  );
}
