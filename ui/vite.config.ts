/// <reference types="vitest/config" />
import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const BACKEND = "http://localhost:8711"

const proxyPaths = ["/api", "/health", "/admin"]

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
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      reportsDirectory: "coverage",
      include: ["src/**"],
    },
    // junit — тот же формат, что pytest --junitxml для backend/бота, чтобы
    // Jenkins мог показать все три через один встроенный шаг `junit`.
    reporters: ["default", "junit"],
    outputFile: { junit: "./junit-ui.xml" },
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
