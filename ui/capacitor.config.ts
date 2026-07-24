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
  },
}

export default config
