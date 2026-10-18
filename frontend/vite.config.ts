/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") },
      "/api/ws": { target: "ws://localhost:8000", ws: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts?(x)"] },
});
