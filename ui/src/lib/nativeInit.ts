import { isNative } from "./runtimeConfig"

export async function initNative(): Promise<void> {
  if (!isNative()) return

  await Promise.all([setupStatusBar(), startForegroundService(), checkForUpdate()])
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

async function startForegroundService(): Promise<void> {
  try {
    const { ForegroundService } = await import("@capawesome-team/capacitor-android-foreground-service")
    await ForegroundService.startForegroundService({
      id: 1,
      title: "discocs",
      body: "Playing in the background",
      smallIcon: "ic_stat_discocs",
      // No `serviceType` here: this plugin's ServiceType enum only exposes
      // Location/Microphone, not media playback. The mediaPlayback type is
      // instead declared on the <service> tag in AndroidManifest.xml, which
      // the native implementation falls back to when no runtime type is passed.
    })
  } catch {
    // Foreground service plugin not available (web or unsupported platform)
  }
}

async function checkForUpdate(): Promise<void> {
  try {
    const { CapacitorUpdater } = await import("@capgo/capacitor-updater")
    await CapacitorUpdater.notifyAppReady()

    const response = await fetch("/downloads/update-manifest.json")
    const manifest = (await response.json()) as { version: string; url: string }

    const { bundle } = await CapacitorUpdater.current()
    if (bundle.version === manifest.version) return

    const downloaded = await CapacitorUpdater.download({ url: manifest.url, version: manifest.version })
    await CapacitorUpdater.set({ id: downloaded.id })
  } catch {
    // Offline / first launch / plugin unavailable — keep the bundled version.
  }
}
