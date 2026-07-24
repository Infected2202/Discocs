import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const notifyAppReady = vi.fn()
const current = vi.fn()
const download = vi.fn()
const set = vi.fn()
const startForegroundServiceMock = vi.fn()

vi.mock("@capgo/capacitor-updater", () => ({
  CapacitorUpdater: {
    notifyAppReady: (...args: unknown[]) => notifyAppReady(...args),
    current: (...args: unknown[]) => current(...args),
    download: (...args: unknown[]) => download(...args),
    set: (...args: unknown[]) => set(...args),
  },
}))

vi.mock("@capawesome-team/capacitor-android-foreground-service", () => ({
  ForegroundService: {
    startForegroundService: (...args: unknown[]) => startForegroundServiceMock(...args),
  },
}))

vi.mock("@capacitor/status-bar", () => ({
  StatusBar: {
    setStyle: vi.fn(),
    setBackgroundColor: vi.fn(),
    setOverlaysWebView: vi.fn(),
  },
  Style: { Dark: "DARK", Light: "LIGHT", Default: "DEFAULT" },
}))

describe("nativeInit", () => {
  beforeEach(() => {
    vi.resetModules()
    notifyAppReady.mockReset()
    current.mockReset()
    download.mockReset()
    set.mockReset()
    startForegroundServiceMock.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("no-ops on web", async () => {
    vi.stubGlobal("fetch", vi.fn())
    const { initNative } = await import("./nativeInit")

    await initNative()

    expect(notifyAppReady).not.toHaveBeenCalled()
    expect(startForegroundServiceMock).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it("downloads and applies a new bundle when the manifest version differs", async () => {
    vi.stubGlobal("Capacitor", { isNativePlatform: () => true })
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: () => Promise.resolve({ version: "abc123", url: "/downloads/discocs-web-abc123.zip" }),
      })
    )
    current.mockResolvedValue({ bundle: { id: "builtin", version: "old" }, native: "1.0.0" })
    download.mockResolvedValue({ id: "new-bundle-id", version: "abc123" })

    const { initNative } = await import("./nativeInit")
    await initNative()

    expect(notifyAppReady).toHaveBeenCalled()
    expect(download).toHaveBeenCalledWith({ url: "/downloads/discocs-web-abc123.zip", version: "abc123" })
    expect(set).toHaveBeenCalledWith({ id: "new-bundle-id" })
  })

  it("does not download when already on the manifest version", async () => {
    vi.stubGlobal("Capacitor", { isNativePlatform: () => true })
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: () => Promise.resolve({ version: "abc123", url: "/downloads/discocs-web-abc123.zip" }),
      })
    )
    current.mockResolvedValue({ bundle: { id: "current-id", version: "abc123" }, native: "1.0.0" })

    const { initNative } = await import("./nativeInit")
    await initNative()

    expect(download).not.toHaveBeenCalled()
    expect(set).not.toHaveBeenCalled()
  })

  it("swallows errors when the update check fails (offline/first launch)", async () => {
    vi.stubGlobal("Capacitor", { isNativePlatform: () => true })
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network error")))

    const { initNative } = await import("./nativeInit")

    await expect(initNative()).resolves.toBeUndefined()
    expect(download).not.toHaveBeenCalled()
  })
})
