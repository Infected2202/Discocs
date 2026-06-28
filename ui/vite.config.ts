import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const BACKEND = "http://localhost:7752"

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
  server: {
    proxy: Object.fromEntries(
      proxyPaths.map((p) => [
        p,
        { target: BACKEND, changeOrigin: true },
      ])
    ),
  },
})
