import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { privframeMockApi } from "./mock/plugin";

// PRIVFRAME_API=mock (default) serves the API from the in-memory mock engine in ./mock.
// PRIVFRAME_API=proxy forwards /api to a real backend at PRIVFRAME_API_URL (M4).
const useProxy = process.env.PRIVFRAME_API === "proxy";

export default defineConfig({
  plugins: [react(), ...(useProxy ? [] : [privframeMockApi()])],
  server: {
    port: 5180,
    strictPort: true,
    proxy: useProxy
      ? {
          "/api": {
            target: process.env.PRIVFRAME_API_URL ?? "http://127.0.0.1:8000",
            changeOrigin: true,
          },
        }
      : undefined,
  },
});
