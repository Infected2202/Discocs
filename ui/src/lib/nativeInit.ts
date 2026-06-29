import { isNative, initBackendUrl } from "./runtimeConfig"

export async function initNative(): Promise<void> {
  if (!isNative()) return

  await Promise.all([
    initBackendUrl(),
    setupStatusBar(),
  ])
}

async function setupStatusBar(): Promise<void> {
  try {
    const { StatusBar, Style } = await import("@capacitor/status-bar")
    await StatusBar.setStyle({ style: Style.Dark })
    await StatusBar.setBackgroundColor({ color: "#0b0d0f" })
    await StatusBar.setOverlaysWebView({ overlay: false })
  } catch {
    // StatusBar plugin not available (web or unsupported platform)
  }
}
