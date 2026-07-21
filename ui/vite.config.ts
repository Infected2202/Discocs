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
    // Дефолтные 5s изредка ловят не баг, а scheduling jitter: на полном
    // прогоне (16 воркеров) узкое место — jsdom-окружение (~490s суммарно),
    // и тяжёлые компонентные тесты (монтаж Radix Dialog, большой DJ-микшер)
    // под контеншеном раздуваются с <100ms до 5s+. Код при этом корректен —
    // изолированно тесты зелёные, а под нагрузкой они *досчитывают* за 4–5s,
    // не виснут (не бесконечный цикл). Худшее наблюдалось ~6.7s, 15s даёт 2.3×
    // запаса. Это перекалибровка порога под реальную стоимость, не маскировка.
    testTimeout: 15000,
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
