import { describe, it, expect, vi, afterEach } from "vitest"
import { ApiError } from "@/api/client"
import { isUnauthorized, redirectToLogin, LOGIN_PATH } from "./authRedirect"

describe("isUnauthorized", () => {
  it("is true only for an ApiError with status 401", () => {
    expect(isUnauthorized(new ApiError(401, "unauthorized", "nope"))).toBe(true)
    expect(isUnauthorized(new ApiError(403, "forbidden", "nope"))).toBe(false)
    expect(isUnauthorized(new ApiError(500, "server_error", "boom"))).toBe(false)
    expect(isUnauthorized(new Error("plain"))).toBe(false)
    expect(isUnauthorized(null)).toBe(false)
  })
})

describe("redirectToLogin", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("navigates to the login page when elsewhere", () => {
    const assign = vi.fn()
    // location.assign is non-configurable in jsdom, so replace the whole object.
    vi.stubGlobal("location", { pathname: "/dashboard", assign })
    redirectToLogin()
    expect(assign).toHaveBeenCalledWith(LOGIN_PATH)
  })

  it("does not navigate when already on the login page", () => {
    const assign = vi.fn()
    vi.stubGlobal("location", { pathname: LOGIN_PATH, assign })
    redirectToLogin()
    expect(assign).not.toHaveBeenCalled()
  })
})
