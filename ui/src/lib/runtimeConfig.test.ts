import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

describe("runtimeConfig", () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("returns false by default and true when Capacitor reports a native platform", async () => {
    const runtimeConfig = await import("./runtimeConfig")

    expect(runtimeConfig.isNative()).toBe(false)

    vi.stubGlobal("Capacitor", { isNativePlatform: () => true })

    expect(runtimeConfig.isNative()).toBe(true)
  })

  it("uses trimmed localStorage values on the web", async () => {
    const runtimeConfig = await import("./runtimeConfig")

    await runtimeConfig.setBackendUrl("  http://localhost:9999  ")

    expect(localStorage.getItem("discocs_backend_url")).toBe("http://localhost:9999")
    expect(await runtimeConfig.getBackendUrl()).toBe("http://localhost:9999")
    expect(runtimeConfig.getBackendUrlSync()).toBe("")
  })

  it("removes the stored value when setting an empty URL", async () => {
    const runtimeConfig = await import("./runtimeConfig")

    await runtimeConfig.setBackendUrl("http://localhost:9999")
    await runtimeConfig.setBackendUrl("   ")

    expect(localStorage.getItem("discocs_backend_url")).toBeNull()
    expect(await runtimeConfig.initBackendUrl()).toBe("")
  })
})
