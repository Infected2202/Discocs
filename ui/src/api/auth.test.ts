import { beforeEach, describe, expect, it, vi } from "vitest"

const apiFetch = vi.fn()
const resetUserSessionState = vi.fn()
const stopActivePresence = vi.fn()

vi.mock("./client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}))

vi.mock("@/store/userSessionState", () => ({
  resetUserSessionState: () => resetUserSessionState(),
}))

vi.mock("@/lib/presence", () => ({
  stopActivePresence: () => stopActivePresence(),
}))

import { logout } from "./auth"

describe("logout", () => {
  beforeEach(() => {
    apiFetch.mockReset()
    resetUserSessionState.mockReset()
    stopActivePresence.mockReset()
    stopActivePresence.mockResolvedValue(undefined)
  })

  it("reports presence stopped before the session cookie is revoked", async () => {
    const order: string[] = []
    stopActivePresence.mockImplementation(async () => { order.push("presence") })
    apiFetch.mockImplementation(async () => { order.push("logout") })

    await logout()

    expect(order).toEqual(["presence", "logout"])
  })

  it("clears client user state after a successful logout", async () => {
    apiFetch.mockResolvedValue(undefined)

    await logout()

    expect(apiFetch).toHaveBeenCalledWith("/api/v1/auth/logout", { method: "POST" })
    expect(resetUserSessionState).toHaveBeenCalledTimes(1)
  })

  it("still clears client user state when the logout request fails", async () => {
    apiFetch.mockRejectedValue(new TypeError("offline"))

    await expect(logout()).rejects.toThrow("offline")

    expect(resetUserSessionState).toHaveBeenCalledTimes(1)
  })
})
