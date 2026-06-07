// Bundle Monaco locally instead of @monaco-editor/react's default CDN loader:
// the editor must work fully offline (localhost tool, no external requests).
// The slim editor.api entry keeps every language except YAML out of the
// bundle (the full "monaco-editor" entry weighs ~5 MB). YAML is registered
// statically because the stock yaml.contribution's lazy `import()` breaks
// vite's build-time import analysis.
import * as monaco from "monaco-editor/esm/vs/editor/editor.api";
// @ts-expect-error - monaco ships no .d.ts for the raw language module
import { conf as yamlConf, language as yamlLanguage } from "monaco-editor/esm/vs/basic-languages/yaml/yaml";
import { loader } from "@monaco-editor/react";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

monaco.languages.register({ id: "yaml", extensions: [".yml", ".yaml"], aliases: ["YAML"] });
monaco.languages.setMonarchTokensProvider("yaml", yamlLanguage);
monaco.languages.setLanguageConfiguration("yaml", yamlConf);

(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};

loader.config({ monaco });

export { monaco };
