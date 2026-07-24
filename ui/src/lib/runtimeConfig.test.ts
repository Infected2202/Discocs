import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

describe("runtimeConfig", () => {
  beforeEach(() => {
    vi.resetModules()
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
})
