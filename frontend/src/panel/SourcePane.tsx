import { useEffect, useRef } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { LintFinding } from "../api";
import { monaco } from "./monacoSetup";

type IStandaloneCodeEditor = Parameters<OnMount>[0];

export interface SourcePaneHandle {
  revealLine: (line: number) => void;
}

interface Props {
  yaml: string;
  lint: LintFinding[];
  onReady?: (handle: SourcePaneHandle) => void;
}

/** Read-only Monaco YAML view with lint findings as inline markers. */
export function SourcePane({ yaml, lint, onReady }: Props) {
  const editorRef = useRef<IStandaloneCodeEditor | null>(null);

  const applyMarkers = (editor: IStandaloneCodeEditor) => {
    const model = editor.getModel();
    if (!model) return;
    const markers = lint
      .filter((finding) => finding.line !== null)
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
  };

  const handleMount: OnMount = (editor) => {
    editorRef.current = editor;
    applyMarkers(editor);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lint, yaml]);

  return (
    <Editor
      language="yaml"
      value={yaml}
      onMount={handleMount}
      options={{
        readOnly: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        fontSize: 12,
        renderValidationDecorations: "on",
      }}
    />
  );
}
