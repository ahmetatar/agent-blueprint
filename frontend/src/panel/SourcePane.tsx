import { useCallback, useEffect, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import { SaveRejectedError, saveYaml, type BlueprintInfo, type LintFinding } from "../api";
import { monaco } from "./monacoSetup";

type IStandaloneCodeEditor = Parameters<OnMount>[0];

export interface SourcePaneHandle {
  revealLine: (line: number) => void;
}

interface Props {
  /** The server's current file content (refreshed on save and live reload). */
  yaml: string;
  lint: LintFinding[];
  onSaved: (info: BlueprintInfo) => void;
  onDirtyChange: (dirty: boolean) => void;
  onReady?: (handle: SourcePaneHandle) => void;
}

/**
 * Editable Monaco YAML pane with lint markers.
 *
 * The editor content is deliberately uncontrolled after mount: while the pane
 * is clean it tracks the server text (live reload), but unsaved local edits
 * are never clobbered — an external file change instead raises a conflict
 * banner (load the file version, or keep editing and Save overwrites:
 * last-writer-wins on whole-file saves).
 */
export function SourcePane({ yaml, lint, onSaved, onDirtyChange, onReady }: Props) {
  const editorRef = useRef<IStandaloneCodeEditor | null>(null);
  // The server text the editor content was last synced to or saved as.
  const baselineRef = useRef(yaml);
  const dirtyRef = useRef(false);
  const [dirty, setDirtyState] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [externalChange, setExternalChange] = useState(false);

  const setDirty = useCallback(
    (next: boolean) => {
      dirtyRef.current = next;
      setDirtyState(next);
      onDirtyChange(next);
    },
    [onDirtyChange],
  );

  const applyMarkers = useCallback(
    (editor: IStandaloneCodeEditor) => {
      const model = editor.getModel();
      if (!model) return;
      const markers = lint
        .filter((finding) => finding.line !== null && (finding.line as number) <= model.getLineCount())
        .map((finding) => {
          const line = finding.line as number;
          return {
            severity:
              finding.severity === "error"
                ? monaco.MarkerSeverity.Error
                : monaco.MarkerSeverity.Warning,
            message: `${finding.code}: ${finding.message}`,
            startLineNumber: line,
            startColumn: finding.col ?? 1,
            endLineNumber: line,
            endColumn: model.getLineMaxColumn(line),
          };
        });
      monaco.editor.setModelMarkers(model, "abp-lint", markers);
    },
    [lint],
  );

  const save = useCallback(async () => {
    const editor = editorRef.current;
    if (editor === null || !dirtyRef.current) return;
    const text = editor.getValue();
    setSaving(true);
    try {
      const info = await saveYaml(text);
      baselineRef.current = info.yaml;
      setDirty(false);
      setSaveError(null);
      setExternalChange(false);
      onSaved(info);
    } catch (e) {
      setSaveError(e instanceof SaveRejectedError ? e.message : `Save failed: ${String(e)}`);
    } finally {
      setSaving(false);
    }
  }, [onSaved, setDirty]);
  const saveRef = useRef(save);
  saveRef.current = save;

  const loadFileVersion = useCallback(() => {
    const editor = editorRef.current;
    if (editor === null) return;
    editor.setValue(baselineRef.current);
    setDirty(false);
    setSaveError(null);
    setExternalChange(false);
  }, [setDirty]);

  // Track the server text: while clean, follow it (live reload); while dirty,
  // flag the conflict instead of touching the user's edits.
  useEffect(() => {
    const previousBaseline = baselineRef.current;
    baselineRef.current = yaml;
    const editor = editorRef.current;
    if (editor === null) return;
    if (!dirtyRef.current) {
      if (editor.getValue() !== yaml) editor.setValue(yaml); // identical → keep cursor
      setExternalChange(false);
    } else if (yaml !== previousBaseline) {
      setExternalChange(true);
    }
  }, [yaml]);

  const handleMount: OnMount = (editor) => {
    editorRef.current = editor;
    applyMarkers(editor);
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      void saveRef.current();
    });
    onReady?.({
      revealLine: (line) => {
        editor.revealLineInCenter(line);
        editor.setPosition({ lineNumber: line, column: 1 });
        editor.focus();
      },
    });
  };

  useEffect(() => {
    if (editorRef.current) applyMarkers(editorRef.current);
  }, [applyMarkers, yaml]);

  return (
    <div className="source-pane">
      {externalChange && dirty && (
        <div className="source-banner source-banner-conflict">
          <span>File changed on disk while you have unsaved edits.</span>
          <button type="button" onClick={loadFileVersion}>
            Load file version
          </button>
          <button type="button" onClick={() => setExternalChange(false)}>
            Keep mine
          </button>
        </div>
      )}
      {saveError !== null && (
        <div className="source-banner source-banner-error">
          <span>{saveError}</span>
          <button type="button" onClick={() => setSaveError(null)}>
            Dismiss
          </button>
        </div>
      )}
      <div className="source-editor">
        <Editor
          language="yaml"
          defaultValue={yaml}
          onMount={handleMount}
          onChange={(text) => setDirty((text ?? "") !== baselineRef.current)}
          options={{
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 12,
            renderValidationDecorations: "on",
          }}
        />
      </div>
      <footer className="source-footer">
        <span className="muted">{dirty ? "Unsaved changes" : "In sync with file"}</span>
        <button
          type="button"
          className="save-button"
          disabled={!dirty || saving}
          onClick={() => void save()}
        >
          {saving ? "Saving…" : "Save"}
          <kbd>⌘S</kbd>
        </button>
      </footer>
    </div>
  );
}
