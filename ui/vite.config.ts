import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
/// <reference types="vitest" />

const BACKEND = "http://localhost:8711"

const proxyPaths = [
  "/api",
  "/tracks",
  "/artists",
  "/releases",
  "/mixes",
  "/settings",
  "/stats",
  "/jobs",
  "/feedback",
  "/navidrome",
  "/dashboard",
  "/workers",
  "/models",
  "/index",
  "/metrics",
  "/playback",
  "/likes",
  "/health",
]

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
  server: {
    proxy: Object.fromEntries(
      proxyPaths.map((p) => [
        p,
        {
          target: BACKEND,
          changeOrigin: true,
          // Only proxy API/XHR requests, not browser navigation (SPA routes)
          bypass: (req) => {
            const accept = req.headers.accept ?? ""
            if (accept.includes("text/html")) return "/index.html"
          },
        },
      ])
    ),
  },
})
