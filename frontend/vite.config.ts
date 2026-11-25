/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Backend FastAPI server (Phase 1) runs on 127.0.0.1:8000 by default.
const BACKEND_TARGET = process.env.VITE_BACKEND_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // Fail loudly on a port conflict instead of silently drifting to a random
    // port: the backend CORS/WS origin allowlist only trusts the known dev
    // origins (5173/5180), so a drifted port would be rejected at the /api/ws
    // upgrade. The run recipe passes --port 5180 --strictPort explicitly; this
    // keeps a bare `npm run dev` honest too.
    strictPort: true,
    // Proxy API calls to the FastAPI backend so the browser never talks to NSE
    // directly and we avoid CORS friction during development. `ws: true` also
    // proxies the WebSocket upgrade for the Phase 4 real-time push (/api/ws).
    proxy: {
      "/api": {
        target: BACKEND_TARGET,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/tests/setup.ts"],
    css: false,
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
  },
});
