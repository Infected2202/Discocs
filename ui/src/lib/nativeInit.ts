import { StatusBar, Style } from "@capacitor/status-bar"
import { isNative } from "./runtimeConfig"
import { initBackendUrl } from "./runtimeConfig"

export async function initNative(): Promise<void> {
  if (!isNative()) return

  await Promise.all([
    initBackendUrl(),
    setupStatusBar(),
  ])
}

async function setupStatusBar(): Promise<void> {
  try {
    await StatusBar.setStyle({ style: Style.Dark })
    await StatusBar.setBackgroundColor({ color: "#0b0d0f" })
    await StatusBar.setOverlaysWebView({ overlay: false })
  } catch {
    // StatusBar plugin may not be available on all platforms
  }
}
