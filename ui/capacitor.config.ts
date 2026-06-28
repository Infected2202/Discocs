import type { CapacitorConfig } from "@capacitor/cli"

const config: CapacitorConfig = {
  appId: "com.discocs.app",
  appName: "discocs",
  webDir: "dist",
  backgroundColor: "#0b0d0f",
  plugins: {
    StatusBar: {
      style: "DARK",
      backgroundColor: "#0b0d0f",
      overlaysWebView: false,
    },
  },
}

export default config
