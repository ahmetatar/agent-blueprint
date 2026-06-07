import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build output goes straight into the Python package so the hatch build hook
// (and editable-install contributors) only need to run `npm run build`.
export default defineConfig({
  plugins: [react()],
  worker: {
    // Monaco's editor worker is bundled as an ES module (the default iife
    // worker output chokes on code-split imports).
    format: "es",
  },
  build: {
    outDir: "../src/agent_blueprint/editor/static",
    emptyOutDir: true,
  },
  server: {
    // `abp editor --dev` serves the API on a fixed port; the Vite dev server
    // proxies API/WS calls so the browser stays same-origin (no CORS).
    proxy: {
      "/api": "http://127.0.0.1:8321",
      "/ws": { target: "ws://127.0.0.1:8321", ws: true },
    },
  },
});
