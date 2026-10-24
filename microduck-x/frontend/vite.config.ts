import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the control service, which stays on 127.0.0.1.
const backend = process.env.MDX_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace("http", "ws"), ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1200 },
});
