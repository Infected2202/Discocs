import type { CapacitorConfig } from "@capacitor/cli"

const config: CapacitorConfig = {
  appId: "com.discocs.app",
  appName: "discocs",
  webDir: "dist",
  backgroundColor: "#0b0d0f",
  server: {
    hostname: new URL(process.env.DISCOCS_PUBLIC_URL ?? "https://localhost").hostname,
    androidScheme: "https",
  },
  plugins: {
    StatusBar: {
      style: "DARK",
      backgroundColor: "#0b0d0f",
      overlaysWebView: false,
    },
    CapacitorUpdater: {
      autoUpdate: false,
    },
    // The WebView's own local asset server (which serves the bundled dist/
    // under the hostname-matched origin, see server.hostname above) owns the
    // whole path space for that origin and does not fall through to the real
    // network for unmatched paths like /api/*. Plain fetch()/XMLHttpRequest
    // therefore never reach the real backend at all. CapacitorHttp/
    // CapacitorCookies patch window.fetch/XMLHttpRequest/document.cookie to
    // go through native networking instead, bypassing the local asset loader
    // entirely — required for apiFetch() (ui/src/api/client.ts) to actually
    // reach the production API. Bundled with @capacitor/core, no extra deps.
    CapacitorHttp: {
      enabled: true,
    },
    CapacitorCookies: {
      enabled: true,
    },
  },
}

export default config
