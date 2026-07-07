import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
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
  let assign: ReturnType<typeof vi.fn>

  beforeEach(() => {
    assign = vi.fn()
    vi.spyOn(globalThis.location, "assign").mockImplementation(assign)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("navigates to the login page when elsewhere", () => {
    globalThis.history.pushState({}, "", "/dashboard")
    redirectToLogin()
    expect(assign).toHaveBeenCalledWith(LOGIN_PATH)
  })

  it("does not navigate when already on the login page", () => {
    globalThis.history.pushState({}, "", LOGIN_PATH)
    redirectToLogin()
    expect(assign).not.toHaveBeenCalled()
  })
})
