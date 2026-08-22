import { describe, it, expect } from "vitest"
import { ApiError } from "@/api/client"
import { isNetworkError } from "./apiErrorKind"

describe("isNetworkError", () => {
  it("is true for a fetch failure that never reached the server", () => {
    expect(isNetworkError(new TypeError("Failed to fetch"))).toBe(true)
    expect(isNetworkError(new Error("network error"))).toBe(true)
  })

  it("is false for a real HTTP error returned by the server", () => {
    expect(isNetworkError(new ApiError(404, "not_found", "nope"))).toBe(false)
    expect(isNetworkError(new ApiError(401, "unauthorized", "nope"))).toBe(false)
    expect(isNetworkError(new ApiError(500, "server_error", "boom"))).toBe(false)
  })

  it("is false when there is no error", () => {
    expect(isNetworkError(null)).toBe(false)
    expect(isNetworkError(undefined)).toBe(false)
  })
})
