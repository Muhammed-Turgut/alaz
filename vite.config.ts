import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  server: {
    // Python backend (venv, veri, önbellek) izlenmesin: inotify limitini (ENOSPC) aşıp dev sunucuyu bozuyordu.
    watch: { ignored: ["**/backend/**"] },
    // Python backend (backend/run.py) -> http://127.0.0.1:8000
    proxy: {
      "/api": { target: process.env.AETHER_API ?? "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
